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
