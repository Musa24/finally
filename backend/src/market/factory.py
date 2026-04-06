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
