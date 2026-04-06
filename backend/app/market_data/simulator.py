"""Market data simulator using Geometric Brownian Motion (GBM).

Generates realistic-looking price movements with:
- Per-ticker GBM parameters (drift μ and volatility σ)
- Correlated moves across tickers in the same market sector
- Occasional random "events" causing sudden 2-5% price jumps
- Runs as an in-process asyncio background task — no external dependencies
"""

import asyncio
import math
import random
from typing import Optional

import numpy as np

from .interface import MarketDataProvider, PriceUpdate

# ---------------------------------------------------------------------------
# Seed prices — realistic starting values (approximate as of late 2024)
# ---------------------------------------------------------------------------
DEFAULT_SEED_PRICES: dict[str, float] = {
    "AAPL": 190.0,
    "GOOGL": 175.0,
    "MSFT": 420.0,
    "AMZN": 185.0,
    "TSLA": 250.0,
    "NVDA": 875.0,
    "META": 500.0,
    "JPM": 200.0,
    "V": 280.0,
    "NFLX": 630.0,
}

# ---------------------------------------------------------------------------
# Per-ticker GBM parameters
# mu  = annualised drift (small positive number for realistic long-run growth)
# sigma = annualised volatility; scaled by sqrt(dt) per step
# ---------------------------------------------------------------------------
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL":  {"mu": 0.0001, "sigma": 0.015},
    "GOOGL": {"mu": 0.0001, "sigma": 0.016},
    "MSFT":  {"mu": 0.00012, "sigma": 0.014},
    "AMZN":  {"mu": 0.00015, "sigma": 0.018},
    "TSLA":  {"mu": 0.0002,  "sigma": 0.035},
    "NVDA":  {"mu": 0.0003,  "sigma": 0.030},
    "META":  {"mu": 0.00018, "sigma": 0.020},
    "JPM":   {"mu": 0.00008, "sigma": 0.012},
    "V":     {"mu": 0.00007, "sigma": 0.010},
    "NFLX":  {"mu": 0.00014, "sigma": 0.022},
}

# Default params used for tickers not in TICKER_PARAMS
DEFAULT_PARAMS: dict[str, float] = {"mu": 0.0001, "sigma": 0.020}

# ---------------------------------------------------------------------------
# Correlation groups — tickers in the same group share a common factor shock,
# producing correlated (but not identical) price moves.
# ---------------------------------------------------------------------------
CORRELATION_GROUPS: list[list[str]] = [
    ["AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA"],  # Mega-cap tech
    ["TSLA", "NVDA"],                                     # Growth / momentum
    ["JPM", "V"],                                         # Financials
    ["NFLX"],                                             # Entertainment
]

# Weight of the common-factor component vs idiosyncratic noise [0, 1]
COMMON_FACTOR_WEIGHT = 0.6

# ---------------------------------------------------------------------------
# Simulation settings
# ---------------------------------------------------------------------------
UPDATE_INTERVAL = 0.5          # seconds between price ticks
EVENT_PROBABILITY = 0.002      # probability of a sudden event per ticker per tick
EVENT_MIN_MAGNITUDE = 0.02     # minimum jump size (2%)
EVENT_MAX_MAGNITUDE = 0.05     # maximum jump size (5%)
PRICE_FLOOR_FRACTION = 0.01    # price never falls below 1% of its seed value


