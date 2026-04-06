# Market Data Backend - Detailed Design

This document is the implementation blueprint for the market data subsystem. It consolidates the interface, simulator, and Massive API specs from the other planning docs into a single, copy-paste-ready reference with all code snippets, file paths, and wiring details an implementing agent needs.

---

## File Layout

```
backend/
  pyproject.toml
  src/
    market/
      __init__.py           # Public exports: PriceTick, PriceCache, MarketDataSource, create_market_source
      models.py             # PriceTick dataclass
      cache.py              # PriceCache
      interface.py          # MarketDataSource ABC
      factory.py            # create_market_source()
      simulator.py          # SimulatorDataSource (MarketDataSource wrapper)
      simulator_engine.py   # MarketSimulator, TickerConfig, GBM math, correlation, events
      massive.py            # MassiveDataSource (MarketDataSource wrapper, REST poller)
      sse.py                # SSE endpoint (reads from PriceCache)
```

---

## 1. Data Model: `models.py`

A single, flat dataclass represents one price update for one ticker. Every downstream consumer (SSE, trade execution, portfolio snapshots) speaks this type.

```python
"""Market data model."""

from dataclasses import dataclass


@dataclass(slots=True)
class PriceTick:
    """A single price update for one ticker."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float        # Unix epoch seconds
    change: float           # price - previous_price
    change_pct: float       # percentage change from previous_price
```

### SSE JSON representation

Every `PriceTick` is serialized to this shape before being pushed over the wire:

```json
{
  "ticker": "AAPL",
  "price": 193.42,
  "previous_price": 193.15,
  "timestamp": 1712412345.678,
  "change": 0.27,
  "change_pct": 0.1398
}
```

---

## 2. Price Cache: `cache.py`

The cache sits between the data source (writer) and SSE streams (readers). It holds only the latest tick per ticker.

```python
"""Thread-safe in-memory price cache."""

from .models import PriceTick


class PriceCache:
    """Latest price state shared between the data source and SSE streams.

    Written by the market data background task.
    Read by the SSE endpoint on every push cycle.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceTick] = {}

    def update(self, ticks: list[PriceTick]) -> None:
        """Bulk-update prices from a batch of ticks."""
        for tick in ticks:
            self._prices[tick.ticker] = tick

    def get_all(self) -> dict[str, PriceTick]:
        """Return a shallow copy of all latest ticks, keyed by ticker."""
        return dict(self._prices)

    def get(self, ticker: str) -> PriceTick | None:
        """Return the latest tick for a single ticker, or None."""
        return self._prices.get(ticker)

    def remove(self, ticker: str) -> None:
        """Remove a ticker from the cache (e.g. after watchlist removal)."""
        self._prices.pop(ticker, None)
```

### Why no locks?

All reads and writes happen on the same asyncio event loop (single thread). The `dict` operations are atomic at the Python level, and there is no concurrent mutation risk. If we ever need multi-worker support, we would switch to a shared-memory store rather than adding locks here.

---

## 3. Abstract Interface: `interface.py`

Both data sources implement this ABC. Downstream code depends only on this contract.

```python
"""Abstract market data source interface."""

from abc import ABC, abstractmethod

from .models import PriceTick


class MarketDataSource(ABC):
    """Contract for all market data providers."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize and begin producing price updates."""

    @abstractmethod
    async def stop(self) -> None:
        """Shut down cleanly, cancelling background tasks."""

    @abstractmethod
    async def get_prices(self) -> dict[str, PriceTick]:
        """Return the latest price tick for every active ticker."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. Idempotent."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set. Idempotent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the list of currently tracked tickers."""
```

---

## 4. Simulator Engine: `simulator_engine.py`

This is the pure-math core. It owns no async, no cache, no I/O -- just numpy arrays and a `tick()` method.

### 4.1 Ticker Configuration

