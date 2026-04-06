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