class MarketSimulator(MarketDataProvider):
    """
    In-process market data simulator.

    Usage::

        sim = MarketSimulator()
        sim.add_ticker("AAPL")
        await sim.start()
        # ... later ...
        update = sim.get_price("AAPL")
        await sim.stop()
    """

    def __init__(
        self,
        seed_prices: Optional[dict[str, float]] = None,
        update_interval: float = UPDATE_INTERVAL,
        random_seed: Optional[int] = None,
    ) -> None:
        self._seed_prices: dict[str, float] = seed_prices or dict(DEFAULT_SEED_PRICES)
        self._update_interval = update_interval
        self._prices: dict[str, float] = {}
        self._price_updates: dict[str, PriceUpdate] = {}
        self._tickers: list[str] = []
        self._task: Optional[asyncio.Task] = None
        self._running = False

        if random_seed is not None:
            random.seed(random_seed)
            np.random.seed(random_seed)

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the simulation loop as a background asyncio task."""
        self._running = True
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def get_price(self, ticker: str) -> Optional[PriceUpdate]:
        return self._price_updates.get(ticker.upper())

    def get_all_prices(self) -> dict[str, PriceUpdate]:
        return dict(self._price_updates)

    def add_ticker(self, ticker: str) -> None:
        """Add a ticker and initialise its price from the seed table."""
        ticker = ticker.upper()
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        seed = self._seed_prices.get(ticker, 100.0)
        self._prices[ticker] = seed
        # Emit an initial update so callers always get a valid PriceUpdate
        self._price_updates[ticker] = PriceUpdate.from_prices(ticker, seed, seed)

    def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.upper()
        if ticker in self._tickers:
            self._tickers.remove(ticker)
        self._prices.pop(ticker, None)
        self._price_updates.pop(ticker, None)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_correlated_shocks(self) -> dict[str, float]:
        """
        Generate standard-normal shocks for every tracked ticker.

        Tickers in the same CORRELATION_GROUPS entry share a common-factor
        component so they move in the same direction most of the time —
        mimicking real-world sector co-movement.
        """
        shocks: dict[str, float] = {}
        assigned: set[str] = set()

        for group in CORRELATION_GROUPS:
            active = [t for t in group if t in self._tickers]
            if not active:
                continue
            common = float(np.random.standard_normal())
            for ticker in active:
                idio = float(np.random.standard_normal())
                shocks[ticker] = (
                    COMMON_FACTOR_WEIGHT * common
                    + (1 - COMMON_FACTOR_WEIGHT) * idio
                )
                assigned.add(ticker)

        # Tickers not covered by any group get independent shocks
        for ticker in self._tickers:
            if ticker not in assigned:
                shocks[ticker] = float(np.random.standard_normal())

        return shocks

    def _apply_gbm_step(self, ticker: str, shock: float) -> float:
        """
        Advance a ticker's price by one GBM step.

        GBM discretisation (Euler-Maruyama):
            S(t+dt) = S(t) * exp((μ - ½σ²)dt  +  σ√dt · Z)

        where Z ~ N(0,1) is the *shock* passed in (possibly correlated).
        """
        params = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        mu = params["mu"]
        sigma = params["sigma"]
        dt = self._update_interval

        current = self._prices[ticker]
        drift = (mu - 0.5 * sigma ** 2) * dt
        diffusion = sigma * math.sqrt(dt) * shock
        return current * math.exp(drift + diffusion)

    def _maybe_apply_event(self, ticker: str, price: float) -> float:
        """
        With a small probability, apply a sudden 2-5% price shock in either
        direction — simulating earnings surprises, news events, etc.
        """
        if random.random() < EVENT_PROBABILITY:
            magnitude = random.uniform(EVENT_MIN_MAGNITUDE, EVENT_MAX_MAGNITUDE)
            direction = random.choice((-1, 1))
            price *= 1 + direction * magnitude
        return price

    async def _run(self) -> None:
        """Main simulation loop — runs until cancelled or _running is False."""
        while self._running:
            if self._tickers:
                shocks = self._get_correlated_shocks()
                for ticker in list(self._tickers):
                    if ticker not in self._prices:
                        self._prices[ticker] = self._seed_prices.get(ticker, 100.0)

                    previous_price = self._prices[ticker]
                    shock = shocks.get(ticker, float(np.random.standard_normal()))

                    new_price = self._apply_gbm_step(ticker, shock)
                    new_price = self._maybe_apply_event(ticker, new_price)

                    # Hard floor: price can't fall below 1% of seed value
                    floor = self._seed_prices.get(ticker, 100.0) * PRICE_FLOOR_FRACTION
                    new_price = max(new_price, floor)

                    self._prices[ticker] = new_price
                    self._price_updates[ticker] = PriceUpdate.from_prices(
                        ticker, new_price, previous_price
                    )

            await asyncio.sleep(self._update_interval)
