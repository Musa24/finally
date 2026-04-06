"""Tests for the PriceUpdate dataclass and MarketDataProvider ABC."""

import pytest

from app.market_data.interface import MarketDataProvider, PriceUpdate


# ---------------------------------------------------------------------------
# PriceUpdate tests
# ---------------------------------------------------------------------------


class TestPriceUpdate:
    def test_from_prices_uptick(self):
        update = PriceUpdate.from_prices("AAPL", price=191.0, previous_price=190.0)
        assert update.ticker == "AAPL"
        assert update.price == 191.0
        assert update.previous_price == 190.0
        assert update.change_direction == "up"
        assert update.timestamp  # non-empty

    def test_from_prices_downtick(self):
        update = PriceUpdate.from_prices("aapl", price=189.0, previous_price=190.0)
        assert update.ticker == "AAPL"   # normalised to upper
        assert update.change_direction == "down"

    def test_from_prices_unchanged(self):
        update = PriceUpdate.from_prices("TSLA", price=250.0, previous_price=250.0)
        assert update.change_direction == "unchanged"

    def test_ticker_is_uppercased(self):
        update = PriceUpdate.from_prices("msft", 420.0, 419.0)
        assert update.ticker == "MSFT"

    def test_price_is_rounded_to_4dp(self):
        update = PriceUpdate.from_prices("NVDA", 875.12345678, 875.0)
        assert update.price == round(875.12345678, 4)
        assert update.previous_price == 875.0

    def test_explicit_timestamp_is_preserved(self):
        ts = "2024-01-01T00:00:00+00:00"
        update = PriceUpdate.from_prices("V", 280.0, 279.0, timestamp=ts)
        assert update.timestamp == ts

    def test_to_dict_contains_all_fields(self):
        update = PriceUpdate.from_prices("JPM", 200.0, 199.0)
        d = update.to_dict()
        assert set(d.keys()) == {
            "ticker", "price", "previous_price", "timestamp", "change_direction"
        }
        assert d["ticker"] == "JPM"
        assert d["price"] == 200.0
        assert d["previous_price"] == 199.0
        assert d["change_direction"] == "up"

    def test_to_dict_values_match_fields(self):
        update = PriceUpdate.from_prices("NFLX", 630.0, 635.0)
        d = update.to_dict()
        assert d["ticker"] == update.ticker
        assert d["price"] == update.price
        assert d["previous_price"] == update.previous_price
        assert d["timestamp"] == update.timestamp
        assert d["change_direction"] == update.change_direction


# ---------------------------------------------------------------------------
# MarketDataProvider ABC enforcement tests
# ---------------------------------------------------------------------------


class TestMarketDataProviderABC:
    def test_cannot_instantiate_abstract_class(self):
        with pytest.raises(TypeError):
            MarketDataProvider()  # type: ignore[abstract]

    def test_partial_implementation_raises(self):
        """A class that only partially implements the ABC cannot be instantiated."""

        class Partial(MarketDataProvider):
            async def start(self): ...
            async def stop(self): ...
            def get_price(self, ticker): ...
            def get_all_prices(self): ...
            # missing: add_ticker, remove_ticker, get_tickers

        with pytest.raises(TypeError):
            Partial()  # type: ignore[abstract]

    def test_full_implementation_can_be_instantiated(self):
        class Full(MarketDataProvider):
            async def start(self): ...
            async def stop(self): ...
            def get_price(self, ticker): return None
            def get_all_prices(self): return {}
            def add_ticker(self, ticker): ...
            def remove_ticker(self, ticker): ...
            def get_tickers(self): return []

        obj = Full()
        assert isinstance(obj, MarketDataProvider)
