"""Tests for the PriceCache."""

import pytest

from app.market_data.cache import PriceCache
from app.market_data.interface import PriceUpdate


def make_update(ticker: str, price: float, prev: float | None = None) -> PriceUpdate:
    return PriceUpdate.from_prices(ticker, price, prev if prev is not None else price)


class TestPriceCache:
    def setup_method(self):
        self.cache = PriceCache()

    # ------------------------------------------------------------------
    # Basic get/update
    # ------------------------------------------------------------------

    def test_get_empty_returns_none(self):
        assert self.cache.get("AAPL") is None

    def test_update_and_get_single(self):
        update = make_update("AAPL", 190.0)
        self.cache.update(update)
        result = self.cache.get("AAPL")
        assert result is update

    def test_get_is_case_insensitive(self):
        update = make_update("AAPL", 190.0)
        self.cache.update(update)
        assert self.cache.get("aapl") is update
        assert self.cache.get("Aapl") is update

    def test_update_overwrites_previous(self):
        self.cache.update(make_update("AAPL", 190.0))
        newer = make_update("AAPL", 195.0)
        self.cache.update(newer)
        assert self.cache.get("AAPL") is newer

    # ------------------------------------------------------------------
    # Bulk update
    # ------------------------------------------------------------------

    def test_update_many(self):
        updates = {
            "AAPL": make_update("AAPL", 190.0),
            "MSFT": make_update("MSFT", 420.0),
        }
        self.cache.update_many(updates)
        assert self.cache.get("AAPL").price == 190.0
        assert self.cache.get("MSFT").price == 420.0

    def test_update_many_overwrites_existing(self):
        self.cache.update(make_update("AAPL", 190.0))
        self.cache.update_many({"AAPL": make_update("AAPL", 200.0)})
        assert self.cache.get("AAPL").price == 200.0

    # ------------------------------------------------------------------
    # get_all
    # ------------------------------------------------------------------

    def test_get_all_empty(self):
        assert self.cache.get_all() == {}

    def test_get_all_returns_snapshot(self):
        self.cache.update(make_update("AAPL", 190.0))
        self.cache.update(make_update("GOOGL", 175.0))
        snapshot = self.cache.get_all()
        assert set(snapshot.keys()) == {"AAPL", "GOOGL"}

    def test_get_all_is_a_copy(self):
        """Mutating the returned dict should not affect the cache."""
        self.cache.update(make_update("AAPL", 190.0))
        snapshot = self.cache.get_all()
        snapshot["AAPL"] = make_update("AAPL", 999.0)
        assert self.cache.get("AAPL").price == 190.0

    # ------------------------------------------------------------------
    # remove / clear
    # ------------------------------------------------------------------

    def test_remove_existing_ticker(self):
        self.cache.update(make_update("AAPL", 190.0))
        self.cache.remove("AAPL")
        assert self.cache.get("AAPL") is None

    def test_remove_unknown_ticker_is_noop(self):
        self.cache.remove("ZZZZ")  # should not raise

    def test_clear(self):
        self.cache.update(make_update("AAPL", 190.0))
        self.cache.update(make_update("MSFT", 420.0))
        self.cache.clear()
        assert len(self.cache) == 0
        assert self.cache.get_all() == {}

    # ------------------------------------------------------------------
    # __len__ / __contains__
    # ------------------------------------------------------------------

    def test_len_empty(self):
        assert len(self.cache) == 0

    def test_len_after_updates(self):
        self.cache.update(make_update("AAPL", 190.0))
        self.cache.update(make_update("MSFT", 420.0))
        assert len(self.cache) == 2

    def test_len_does_not_double_count(self):
        self.cache.update(make_update("AAPL", 190.0))
        self.cache.update(make_update("AAPL", 195.0))
        assert len(self.cache) == 1

    def test_contains_true(self):
        self.cache.update(make_update("AAPL", 190.0))
        assert "AAPL" in self.cache
        assert "aapl" in self.cache

    def test_contains_false(self):
        assert "AAPL" not in self.cache
