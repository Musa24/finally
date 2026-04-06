"""Unit tests for PriceCache."""

from src.market.cache import PriceCache
from src.market.models import PriceTick


def _make_tick(ticker: str, price: float = 100.0) -> PriceTick:
    return PriceTick(
        ticker=ticker,
        price=price,
        previous_price=price - 0.5,
        timestamp=1000.0,
        change=0.5,
        change_pct=0.5,
    )


def test_update_and_get():
    cache = PriceCache()
    tick = PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26)
    cache.update([tick])
    assert cache.get("AAPL") == tick
    assert cache.get("MISSING") is None


def test_get_all_returns_copy():
    cache = PriceCache()
    tick = PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26)
    cache.update([tick])
    all_prices = cache.get_all()
    all_prices["AAPL"] = None  # mutate the copy
    assert cache.get("AAPL") == tick  # original unchanged


def test_remove():
    cache = PriceCache()
    tick = PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26)
    cache.update([tick])
    cache.remove("AAPL")
    assert cache.get("AAPL") is None


def test_remove_nonexistent_is_safe():
    cache = PriceCache()
    cache.remove("NONEXISTENT")  # should not raise


def test_update_multiple_tickers():
    cache = PriceCache()
    ticks = [_make_tick("AAPL", 193.0), _make_tick("MSFT", 420.0)]
    cache.update(ticks)
    assert cache.get("AAPL").price == 193.0
    assert cache.get("MSFT").price == 420.0


def test_update_overwrites_previous():
    cache = PriceCache()
    tick1 = _make_tick("AAPL", 193.0)
    cache.update([tick1])
    tick2 = _make_tick("AAPL", 194.0)
    cache.update([tick2])
    assert cache.get("AAPL").price == 194.0


def test_get_all_empty_cache():
    cache = PriceCache()
    assert cache.get_all() == {}


def test_get_all_contains_all_tickers():
    cache = PriceCache()
    tickers = ["AAPL", "MSFT", "GOOGL"]
    cache.update([_make_tick(t) for t in tickers])
    result = cache.get_all()
    assert set(result.keys()) == set(tickers)


def test_remove_leaves_others_intact():
    cache = PriceCache()
    cache.update([_make_tick("AAPL"), _make_tick("MSFT")])
    cache.remove("AAPL")
    assert cache.get("AAPL") is None
    assert cache.get("MSFT") is not None


def test_initial_state_is_empty():
    cache = PriceCache()
    assert cache.get("AAPL") is None
    assert cache.get_all() == {}


def test_update_empty_list_is_safe():
    cache = PriceCache()
    cache.update([])
    assert cache.get_all() == {}
