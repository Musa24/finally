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
