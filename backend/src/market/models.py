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
