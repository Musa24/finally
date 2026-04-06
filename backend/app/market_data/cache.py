"""Shared in-memory price cache.

The background market data task (simulator or Massive poller) writes here.
SSE stream endpoints read from this cache to push updates to clients.
This decouples data production from data consumption and supports future
multi-producer scenarios without changes to the reading side.
"""

from typing import Optional

from .interface import PriceUpdate


class PriceCache:
    """Thread-safe (GIL-protected) in-memory store for the latest price of each ticker."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}

    # ------------------------------------------------------------------
    # Write side (called by the market data background task)
    # ------------------------------------------------------------------

    def update(self, update: PriceUpdate) -> None:
        """Store or overwrite the latest price for a single ticker."""
        self._prices[update.ticker.upper()] = update

    def update_many(self, updates: dict[str, PriceUpdate]) -> None:
        """Bulk-update multiple tickers at once."""
        for ticker, update in updates.items():
            self._prices[ticker.upper()] = update

    # ------------------------------------------------------------------
    # Read side (called by SSE stream and API endpoints)
    # ------------------------------------------------------------------

    def get(self, ticker: str) -> Optional[PriceUpdate]:
        """Return the latest PriceUpdate for *ticker*, or None if not cached."""
        return self._prices.get(ticker.upper())

    def get_all(self) -> dict[str, PriceUpdate]:
        """Return a shallow copy of the full price snapshot."""
        return dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Evict a ticker from the cache (e.g., when removed from watchlist)."""
        self._prices.pop(ticker.upper(), None)

    def clear(self) -> None:
        """Evict all cached prices (used in tests)."""
        self._prices.clear()

    def __len__(self) -> int:
        return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        return ticker.upper() in self._prices