```python
"""GBM market simulator engine."""

from dataclasses import dataclass
from time import time

import numpy as np

from .models import PriceTick


@dataclass
class TickerConfig:
    """Per-ticker simulation parameters."""

    seed_price: float
    mu: float       # annualized drift (expected return)
    sigma: float    # annualized volatility
    sector: str


DEFAULT_CONFIGS: dict[str, TickerConfig] = {
    "AAPL":  TickerConfig(seed_price=192.0,  mu=0.10, sigma=0.25, sector="tech"),
    "GOOGL": TickerConfig(seed_price=176.0,  mu=0.12, sigma=0.28, sector="tech"),
    "MSFT":  TickerConfig(seed_price=420.0,  mu=0.11, sigma=0.24, sector="tech"),
    "AMZN":  TickerConfig(seed_price=185.0,  mu=0.13, sigma=0.30, sector="tech"),
    "TSLA":  TickerConfig(seed_price=175.0,  mu=0.08, sigma=0.55, sector="tech"),
    "NVDA":  TickerConfig(seed_price=880.0,  mu=0.15, sigma=0.45, sector="tech"),
    "META":  TickerConfig(seed_price=510.0,  mu=0.12, sigma=0.35, sector="tech"),
    "JPM":   TickerConfig(seed_price=198.0,  mu=0.08, sigma=0.20, sector="finance"),
    "V":     TickerConfig(seed_price=280.0,  mu=0.09, sigma=0.18, sector="finance"),
    "NFLX":  TickerConfig(seed_price=620.0,  mu=0.11, sigma=0.38, sector="media"),
}
```

### 4.2 Correlation Matrix and Cholesky

```python
# Constants
INTRA_SECTOR_CORR = 0.7
CROSS_SECTOR_CORR = 0.3


def build_correlation_matrix(
    tickers: list[str],
    configs: dict[str, TickerConfig],
) -> np.ndarray:
    """Build a correlation matrix based on sector groupings.

    Same sector: 0.7, cross sector: 0.3, diagonal: 1.0.
    """
    n = len(tickers)
    corr = np.full((n, n), CROSS_SECTOR_CORR)
    for i in range(n):
        corr[i, i] = 1.0
        for j in range(i + 1, n):
            if configs[tickers[i]].sector == configs[tickers[j]].sector:
                corr[i, j] = INTRA_SECTOR_CORR
                corr[j, i] = INTRA_SECTOR_CORR
    return corr
```

### 4.3 Random Events

```python
EVENT_PROBABILITY = 0.0005  # per ticker per tick (~1 event per ticker every 17 min)
EVENT_MIN_PCT = 0.02        # 2% minimum move
EVENT_MAX_PCT = 0.05        # 5% maximum move


def generate_event_shocks(
    n_tickers: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return multiplicative shock factors. Most are 1.0 (no event)."""
    shocks = np.ones(n_tickers)
    for i in range(n_tickers):
        if rng.random() < EVENT_PROBABILITY:
            magnitude = rng.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
            direction = rng.choice([-1.0, 1.0])
            shocks[i] = 1.0 + direction * magnitude
    return shocks
```

### 4.4 MarketSimulator Class

