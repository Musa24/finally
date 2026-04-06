# Market Data Interface

This document defines the unified Python interface that abstracts over two market data sources:
- **Massive API** — real market data, used when `MASSIVE_API_KEY` is set
- **Simulator** — GBM-based price simulation, used by default

All downstream code (SSE streaming, price cache, trade execution) depends only on this interface.

---

## Core Data Model

```python
from dataclasses import dataclass


@dataclass
class PriceTick:
    """A single price update for one ticker."""
    ticker: str
    price: float
    previous_price: float
    timestamp: float          # Unix timestamp (seconds)
    change: float             # price - previous_price
    change_pct: float         # percentage change
```

---

## Abstract Interface

```python
from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Abstract interface for market data providers."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize and begin producing price updates."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down cleanly."""

    @abstractmethod
    async def get_prices(self) -> dict[str, PriceTick]:
        """Return the latest price tick for each active ticker."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the list of currently active tickers."""
```

Both `MassiveDataSource` and `SimulatorDataSource` implement this interface.

---

## Price Cache

A shared in-memory cache sits between the data source and SSE streams. The data source writes to it; SSE endpoints read from it.

```python
class PriceCache:
    """Shared price state read by SSE streams."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceTick] = {}

    def update(self, ticks: list[PriceTick]) -> None:
        for tick in ticks:
            self._prices[tick.ticker] = tick

    def get_all(self) -> dict[str, PriceTick]:
        return dict(self._prices)

    def get(self, ticker: str) -> PriceTick | None:
        return self._prices.get(ticker)
```

---

## Factory Function

Source selection happens once at startup based on environment variables.

```python
import os


def create_market_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        from .massive import MassiveDataSource
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)

    from .simulator import SimulatorDataSource
    return SimulatorDataSource(price_cache=price_cache)
```

---

## Implementation: MassiveDataSource

Polls the Massive snapshot endpoint on an interval and writes to the price cache.

```python
import asyncio
import httpx
from time import time

MASSIVE_BASE_URL = "https://api.massive.com"
FREE_TIER_INTERVAL = 15.0   # 5 requests/min = every 12s, use 15s for safety
PAID_TIER_INTERVAL = 3.0


class MassiveDataSource(MarketDataSource):

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
        self._task: asyncio.Task | None = None
        self._prev_prices: dict[str, float] = {}

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def get_prices(self) -> dict[str, PriceTick]:
        return self._cache.get_all()

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if ticker in self._tickers:
            self._tickers.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def _poll_loop(self) -> None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            while True:
                if self._tickers:
                    await self._fetch_and_update(client)
                await asyncio.sleep(self._poll_interval)

    async def _fetch_and_update(self, client: httpx.AsyncClient) -> None:
        try:
            resp = await client.get(
                f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers",
                params={
                    "tickers": ",".join(self._tickers),
                    "apiKey": self._api_key,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, Exception):
            return  # skip this cycle, retry next interval

        now = time()
        ticks = []
        for t in data.get("tickers", []):
            ticker = t["ticker"]
            price = t["lastTrade"]["p"]
            prev = self._prev_prices.get(ticker, price)
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0.0

            ticks.append(PriceTick(
                ticker=ticker,
                price=price,
                previous_price=prev,
                timestamp=now,
                change=change,
                change_pct=change_pct,
            ))
            self._prev_prices[ticker] = price

        self._cache.update(ticks)
```

---

## Implementation: SimulatorDataSource

Wraps the GBM simulator (see [MARKET_SIMULATOR.md](MARKET_SIMULATOR.md)) and writes to the same price cache.

```python
class SimulatorDataSource(MarketDataSource):

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
    ) -> None:
        self._cache = price_cache
        self._simulator: MarketSimulator | None = None
        self._update_interval = update_interval
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        from .simulator_engine import MarketSimulator, DEFAULT_CONFIGS
        self._simulator = MarketSimulator(
            ticker_configs=DEFAULT_CONFIGS,
            update_interval=self._update_interval,
        )
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def get_prices(self) -> dict[str, PriceTick]:
        return self._cache.get_all()

    async def add_ticker(self, ticker: str) -> None:
        if self._simulator:
            self._simulator.add_ticker(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._simulator:
            self._simulator.remove_ticker(ticker)

    def get_tickers(self) -> list[str]:
        return self._simulator.tickers if self._simulator else []

    async def _run_loop(self) -> None:
        while True:
            ticks = self._simulator.tick()
            self._cache.update(ticks)
            await asyncio.sleep(self._update_interval)
```

---

## FastAPI Integration

Wire it all up in the app lifespan.

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI


@asynccontextmanager
async def lifespan(app: FastAPI):
    price_cache = PriceCache()
    source = create_market_source(price_cache)

    # Load initial tickers from DB watchlist
    tickers = await get_watchlist_tickers()
    for t in tickers:
        await source.add_ticker(t)

    await source.start()
    app.state.price_cache = price_cache
    app.state.market_source = source

    yield

    await source.stop()
```

---

## SSE Streaming

The SSE endpoint reads from the price cache, not from the data source directly.

```python
from sse_starlette.sse import EventSourceResponse
import json


async def price_stream(request):
    async def event_generator():
        while True:
            prices = request.app.state.price_cache.get_all()
            for tick in prices.values():
                yield {
                    "event": "price",
                    "data": json.dumps({
                        "ticker": tick.ticker,
                        "price": tick.price,
                        "previous_price": tick.previous_price,
                        "change": tick.change,
                        "change_pct": tick.change_pct,
                        "timestamp": tick.timestamp,
                    }),
                }
            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())
```

---

## File Structure

```
backend/
  src/
    market/
      __init__.py
      interface.py          # MarketDataSource ABC, PriceTick, PriceCache
      factory.py            # create_market_source()
      massive.py            # MassiveDataSource
      simulator.py          # SimulatorDataSource (wrapper)
      simulator_engine.py   # MarketSimulator, GBM math, TickerConfig
```

---

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| ABC with two implementations | Downstream code never knows or cares which source is active |
| PriceCache as intermediary | Decouples update frequency from read frequency; SSE always reads latest |
| Factory from env var | Single decision point at startup; no runtime switching |
| Massive polls REST, not WebSocket | Simpler, works on all Massive tiers, sufficient for our update needs |
| Simulator at 500ms, Massive at 15s (free) | Different cadences, same interface — cache smooths this out |
