"""Abstract market data interface and shared data types."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PriceUpdate:
    """A single price update for a ticker."""

    ticker: str
    price: float
    previous_price: float
    timestamp: str
    change_direction: str  # "up", "down", or "unchanged"

    @classmethod
    def from_prices(
        cls,
        ticker: str,
        price: float,
        previous_price: float,
        timestamp: Optional[str] = None,
    ) -> "PriceUpdate":
        """Construct a PriceUpdate, computing the change direction automatically."""
        if price > previous_price:
            direction = "up"
        elif price < previous_price:
            direction = "down"
        else:
            direction = "unchanged"

        return cls(
            ticker=ticker.upper(),
            price=round(price, 4),
            previous_price=round(previous_price, 4),
            timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
            change_direction=direction,
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dictionary (for SSE/JSON serialization)."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change_direction": self.change_direction,
        }


class MarketDataProvider(ABC):
    """
    Abstract base class for all market data providers.

    Two concrete implementations exist:
    - MarketSimulator: generates prices using GBM (default, no API key required)
    - MassiveAPIClient: polls the Massive (Polygon.io) REST API for live data

    All downstream code (SSE streaming, price cache, frontend) interacts only
    with this interface so the data source can be swapped transparently.
    """

    @abstractmethod
    async def start(self) -> None:
        """Start the market data provider (background task)."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the market data provider and clean up resources."""

    @abstractmethod
    def get_price(self, ticker: str) -> Optional[PriceUpdate]:
        """Return the latest PriceUpdate for a ticker, or None if unknown."""

    @abstractmethod
    def get_all_prices(self) -> dict[str, PriceUpdate]:
        """Return a snapshot of all current prices keyed by ticker symbol."""

    @abstractmethod
    def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the set of tracked symbols."""

    @abstractmethod
    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the set of tracked symbols."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the list of currently tracked ticker symbols."""