```python
class MarketSimulator:
    """GBM price simulator with correlated sectors and random events.

    All math is vectorized via numpy. The tick() method is synchronous and
    returns a list of PriceTick objects -- the async wrapper lives in
    SimulatorDataSource.
    """

    SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5,896,800 trading seconds

    def __init__(
        self,
        ticker_configs: dict[str, TickerConfig],
        update_interval: float = 0.5,
        seed: int | None = None,
    ) -> None:
        self.configs = dict(ticker_configs)
        self.update_interval = update_interval
        self.rng = np.random.default_rng(seed)

        self.tickers = list(ticker_configs.keys())
        self.n = len(self.tickers)

        self.prices = np.array([c.seed_price for c in ticker_configs.values()])
        self.prev_prices = self.prices.copy()
        self.mu = np.array([c.mu for c in ticker_configs.values()])
        self.sigma = np.array([c.sigma for c in ticker_configs.values()])

        self.dt = update_interval / self.SECONDS_PER_YEAR
        self._recompute_terms()

    def _recompute_terms(self) -> None:
        """Recompute Cholesky factor and cached drift/diffusion terms."""
        corr = build_correlation_matrix(self.tickers, self.configs)
        self.cholesky_L = np.linalg.cholesky(corr)
        self.drift_term = (self.mu - 0.5 * self.sigma ** 2) * self.dt
        self.diffusion_term = self.sigma * np.sqrt(self.dt)

    def tick(self) -> list[PriceTick]:
        """Advance one time step and return new price ticks."""
        self.prev_prices = self.prices.copy()

        # Correlated normal draws via Cholesky decomposition
        z = self.cholesky_L @ self.rng.standard_normal(self.n)

        # GBM exact solution: S(t+dt) = S(t) * exp(drift + diffusion * Z)
        self.prices = self.prices * np.exp(
            self.drift_term + self.diffusion_term * z
        )

        # Apply random event shocks
        self.prices *= generate_event_shocks(self.n, self.rng)

        # Round to cents
        self.prices = np.round(self.prices, 2)

        now = time()
        return [
            PriceTick(
                ticker=self.tickers[i],
                price=float(self.prices[i]),
                previous_price=float(self.prev_prices[i]),
                timestamp=now,
                change=round(float(self.prices[i] - self.prev_prices[i]), 2),
                change_pct=round(
                    (self.prices[i] - self.prev_prices[i])
                    / self.prev_prices[i]
                    * 100,
                    4,
                ),
            )
            for i in range(self.n)
        ]

    def add_ticker(self, ticker: str, config: TickerConfig | None = None) -> None:
        """Add a ticker at runtime. Uses generic config if none provided."""
        if ticker in self.configs:
            return
        if config is None:
            config = TickerConfig(seed_price=100.0, mu=0.10, sigma=0.30, sector="other")
        self.configs[ticker] = config
        self.tickers.append(ticker)
        self.n += 1
        self.prices = np.append(self.prices, config.seed_price)
        self.prev_prices = np.append(self.prev_prices, config.seed_price)
        self.mu = np.append(self.mu, config.mu)
        self.sigma = np.append(self.sigma, config.sigma)
        self._recompute_terms()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation."""
        if ticker not in self.configs:
            return
        idx = self.tickers.index(ticker)
        del self.configs[ticker]
        self.tickers.pop(idx)
        self.n -= 1
        self.prices = np.delete(self.prices, idx)
        self.prev_prices = np.delete(self.prev_prices, idx)
        self.mu = np.delete(self.mu, idx)
        self.sigma = np.delete(self.sigma, idx)
        self._recompute_terms()
```

### 4.5 GBM Math Quick Reference

```
S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
```

| Term | Purpose |
|------|---------|
| `mu` | Annualized expected return (drift) |
| `sigma` | Annualized volatility |
| `dt` | Time step in years: `update_interval / SECONDS_PER_YEAR` |
| `Z` | Standard normal random variable (correlated across tickers via Cholesky) |
| `-sigma^2/2` | Ito correction -- ensures `E[S(t+dt)] = S(t) * exp(mu * dt)` |

With `dt ~ 8.48e-8` (0.5s tick), per-tick std dev is ~0.007% for 25% annual vol. These compound correctly over a trading day to produce realistic daily ranges.

---

## 5. Simulator Data Source: `simulator.py`

Async wrapper that owns the background task loop and writes to the price cache.

```python
"""Simulator data source -- wraps MarketSimulator for async use."""

import asyncio
import logging

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceTick
from .simulator_engine import DEFAULT_CONFIGS, MarketSimulator, TickerConfig

logger = logging.getLogger(__name__)


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource backed by the GBM simulator."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._update_interval = update_interval
        self._seed = seed
        self._simulator: MarketSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self._simulator = MarketSimulator(
            ticker_configs=DEFAULT_CONFIGS,
            update_interval=self._update_interval,
            seed=self._seed,
        )
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Simulator started (interval=%.1fs)", self._update_interval)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Simulator stopped")

    async def get_prices(self) -> dict[str, PriceTick]:
        return self._cache.get_all()

    async def add_ticker(self, ticker: str) -> None:
        if self._simulator:
            self._simulator.add_ticker(ticker)
            logger.info("Simulator: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        if self._simulator:
            self._simulator.remove_ticker(ticker)
            self._cache.remove(ticker)
            logger.info("Simulator: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._simulator.tickers if self._simulator else []

    async def _run_loop(self) -> None:
        """Background loop: tick the simulator and push to cache."""
        while True:
            ticks = self._simulator.tick()
            self._cache.update(ticks)
            await asyncio.sleep(self._update_interval)
```

