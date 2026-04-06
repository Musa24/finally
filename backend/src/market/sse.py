"""SSE streaming endpoint for live price updates."""

import asyncio
import json

from starlette.requests import Request
from sse_starlette.sse import EventSourceResponse


async def price_stream(request: Request) -> EventSourceResponse:
    """GET /api/stream/prices -- push price updates via SSE."""

    async def event_generator():
        while True:
            if await request.is_disconnected():
                break

            prices = request.app.state.price_cache.get_all()
            if prices:
                yield {
                    "event": "price_update",
                    "data": json.dumps({
                        ticker: {
                            "ticker": tick.ticker,
                            "price": tick.price,
                            "previous_price": tick.previous_price,
                            "change": tick.change,
                            "change_pct": tick.change_pct,
                            "timestamp": tick.timestamp,
                        }
                        for ticker, tick in prices.items()
                    }),
                }

            await asyncio.sleep(0.5)

    return EventSourceResponse(event_generator())
