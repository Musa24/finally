"""Massive API data source -- polls REST snapshots."""

import asyncio
import logging
from time import time

import httpx

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceTick

logger = logging.getLogger(__name__)

MASSIVE_BASE_URL = "https://api.polygon.io"
FREE_TIER_INTERVAL = 15.0   # 5 req/min => poll every 15s for safety margin
PAID_TIER_INTERVAL = 3.0


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by Massive (formerly Polygon.io) REST API."""

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = FREE_TIER_INTERVAL,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._poll_interval = poll_interval
        self._tickers: list[str] = []
        self._prev_prices: dict[str, float] = {}
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Massive poller started (interval=%.1fs)", self._poll_interval
        )

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Massive poller stopped")

    async def get_prices(self) -> dict[str, PriceTick]:
        return self._cache.get_all()

    async def add_ticker(self, ticker: str) -> None:
        ticker = ticker.upper()
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper()
        if ticker in self._tickers:
            self._tickers.remove(ticker)
            self._cache.remove(ticker)
            logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # -- Internal polling --

    async def _poll_loop(self) -> None:
        """Background loop: poll Massive and push to cache."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                if self._tickers:
                    await self._fetch_and_update(client)
                await asyncio.sleep(self._poll_interval)

    async def _fetch_and_update(self, client: httpx.AsyncClient) -> None:
        """Single poll cycle: fetch snapshot, parse, update cache."""
        url = (
            f"{MASSIVE_BASE_URL}"
            "/v2/snapshot/locale/us/markets/stocks/tickers"
        )
        try:
            resp = await client.get(
                url,
                params={
                    "tickers": ",".join(self._tickers),
                    "apiKey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "Massive API HTTP %s: %s", exc.response.status_code, exc
            )
            return
        except httpx.HTTPError as exc:
            logger.warning("Massive API request failed: %s", exc)
            return

        ticks = self._parse_snapshot_response(data)
        self._cache.update(ticks)

    def _parse_snapshot_response(self, data: dict) -> list[PriceTick]:
        """Parse the Massive /v2/snapshot response into PriceTick objects.

        Expected shape:
        {
          "tickers": [
            {
              "ticker": "AAPL",
              "lastTrade": {"p": 193.75, ...},
              "prevDay": {"c": 192.50, ...},
              "todaysChange": 1.25,
              "todaysChangePerc": 0.65,
              ...
            },
            ...
          ]
        }
        """
        now = time()
        ticks: list[PriceTick] = []

        for t in data.get("tickers", []):
            ticker = t["ticker"]
            price = t["lastTrade"]["p"]
            prev = self._prev_prices.get(ticker, price)
            change = round(price - prev, 2)
            change_pct = round((change / prev) * 100, 4) if prev else 0.0

            ticks.append(PriceTick(
                ticker=ticker,
                price=price,
                previous_price=prev,
                timestamp=now,
                change=change,
                change_pct=change_pct,
            ))
            self._prev_prices[ticker] = price

        return ticks
