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