---

## 6. Massive API Data Source: `massive.py`

REST poller that hits the Massive batch snapshot endpoint and writes to the same price cache.

```python
"""Massive API data source -- polls REST snapshots."""

import asyncio
import logging
from time import time

import httpx

from .cache import PriceCache
from .interface import MarketDataSource
from .models import PriceTick

logger = logging.getLogger(__name__)

MASSIVE_BASE_URL = "https://api.massive.com"
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
```

### Error handling strategy

| HTTP Status | Action |
|-------------|--------|
| 200 | Parse and update cache |
| 401/403 | Log warning, skip cycle (bad/expired key -- operator must fix) |
| 429 | Log warning, skip cycle (next poll respects interval naturally) |
| 5xx / network | Log warning, skip cycle, retry next interval |

No exponential backoff needed -- the fixed poll interval already spaces retries. If the API is down for an extended period, the cache retains the last known prices and the SSE stream keeps pushing them.

---

## 7. Factory: `factory.py`

Source selection happens once at startup. No runtime switching.

```python
"""Factory for selecting the active market data source."""

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


def create_market_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate data source based on environment variables.

    If MASSIVE_API_KEY is set and non-empty, uses the Massive REST poller.
    Otherwise, falls back to the built-in GBM simulator.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        from .massive import MassiveDataSource

        logger.info("Using Massive API for market data")
        return MassiveDataSource(api_key=api_key, price_cache=price_cache)

    from .simulator import SimulatorDataSource

    logger.info("Using simulator for market data (no MASSIVE_API_KEY set)")
    return SimulatorDataSource(price_cache=price_cache)
```

---

## 8. Package Exports: `__init__.py`

```python
"""Market data subsystem.

Public API:
    PriceTick        -- data model for a single price update
    PriceCache       -- shared in-memory price state
    MarketDataSource -- abstract interface for data providers
    create_market_source -- factory that selects simulator or Massive
"""

from .cache import PriceCache
from .factory import create_market_source
from .interface import MarketDataSource
from .models import PriceTick

__all__ = [
    "PriceTick",
    "PriceCache",
    "MarketDataSource",
    "create_market_source",
]
```

---

## 9. SSE Streaming Endpoint: `sse.py`

The SSE endpoint reads from the `PriceCache` and pushes updates to connected clients. It is independent of which data source is active.

```python
"""SSE streaming endpoint for live price updates."""

import asyncio
import json

from starlette.requests import Request
from sse_starlette.sse import EventSourceResponse


async def price_stream(request: Request) -> EventSourceResponse:
    """GET /api/stream/prices -- push price updates via SSE."""

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            prices = request.app.state.price_cache.get_all()
            if prices:
                yield {
                    "event": "price_update",
                    "data": json.dumps({
                        ticker: {
                            "ticker": tick.ticker,
                            "price": tick.price,
                            "previous_price": tick.previous_price,
                            "change": tick.change,
                            "change_pct": tick.change_pct,
                            "timestamp": tick.timestamp,
                        }
                        for ticker, tick in prices.items()
                    }),
                }

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())
```

### Design notes

- **Single event per push cycle** containing all tickers, rather than one event per ticker. This reduces overhead on the SSE connection (fewer frames, fewer `event:` headers) and lets the frontend apply all updates in one render pass.
- **`is_disconnected()` check** ensures the generator exits cleanly when the client drops the connection.
- **0.5s push interval** matches the simulator tick rate. When Massive is active (15s polls), the SSE still pushes every 0.5s but the data only changes when a new poll lands -- the frontend sees the same prices repeated, which is correct (no spurious flashes because `previous_price` stays equal to `price`).

