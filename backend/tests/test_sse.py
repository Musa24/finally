"""Unit tests for the SSE streaming endpoint."""

import json
import pytest
from httpx import AsyncClient, ASGITransport
from fastapi import FastAPI

from src.market.cache import PriceCache
from src.market.models import PriceTick
from src.market.sse import price_stream


@pytest.fixture
def app_with_cache():
    app = FastAPI()
    app.state.price_cache = PriceCache()
    app.state.price_cache.update([
        PriceTick("AAPL", 193.0, 192.5, 1000.0, 0.5, 0.26),
        PriceTick("MSFT", 420.0, 419.0, 1000.0, 1.0, 0.24),
    ])
    app.add_route("/api/stream/prices", price_stream, methods=["GET"])
    return app


@pytest.fixture
def empty_app():
    app = FastAPI()
    app.state.price_cache = PriceCache()
    app.add_route("/api/stream/prices", price_stream, methods=["GET"])
    return app


@pytest.mark.asyncio
async def test_sse_returns_200(app_with_cache):
    transport = ASGITransport(app=app_with_cache)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_sse_content_type_is_event_stream(app_with_cache):
    transport = ASGITransport(app=app_with_cache)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            assert "text/event-stream" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_sse_emits_price_update_event(app_with_cache):
    transport = ASGITransport(app=app_with_cache)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            event_type = None
            data_line = None
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("event:"):
                    event_type = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_line = line.split(":", 1)[1].strip()
                    break

            assert event_type == "price_update"
            assert data_line is not None


@pytest.mark.asyncio
async def test_sse_data_contains_expected_tickers(app_with_cache):
    transport = ASGITransport(app=app_with_cache)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    payload = json.loads(raw)
                    assert "AAPL" in payload
                    assert "MSFT" in payload
                    break


@pytest.mark.asyncio
async def test_sse_data_tick_fields(app_with_cache):
    """Each ticker's data has the required fields."""
    transport = ASGITransport(app=app_with_cache)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            async for line in resp.aiter_lines():
                line = line.strip()
                if line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    payload = json.loads(raw)
                    aapl = payload["AAPL"]
                    assert "ticker" in aapl
                    assert "price" in aapl
                    assert "previous_price" in aapl
                    assert "change" in aapl
                    assert "change_pct" in aapl
                    assert "timestamp" in aapl
                    assert aapl["ticker"] == "AAPL"
                    assert aapl["price"] == 193.0
                    break


@pytest.mark.asyncio
async def test_sse_empty_cache_skips_event(empty_app):
    """With an empty cache, no price_update event is emitted initially."""
    transport = ASGITransport(app=empty_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        async with client.stream("GET", "/api/stream/prices") as resp:
            lines_received = []
            count = 0
            async for line in resp.aiter_lines():
                lines_received.append(line)
                count += 1
                # Collect a few lines and check no price_update event was sent
                if count >= 3:
                    break

            # With empty cache, no "event: price_update" line should appear
            price_update_lines = [l for l in lines_received if "price_update" in l]
            assert len(price_update_lines) == 0
