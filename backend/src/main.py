"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from src.market import PriceCache, create_market_source
from src.market.sse import price_stream
from src.market.simulator_engine import DEFAULT_CONFIGS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize price cache and data source
    price_cache = PriceCache()
    source = create_market_source(price_cache)

    # Seed default tickers (will be replaced with DB watchlist when DB is ready)
    for ticker in DEFAULT_CONFIGS:
        await source.add_ticker(ticker)

    await source.start()

    # Expose on app.state so route handlers can access them
    app.state.price_cache = price_cache
    app.state.market_source = source

    logger.info("Market data source started")
    yield

    await source.stop()
    logger.info("Market data source stopped")


app = FastAPI(title="FinAlly Backend", lifespan=lifespan)

# Market data SSE route
app.add_route("/api/stream/prices", price_stream, methods=["GET"])


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
