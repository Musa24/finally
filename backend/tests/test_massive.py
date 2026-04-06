"""Unit tests for MassiveDataSource response parsing and async behavior."""

import asyncio
import pytest
import respx
import httpx

from src.market.cache import PriceCache
from src.market.massive import MassiveDataSource, MASSIVE_BASE_URL, FREE_TIER_INTERVAL


def test_parse_snapshot_response():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    data = {
        "tickers": [
            {
                "ticker": "AAPL",
                "lastTrade": {"p": 193.75},
                "prevDay": {"c": 192.50},
                "todaysChange": 1.25,
                "todaysChangePerc": 0.65,
            }
        ]
    }

    ticks = source._parse_snapshot_response(data)
    assert len(ticks) == 1
    assert ticks[0].ticker == "AAPL"
    assert ticks[0].price == 193.75


def test_parse_empty_response():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    ticks = source._parse_snapshot_response({"tickers": []})
    assert ticks == []


def test_parse_missing_tickers_key():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    ticks = source._parse_snapshot_response({})
    assert ticks == []


def test_parse_multiple_tickers():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    data = {
        "tickers": [
            {"ticker": "AAPL", "lastTrade": {"p": 193.75}, "prevDay": {"c": 192.50}},
            {"ticker": "MSFT", "lastTrade": {"p": 420.00}, "prevDay": {"c": 418.00}},
        ]
    }

    ticks = source._parse_snapshot_response(data)
    assert len(ticks) == 2
    tickers = {t.ticker for t in ticks}
    assert "AAPL" in tickers
    assert "MSFT" in tickers


def test_parse_first_call_prev_price_equals_current():
    """On first poll, previous_price == price (no prior data)."""
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    data = {"tickers": [{"ticker": "AAPL", "lastTrade": {"p": 193.75}, "prevDay": {"c": 192.50}}]}
    ticks = source._parse_snapshot_response(data)

    assert ticks[0].previous_price == 193.75  # no prior -> prev == current
    assert ticks[0].change == 0.0


def test_parse_subsequent_call_uses_stored_prev_price():
    """On second poll, previous_price is the price from the first poll."""
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    data1 = {"tickers": [{"ticker": "AAPL", "lastTrade": {"p": 193.75}, "prevDay": {"c": 192.50}}]}
    source._parse_snapshot_response(data1)

    data2 = {"tickers": [{"ticker": "AAPL", "lastTrade": {"p": 195.00}, "prevDay": {"c": 192.50}}]}
    ticks2 = source._parse_snapshot_response(data2)

    assert ticks2[0].previous_price == 193.75
    assert ticks2[0].price == 195.00
    assert ticks2[0].change == round(195.00 - 193.75, 2)


def test_parse_updates_cache():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    data = {"tickers": [{"ticker": "AAPL", "lastTrade": {"p": 193.75}, "prevDay": {"c": 192.50}}]}
    ticks = source._parse_snapshot_response(data)
    cache.update(ticks)

    assert cache.get("AAPL") is not None
    assert cache.get("AAPL").price == 193.75


def test_add_and_remove_ticker():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    asyncio.run(source.add_ticker("aapl"))  # should uppercase
    assert "AAPL" in source.get_tickers()

    asyncio.run(source.remove_ticker("AAPL"))
    assert "AAPL" not in source.get_tickers()


def test_add_ticker_is_idempotent():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)

    asyncio.run(source.add_ticker("AAPL"))
    asyncio.run(source.add_ticker("AAPL"))
    assert source.get_tickers().count("AAPL") == 1


def test_remove_nonexistent_ticker_is_safe():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    asyncio.run(source.remove_ticker("NONEXISTENT"))  # should not raise


def test_default_poll_interval():
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    assert source._poll_interval == FREE_TIER_INTERVAL


@pytest.mark.asyncio
async def test_fetch_and_update_handles_http_error():
    """HTTP errors are logged and skipped without raising."""
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    await source.add_ticker("AAPL")

    url = f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"

    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(401))
        async with httpx.AsyncClient(timeout=10.0) as client:
            await source._fetch_and_update(client)  # should not raise

    # Cache should remain empty (no update on error)
    assert cache.get("AAPL") is None


@pytest.mark.asyncio
async def test_fetch_and_update_success():
    """Successful poll updates the cache."""
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache)
    await source.add_ticker("AAPL")

    url = f"{MASSIVE_BASE_URL}/v2/snapshot/locale/us/markets/stocks/tickers"
    response_data = {
        "tickers": [
            {"ticker": "AAPL", "lastTrade": {"p": 193.75}, "prevDay": {"c": 192.50}}
        ]
    }

    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, json=response_data))
        async with httpx.AsyncClient(timeout=10.0) as client:
            await source._fetch_and_update(client)

    assert cache.get("AAPL") is not None
    assert cache.get("AAPL").price == 193.75


@pytest.mark.asyncio
async def test_start_and_stop():
    """DataSource can be started and stopped cleanly."""
    cache = PriceCache()
    source = MassiveDataSource(api_key="test", price_cache=cache, poll_interval=60.0)
    await source.start()
    assert source._task is not None
    assert not source._task.done()
    await source.stop()
    assert source._task.done()
