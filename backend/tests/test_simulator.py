"""Unit tests for SimulatorDataSource."""

import asyncio
import pytest

from src.market.cache import PriceCache
from src.market.simulator import SimulatorDataSource
from src.market.simulator_engine import DEFAULT_CONFIGS


@pytest.mark.asyncio
async def test_start_and_stop():
    """Simulator can be started and stopped cleanly."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.start()
    assert source._task is not None
    assert not source._task.done()
    await asyncio.sleep(0.1)  # let it tick at least once
    await source.stop()
    assert source._task.done()


@pytest.mark.asyncio
async def test_start_populates_cache():
    """After starting, cache should be populated with prices."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.start()
    await asyncio.sleep(0.15)  # wait for a few ticks
    prices = await source.get_prices()
    assert len(prices) > 0
    await source.stop()


@pytest.mark.asyncio
async def test_get_prices_returns_all_default_tickers():
    """get_prices() returns entries for all default tickers."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.start()
    await asyncio.sleep(0.15)
    prices = await source.get_prices()
    for ticker in DEFAULT_CONFIGS:
        assert ticker in prices
    await source.stop()


@pytest.mark.asyncio
async def test_add_ticker_before_start():
    """add_ticker before start is a no-op (simulator not yet initialized)."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.add_ticker("PYPL")  # no simulator yet, should not raise


@pytest.mark.asyncio
async def test_add_ticker_after_start():
    """Ticker added after start appears in subsequent get_prices()."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.start()
    await source.add_ticker("PYPL")
    assert "PYPL" in source.get_tickers()
    await asyncio.sleep(0.15)
    prices = await source.get_prices()
    assert "PYPL" in prices
    await source.stop()


@pytest.mark.asyncio
async def test_remove_ticker_after_start():
    """Ticker removed after start disappears from get_prices()."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.start()
    await asyncio.sleep(0.15)
    await source.remove_ticker("AAPL")
    assert "AAPL" not in source.get_tickers()
    prices = await source.get_prices()
    assert "AAPL" not in prices
    await source.stop()


@pytest.mark.asyncio
async def test_get_tickers_before_start_returns_empty():
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, seed=42)
    assert source.get_tickers() == []


@pytest.mark.asyncio
async def test_get_tickers_after_start():
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, update_interval=0.05, seed=42)
    await source.start()
    tickers = source.get_tickers()
    assert set(tickers) == set(DEFAULT_CONFIGS.keys())
    await source.stop()


@pytest.mark.asyncio
async def test_stop_without_start_is_safe():
    """Calling stop before start should not raise."""
    cache = PriceCache()
    source = SimulatorDataSource(price_cache=cache, seed=42)
    await source.stop()  # should not raise
