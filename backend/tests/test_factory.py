"""Unit tests for the provider factory."""

import os
from unittest.mock import patch

import pytest

from app.market_data.factory import DEFAULT_TICKERS, create_market_data_provider
from app.market_data.massive import MassiveAPIClient
from app.market_data.simulator import MarketSimulator


class TestCreateMarketDataProvider:
    def test_returns_simulator_without_env_var(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MASSIVE_API_KEY", None)
            provider = create_market_data_provider()
        assert isinstance(provider, MarketSimulator)

    def test_returns_simulator_when_key_is_empty(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": ""}):
            provider = create_market_data_provider()
        assert isinstance(provider, MarketSimulator)

    def test_returns_simulator_when_key_is_whitespace(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "   "}):
            provider = create_market_data_provider()
        assert isinstance(provider, MarketSimulator)

    def test_returns_massive_client_when_key_provided(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "real-key-abc123"}):
            provider = create_market_data_provider()
        assert isinstance(provider, MassiveAPIClient)

    def test_default_tickers_are_registered(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MASSIVE_API_KEY", None)
            provider = create_market_data_provider()
        tickers = provider.get_tickers()
        for ticker in DEFAULT_TICKERS:
            assert ticker in tickers, f"{ticker} not in provider tickers"

    def test_custom_tickers_override_defaults(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MASSIVE_API_KEY", None)
            provider = create_market_data_provider(default_tickers=["AAPL", "TSLA"])
        tickers = provider.get_tickers()
        assert tickers == ["AAPL", "TSLA"]

    def test_default_ticker_list_has_ten_entries(self):
        assert len(DEFAULT_TICKERS) == 10

    def test_massive_client_receives_api_key(self):
        with patch.dict(os.environ, {"MASSIVE_API_KEY": "my-secret-key"}):
            provider = create_market_data_provider()
        assert isinstance(provider, MassiveAPIClient)
        assert provider._api_key == "my-secret-key"
