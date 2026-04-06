"""Provider factory — selects the market data backend from environment variables.

If MASSIVE_API_KEY is set and non-empty, the MassiveAPIClient is returned.
Otherwise the MarketSimulator is used (no external API key required).

All 10 default tickers are pre-registered on the returned provider so the
application has prices available immediately after start().
"""

import os

from .interface import MarketDataProvider
from .massive import MassiveAPIClient
from .simulator import MarketSimulator

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def create_market_data_provider(
    default_tickers: list[str] | None = None,
) -> MarketDataProvider:
    """
    Instantiate and configure the appropriate market data provider.

    Args:
        default_tickers: Initial set of tickers to track.  Defaults to the
                         10 tickers defined in the project spec.

    Returns:
        A fully configured (but not yet started) MarketDataProvider.
    """
    massive_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if massive_key:
        provider: MarketDataProvider = MassiveAPIClient(api_key=massive_key)
    else:
        provider = MarketSimulator()

    for ticker in (default_tickers or DEFAULT_TICKERS):
        provider.add_ticker(ticker)

    return provider