---

## 10. FastAPI Integration (Lifespan)

Wire everything together in the application lifespan so the data source starts before the first request and stops on shutdown.

```python
"""Application lifespan -- starts/stops market data background tasks."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.market import PriceCache, create_market_source


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize price cache and data source
    price_cache = PriceCache()
    source = create_market_source(price_cache)

    # Load initial tickers from the database watchlist
    tickers = await load_watchlist_tickers_from_db()
    for ticker in tickers:
        await source.add_ticker(ticker)

    await source.start()

    # Expose on app.state so route handlers can access them
    app.state.price_cache = price_cache
    app.state.market_source = source

    yield

    await source.stop()


app = FastAPI(lifespan=lifespan)
```

### Route registration

```python
from fastapi import APIRouter
from src.market.sse import price_stream

router = APIRouter(prefix="/api")
router.add_route("/stream/prices", price_stream, methods=["GET"])
```

### Accessing the source from route handlers

When the watchlist API adds or removes a ticker, it also updates the data source:

```python
from fastapi import Request

@router.post("/watchlist")
async def add_to_watchlist(request: Request, ticker: str):
    # 1. Insert into DB
    await db_add_watchlist_ticker(ticker)
    # 2. Tell the data source to start tracking it
    await request.app.state.market_source.add_ticker(ticker)
    return {"status": "ok", "ticker": ticker}

@router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(request: Request, ticker: str):
    # 1. Delete from DB
    await db_remove_watchlist_ticker(ticker)
    # 2. Stop tracking + remove from cache
    await request.app.state.market_source.remove_ticker(ticker)
    return {"status": "ok"}
```

---

## 11. Python Dependencies

Add these to `backend/pyproject.toml`:

```toml
[project]
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.34",
    "sse-starlette>=2.2",
    "httpx>=0.28",
    "numpy>=2.2",
]
```

---

## 12. Testing

### Unit tests: simulator engine

```python
"""tests/test_simulator_engine.py"""

from src.market.simulator_engine import (
    DEFAULT_CONFIGS,
    MarketSimulator,
    build_correlation_matrix,
)


def test_tick_returns_correct_count():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    ticks = sim.tick()
    assert len(ticks) == len(DEFAULT_CONFIGS)


def test_deterministic_with_seed():
    sim1 = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    sim2 = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    t1 = sim1.tick()
    t2 = sim2.tick()
    for a, b in zip(t1, t2):
        assert a.price == b.price


def test_prices_stay_positive():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    for _ in range(1000):
        ticks = sim.tick()
        for tick in ticks:
            assert tick.price > 0


def test_add_remove_ticker():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    initial_count = sim.n
    sim.add_ticker("PYPL")
    assert sim.n == initial_count + 1
    assert "PYPL" in sim.tickers
    sim.remove_ticker("PYPL")
    assert sim.n == initial_count
    assert "PYPL" not in sim.tickers


def test_correlation_matrix_shape():
    tickers = list(DEFAULT_CONFIGS.keys())
    corr = build_correlation_matrix(tickers, DEFAULT_CONFIGS)
    assert corr.shape == (len(tickers), len(tickers))
    # Diagonal is 1.0
    for i in range(len(tickers)):
        assert corr[i, i] == 1.0
```

### Unit tests: price cache

```python
"""tests/test_cache.py"""

from src.market.cache import PriceCache
from src.market.models import PriceTick


def test_update_and_get():
    cache = PriceCache()
    tick = PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26)
    cache.update([tick])
    assert cache.get("AAPL") == tick
    assert cache.get("MISSING") is None


def test_get_all_returns_copy():
    cache = PriceCache()
    tick = PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26)
    cache.update([tick])
    all_prices = cache.get_all()
    all_prices["AAPL"] = None  # mutate the copy
    assert cache.get("AAPL") == tick  # original unchanged


def test_remove():
    cache = PriceCache()
    tick = PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26)
    cache.update([tick])
    cache.remove("AAPL")
    assert cache.get("AAPL") is None
```

