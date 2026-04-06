"""Unit tests for the MassiveAPIClient."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx

from app.market_data.massive import (
    MASSIVE_BASE_URL,
    POLL_INTERVAL_FREE,
    POLL_INTERVAL_PAID,
    MassiveAPIClient,
    SNAPSHOT_PATH,
)
from app.market_data.interface import PriceUpdate


SNAPSHOT_URL = MASSIVE_BASE_URL + SNAPSHOT_PATH


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_client(
    api_key: str = "test-key",
    poll_interval: float = 0.05,
) -> MassiveAPIClient:
    return MassiveAPIClient(api_key=api_key, poll_interval=poll_interval)


def polygon_response(tickers_data: list[dict]) -> dict:
    """Build a minimal Polygon-style snapshot response."""
    return {"status": "OK", "tickers": tickers_data}


def ticker_item(
    symbol: str,
    last_trade_price: float | None = None,
    min_close: float | None = None,
    day_close: float | None = None,
) -> dict:
    item: dict = {"ticker": symbol}
    if last_trade_price is not None:
        item["lastTrade"] = {"p": last_trade_price}
    if min_close is not None:
        item["min"] = {"c": min_close}
    if day_close is not None:
        item["day"] = {"c": day_close}
    return item


# ---------------------------------------------------------------------------
# Ticker management
# ---------------------------------------------------------------------------


class TestTickerManagement:
    def test_empty_on_creation(self):
        client = make_client()
        assert client.get_tickers() == []

    def test_add_ticker_normalises_case(self):
        client = make_client()
        client.add_ticker("aapl")
        assert "AAPL" in client.get_tickers()

    def test_add_ticker_is_idempotent(self):
        client = make_client()
        client.add_ticker("AAPL")
        client.add_ticker("AAPL")
        assert client.get_tickers().count("AAPL") == 1

    def test_remove_ticker(self):
        client = make_client()
        client.add_ticker("AAPL")
        client.remove_ticker("AAPL")
        assert "AAPL" not in client.get_tickers()
        assert client.get_price("AAPL") is None

    def test_remove_unknown_is_noop(self):
        client = make_client()
        client.remove_ticker("ZZZZ")  # must not raise

    def test_get_tickers_returns_copy(self):
        client = make_client()
        client.add_ticker("AAPL")
        tickers = client.get_tickers()
        tickers.append("FAKE")
        assert "FAKE" not in client.get_tickers()

    def test_get_price_none_before_any_poll(self):
        client = make_client()
        client.add_ticker("AAPL")
        assert client.get_price("AAPL") is None

    def test_get_all_prices_empty_before_poll(self):
        client = make_client()
        client.add_ticker("AAPL")
        assert client.get_all_prices() == {}


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


class TestParseSnapshotResponse:
    def test_parses_last_trade_price(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL", last_trade_price=190.5)])
        prices = client._parse_snapshot_response(data)
        assert prices["AAPL"] == pytest.approx(190.5)

    def test_falls_back_to_min_close(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL", min_close=189.0)])
        prices = client._parse_snapshot_response(data)
        assert prices["AAPL"] == pytest.approx(189.0)

    def test_falls_back_to_day_close(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL", day_close=188.0)])
        prices = client._parse_snapshot_response(data)
        assert prices["AAPL"] == pytest.approx(188.0)

    def test_prefers_last_trade_over_min(self):
        client = make_client()
        data = polygon_response([
            ticker_item("AAPL", last_trade_price=191.0, min_close=189.0, day_close=188.0)
        ])
        prices = client._parse_snapshot_response(data)
        assert prices["AAPL"] == pytest.approx(191.0)

    def test_prefers_min_over_day(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL", min_close=189.0, day_close=185.0)])
        prices = client._parse_snapshot_response(data)
        assert prices["AAPL"] == pytest.approx(189.0)

    def test_ticker_is_uppercased(self):
        client = make_client()
        data = polygon_response([ticker_item("aapl", last_trade_price=190.0)])
        prices = client._parse_snapshot_response(data)
        assert "AAPL" in prices

    def test_multiple_tickers(self):
        client = make_client()
        data = polygon_response([
            ticker_item("AAPL", last_trade_price=190.0),
            ticker_item("MSFT", last_trade_price=420.0),
            ticker_item("GOOGL", last_trade_price=175.0),
        ])
        prices = client._parse_snapshot_response(data)
        assert prices == pytest.approx({"AAPL": 190.0, "MSFT": 420.0, "GOOGL": 175.0})

    def test_skips_item_with_no_price(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL")])  # no price fields
        prices = client._parse_snapshot_response(data)
        assert "AAPL" not in prices

    def test_skips_item_with_zero_price(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL", last_trade_price=0.0)])
        prices = client._parse_snapshot_response(data)
        assert "AAPL" not in prices

    def test_skips_item_with_negative_price(self):
        client = make_client()
        data = polygon_response([ticker_item("AAPL", last_trade_price=-5.0)])
        prices = client._parse_snapshot_response(data)
        assert "AAPL" not in prices

    def test_skips_item_with_missing_ticker_field(self):
        client = make_client()
        data = polygon_response([{"lastTrade": {"p": 190.0}}])  # no "ticker" key
        prices = client._parse_snapshot_response(data)
        assert prices == {}

    def test_handles_empty_tickers_list(self):
        client = make_client()
        prices = client._parse_snapshot_response({"tickers": []})
        assert prices == {}

    def test_handles_missing_tickers_key(self):
        client = make_client()
        prices = client._parse_snapshot_response({})
        assert prices == {}

    def test_handles_non_list_tickers(self):
        client = make_client()
        prices = client._parse_snapshot_response({"tickers": None})
        assert prices == {}

    def test_handles_non_numeric_price(self):
        client = make_client()
        data = polygon_response([{"ticker": "AAPL", "lastTrade": {"p": "N/A"}}])
        prices = client._parse_snapshot_response(data)
        assert "AAPL" not in prices


# ---------------------------------------------------------------------------
# _extract_price (static method)
# ---------------------------------------------------------------------------


class TestExtractPrice:
    def test_extracts_last_trade_price(self):
        item = {"lastTrade": {"p": 190.5}, "min": {"c": 189.0}, "day": {"c": 188.0}}
        assert MassiveAPIClient._extract_price(item) == pytest.approx(190.5)

    def test_falls_back_through_hierarchy(self):
        item = {"min": {"c": 189.0}, "day": {"c": 188.0}}
        assert MassiveAPIClient._extract_price(item) == pytest.approx(189.0)

    def test_returns_none_when_all_missing(self):
        assert MassiveAPIClient._extract_price({}) is None

    def test_skips_none_values(self):
        item = {"lastTrade": {"p": None}, "day": {"c": 188.0}}
        assert MassiveAPIClient._extract_price(item) == pytest.approx(188.0)

    def test_skips_zero(self):
        item = {"lastTrade": {"p": 0}, "min": {"c": 189.0}}
        assert MassiveAPIClient._extract_price(item) == pytest.approx(189.0)

    def test_skips_negative(self):
        item = {"lastTrade": {"p": -1.0}, "day": {"c": 188.0}}
        assert MassiveAPIClient._extract_price(item) == pytest.approx(188.0)


# ---------------------------------------------------------------------------
# HTTP fetch error handling
# ---------------------------------------------------------------------------


class TestFetchPricesErrorHandling:
    @pytest.mark.asyncio
    async def test_returns_empty_on_http_error(self):
        client = make_client()
        client._client = httpx.AsyncClient()

        with respx.mock:
            respx.get(SNAPSHOT_URL).mock(
                return_value=httpx.Response(403, json={"error": "Forbidden"})
            )
            prices = await client._fetch_prices(["AAPL"])
            assert prices == {}

    @pytest.mark.asyncio
    async def test_returns_empty_on_network_error(self):
        client = make_client()
        client._client = httpx.AsyncClient()

        with respx.mock:
            respx.get(SNAPSHOT_URL).mock(side_effect=httpx.ConnectError("timeout"))
            prices = await client._fetch_prices(["AAPL"])
            assert prices == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_tickers(self):
        client = make_client()
        client._client = httpx.AsyncClient()
        prices = await client._fetch_prices([])
        assert prices == {}

    @pytest.mark.asyncio
    async def test_returns_empty_when_client_none(self):
        client = make_client()
        # client._client is None (not started)
        prices = await client._fetch_prices(["AAPL"])
        assert prices == {}

    @pytest.mark.asyncio
    async def test_parses_valid_response(self):
        client = make_client()
        client._client = httpx.AsyncClient()
        body = polygon_response([ticker_item("AAPL", last_trade_price=190.0)])

        with respx.mock:
            respx.get(SNAPSHOT_URL).mock(return_value=httpx.Response(200, json=body))
            prices = await client._fetch_prices(["AAPL"])
            assert prices["AAPL"] == pytest.approx(190.0)

        await client._client.aclose()


# ---------------------------------------------------------------------------
# Async lifecycle
# ---------------------------------------------------------------------------


class TestMassiveClientLifecycle:
    @pytest.mark.asyncio
    async def test_start_launches_task(self):
        client = make_client()
        client.add_ticker("AAPL")

        body = polygon_response([ticker_item("AAPL", last_trade_price=190.0)])

        with respx.mock:
            respx.get(SNAPSHOT_URL).mock(return_value=httpx.Response(200, json=body))
            await client.start()
            assert client._running is True
            assert client._task is not None
            assert client._client is not None
            await client.stop()

    @pytest.mark.asyncio
    async def test_stop_cleans_up(self):
        client = make_client()
        client.add_ticker("AAPL")

        body = polygon_response([ticker_item("AAPL", last_trade_price=190.0)])

        with respx.mock:
            respx.get(SNAPSHOT_URL).mock(return_value=httpx.Response(200, json=body))
            await client.start()
            await client.stop()

        assert client._running is False
        assert client._task is None
        assert client._client is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        client = make_client()
        await client.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_prices_populated_after_poll(self):
        client = make_client(poll_interval=0.01)
        client.add_ticker("AAPL")
        client.add_ticker("MSFT")

        body = polygon_response([
            ticker_item("AAPL", last_trade_price=190.0),
            ticker_item("MSFT", last_trade_price=420.0),
        ])

        with respx.mock:
            respx.get(SNAPSHOT_URL).mock(return_value=httpx.Response(200, json=body))
            await client.start()
            await asyncio.sleep(0.05)
            await client.stop()

        aapl = client.get_price("AAPL")
        msft = client.get_price("MSFT")
        assert aapl is not None
        assert msft is not None
        assert aapl.price == pytest.approx(190.0)
        assert msft.price == pytest.approx(420.0)

    @pytest.mark.asyncio
    async def test_previous_price_tracks_changes(self):
        """On the second poll the previous_price should equal the first poll's price."""
        client = make_client(poll_interval=0.02)
        client.add_ticker("AAPL")

        call_count = 0

        async def mock_fetch(tickers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"AAPL": 190.0}
            return {"AAPL": 192.0}

        client._fetch_prices = mock_fetch  # type: ignore[method-assign]
        await client.start()
        await asyncio.sleep(0.08)
        await client.stop()

        update = client.get_price("AAPL")
        assert update is not None
        # After ≥2 polls: price=192, previous_price=190
        assert update.price == pytest.approx(192.0)
        assert update.previous_price == pytest.approx(190.0)
        assert update.change_direction == "up"

    @pytest.mark.asyncio
    async def test_poll_survives_transient_error(self):
        """A failed poll should not stop the client from polling again."""
        client = make_client(poll_interval=0.02)
        client.add_ticker("AAPL")

        call_count = 0

        async def mock_fetch(tickers):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {}  # simulates a transient error / empty result
            return {"AAPL": 190.0}

        client._fetch_prices = mock_fetch  # type: ignore[method-assign]
        await client.start()
        await asyncio.sleep(0.08)
        await client.stop()

        update = client.get_price("AAPL")
        assert update is not None
        assert update.price == pytest.approx(190.0)


# ---------------------------------------------------------------------------
# Constants sanity
# ---------------------------------------------------------------------------


class TestConstants:
    def test_poll_interval_free_is_15_seconds(self):
        assert POLL_INTERVAL_FREE == pytest.approx(15.0)

    def test_poll_interval_paid_is_2_seconds(self):
        assert POLL_INTERVAL_PAID == pytest.approx(2.0)
