"""Massive (Polygon.io) REST API market data client.

Polls the Massive/Polygon snapshot endpoint at a configurable interval and
maps the response into the same PriceUpdate format used by the simulator.

Two poll rates are supported:
- Free tier  (~5 req/min): poll every 15 seconds (default)
- Paid tiers (higher rate): poll every 2 seconds

Environment variable MASSIVE_API_KEY selects this provider over the simulator.
"""

import asyncio
import logging
from typing import Optional

import httpx

from .interface import MarketDataProvider, PriceUpdate

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Polygon.io / Massive REST endpoints
# ---------------------------------------------------------------------------
MASSIVE_BASE_URL = "https://api.polygon.io"
SNAPSHOT_PATH = "/v2/snapshot/locale/us/markets/stocks/tickers"

# ---------------------------------------------------------------------------
# Poll intervals
# ---------------------------------------------------------------------------
POLL_INTERVAL_FREE = 15.0   # seconds — stays within the free-tier rate limit
POLL_INTERVAL_PAID = 2.0    # seconds — suitable for paid tiers


class MassiveAPIClient(MarketDataProvider):
    """
    Market data client backed by the Massive (Polygon.io) REST API.

    Usage::

        client = MassiveAPIClient(api_key="...", poll_interval=15.0)
        client.add_ticker("AAPL")
        await client.start()
        # ... later ...
        update = client.get_price("AAPL")
        await client.stop()
    """

    def __init__(
        self,
        api_key: str,
        poll_interval: float = POLL_INTERVAL_FREE,
        base_url: str = MASSIVE_BASE_URL,
    ) -> None:
        self._api_key = api_key
        self._poll_interval = poll_interval
        self._base_url = base_url.rstrip("/")
        self._tickers: list[str] = []
        self._price_updates: dict[str, PriceUpdate] = {}
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Open the HTTP session and launch the polling background task."""
        self._client = httpx.AsyncClient(timeout=10.0)
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the polling task and close the HTTP session."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def get_price(self, ticker: str) -> Optional[PriceUpdate]:
        return self._price_updates.get(ticker.upper())

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return dict(self._price_updates)

    def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper()
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper()
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self._price_updates.pop(ticker, None)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # ------------------------------------------------------------------
    # Internal: HTTP fetch
    # ------------------------------------------------------------------

    async def _fetch_prices(self, tickers: list[str]) -> dict[str, float]:
        """
        Call the Polygon snapshot endpoint for the given tickers.

        Returns a dict of {TICKER: price} for all tickers that returned a
        valid price.  Returns an empty dict on any error so the caller can
        simply retry on the next poll cycle without crashing.
        """
        if not tickers or self._client is None:
            return {}

        url = self._base_url + SNAPSHOT_PATH
        params = {
            "tickers": ",".join(tickers),
            "apiKey": self._api_key,
        }

        try:
            response = await self._client.get(url, params=params)
            response.raise_for_status()
            return self._parse_snapshot_response(response.json())

        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Massive API HTTP error %s for tickers %s: %s",
                exc.response.status_code,
                tickers,
                exc.response.text[:200],
            )
        except httpx.RequestError as exc:
            logger.warning("Massive API request error: %s", exc)
        except Exception as exc:
            logger.exception("Unexpected error fetching Massive prices: %s", exc)

        return {}

    # ------------------------------------------------------------------
    # Internal: response parsing
    # ------------------------------------------------------------------

    def _parse_snapshot_response(self, data: dict) -> dict[str, float]:
        """
        Parse a Polygon.io snapshot response into {ticker: price} pairs.

        The Polygon snapshot format::

            {
              "tickers": [
                {
                  "ticker": "AAPL",
                  "lastTrade": {"p": 190.50, ...},
                  "min": {"c": 190.40, ...},
                  "day": {"c": 190.50, ...},
                  "prevDay": {"c": 188.00, ...},
                  ...
                },
                ...
              ]
            }

        Price extraction priority (first non-None positive value wins):
          1. lastTrade.p  — most recent trade price
          2. min.c        — close of the most recent minute bar
          3. day.c        — close of the current day bar
        """
        prices: dict[str, float] = {}

        ticker_list = data.get("tickers") or []
        if not isinstance(ticker_list, list):
            logger.warning("Unexpected 'tickers' format in Massive response: %r", type(ticker_list))
            return prices

        for item in ticker_list:
            if not isinstance(item, dict):
                continue

            ticker = str(item.get("ticker", "")).upper()
            if not ticker:
                continue

            price = self._extract_price(item)
            if price is not None:
                prices[ticker] = price

        return prices

    @staticmethod
    def _extract_price(item: dict) -> Optional[float]:
        """
        Extract a price from a single ticker object, trying multiple fields
        in order of preference.  Returns None if no valid price is found.
        """
        candidates = [
            item.get("lastTrade", {}).get("p"),
            item.get("min", {}).get("c"),
            item.get("day", {}).get("c"),
        ]
        for raw in candidates:
            if raw is None:
                continue
            try:
                value = float(raw)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                continue
        return None

    # ------------------------------------------------------------------
    # Internal: polling loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Poll the Massive API on a fixed interval until stopped."""
        while self._running:
            tickers = list(self._tickers)
            if tickers:
                new_prices = await self._fetch_prices(tickers)
                for ticker, price in new_prices.items():
                    prev = self._price_updates.get(ticker)
                    previous_price = prev.price if prev is not None else price
                    self._price_updates[ticker] = PriceUpdate.from_prices(
                        ticker, price, previous_price
                    )

            await asyncio.sleep(self._poll_interval)
