"""Unit tests for the market data source factory."""

import os
import pytest

from src.market.cache import PriceCache
from src.market.factory import create_market_source
from src.market.simulator import SimulatorDataSource
from src.market.massive import MassiveDataSource


def test_factory_returns_simulator_when_no_key(monkeypatch):
    """Without MASSIVE_API_KEY, factory returns SimulatorDataSource."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, SimulatorDataSource)


def test_factory_returns_simulator_when_key_is_empty(monkeypatch):
    """With empty MASSIVE_API_KEY, factory returns SimulatorDataSource."""
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, SimulatorDataSource)


def test_factory_returns_simulator_when_key_is_whitespace(monkeypatch):
    """With whitespace-only MASSIVE_API_KEY, factory returns SimulatorDataSource."""
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, SimulatorDataSource)


def test_factory_returns_massive_when_key_is_set(monkeypatch):
    """With a non-empty MASSIVE_API_KEY, factory returns MassiveDataSource."""
    monkeypatch.setenv("MASSIVE_API_KEY", "test-api-key")
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, MassiveDataSource)


def test_factory_passes_api_key_to_massive(monkeypatch):
    """Factory passes the API key to MassiveDataSource."""
    monkeypatch.setenv("MASSIVE_API_KEY", "my-secret-key")
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, MassiveDataSource)
    assert source._api_key == "my-secret-key"


def test_factory_passes_cache_to_simulator(monkeypatch):
    """Factory passes the PriceCache to SimulatorDataSource."""
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, SimulatorDataSource)
    assert source._cache is cache


def test_factory_passes_cache_to_massive(monkeypatch):
    """Factory passes the PriceCache to MassiveDataSource."""
    monkeypatch.setenv("MASSIVE_API_KEY", "test-key")
    cache = PriceCache()
    source = create_market_source(cache)
    assert isinstance(source, MassiveDataSource)
    assert source._cache is cache