### Unit tests: Massive response parsing

```python
"""tests/test_massive.py"""

from src.market.cache import PriceCache
from src.market.massive import MassiveDataSource


def test_parse_snapshot_response():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    data = {
        "tickers": [
            {
                "ticker": "AAPL",
                "lastTrade": {"p": 193.75},
                "prevDay": {"c": 192.50},
                "todaysChange": 1.25,
                "todaysChangePerc": 0.65,
            }
        ]
    }

    ticks = source._parse_snapshot_response(data)
    assert len(ticks) == 1
    assert ticks[0].ticker == "AAPL"
    assert ticks[0].price == 193.75


def test_parse_empty_response():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    ticks = source._parse_snapshot_response({"tickers": []})
    assert ticks == []
```

### Integration test: SSE stream

```python
"""tests/test_sse.py"""

import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from src.market.cache import PriceCache
from src.market.models import PriceTick
from src.market.sse import price_stream


@pytest.fixture
def app_with_cache():
    app = FastAPI()
    app.state.price_cache = PriceCache()
    app.state.price_cache.update([
        PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26),
    ])
    app.add_route("/api/stream/prices", price_stream, methods=["GET"])
    return app


@pytest.mark.anyio
async def test_sse_returns_event_stream(app_with_cache):
    transport = ASGITransport(app=app_with_cache)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            assert resp.status_code == 200
            assert "text/event-stream" in resp.headers["content-type"]
            # Read first chunk
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    assert "AAPL" in line
                    break
```

---

## 13. Data Flow Summary

```
                    ┌──────────────────┐
                    │   Environment    │
                    │  MASSIVE_API_KEY │
                    └────────┬─────────┘
                             │
                    ┌────────▼─────────┐
                    │     Factory      │
                    │ create_market_   │
                    │    source()      │
                    └────────┬─────────┘
                             │
              ┌──────────────┴──────────────┐
              │                             │
    ┌─────────▼──────────┐       ┌──────────▼─────────┐
    │  SimulatorDataSource│       │ MassiveDataSource   │
    │  (GBM engine,       │       │ (REST poller,       │
    │   500ms tick loop)  │       │  15s poll loop)     │
    └─────────┬──────────┘       └──────────┬─────────┘
              │                             │
              │    writes PriceTick[]       │
              └──────────────┬──────────────┘
                             │
                    ┌────────▼─────────┐
                    │   PriceCache     │
                    │  (in-memory      │
                    │   dict[ticker,   │
                    │   PriceTick])    │
                    └────────┬─────────┘
                             │
                    reads every 0.5s
                             │
                    ┌────────▼─────────┐
                    │  SSE Endpoint    │
                    │  /api/stream/    │
                    │    prices        │
                    └────────┬─────────┘
                             │
                   EventSource (browser)
                             │
                    ┌────────▼─────────┐
                    │    Frontend      │
                    │  (watchlist,     │
                    │   sparklines,    │
                    │   flash anims)   │
                    └──────────────────┘
```

---

## 14. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| ABC with two implementations | Downstream code (SSE, trades, portfolio) never knows which source is active |
| PriceCache as intermediary | Decouples write frequency from read frequency; SSE always reads latest regardless of source cadence |
| Factory from env var | Single decision point at startup; no runtime switching complexity |
| Massive polls REST, not WebSocket | Simpler, works on all Massive tiers, sufficient update frequency for our use case |
| Simulator at 500ms, Massive at 15s | Different cadences, same interface -- the cache smooths this out for consumers |
| Single SSE event with all tickers | Fewer frames, fewer headers, one render pass on the frontend |
| No locks on PriceCache | Single asyncio thread; dict ops are atomic; adding locks would add complexity for no benefit |
| `slots=True` on PriceTick | Lower memory per instance, faster attribute access |
| Simulator `seed` parameter | Enables deterministic tests with exact price assertions |
