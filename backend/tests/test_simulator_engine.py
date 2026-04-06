"""Unit tests for the GBM simulator engine."""

import numpy as np
import pytest

from src.market.simulator_engine import (
    DEFAULT_CONFIGS,
    MarketSimulator,
    TickerConfig,
    build_correlation_matrix,
    generate_event_shocks,
    INTRA_SECTOR_CORR,
    CROSS_SECTOR_CORR,
)


def test_tick_returns_correct_count():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    ticks = sim.tick()
    assert len(ticks) == len(DEFAULT_CONFIGS)


def test_deterministic_with_seed():
    sim1 = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    sim2 = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    t1 = sim1.tick()
    t2 = sim2.tick()
    for a, b in zip(t1, t2):
        assert a.price == b.price
        assert a.ticker == b.ticker


def test_prices_stay_positive():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    for _ in range(1000):
        ticks = sim.tick()
        for tick in ticks:
            assert tick.price > 0


def test_tick_fields_populated():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    ticks = sim.tick()
    for tick in ticks:
        assert tick.ticker in DEFAULT_CONFIGS
        assert isinstance(tick.price, float)
        assert isinstance(tick.previous_price, float)
        assert isinstance(tick.timestamp, float)
        assert tick.timestamp > 0
        assert isinstance(tick.change, float)
        assert isinstance(tick.change_pct, float)


def test_change_equals_price_minus_previous():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    ticks = sim.tick()
    for tick in ticks:
        expected_change = round(tick.price - tick.previous_price, 2)
        assert tick.change == expected_change


def test_change_pct_calculation():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    ticks = sim.tick()
    for tick in ticks:
        if tick.previous_price != 0:
            expected_pct = round(
                (tick.price - tick.previous_price) / tick.previous_price * 100, 4
            )
            assert abs(tick.change_pct - expected_pct) < 1e-6


def test_add_remove_ticker():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    initial_count = sim.n
    sim.add_ticker("PYPL")
    assert sim.n == initial_count + 1
    assert "PYPL" in sim.tickers
    sim.remove_ticker("PYPL")
    assert sim.n == initial_count
    assert "PYPL" not in sim.tickers


def test_add_existing_ticker_is_idempotent():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    initial_count = sim.n
    sim.add_ticker("AAPL")  # already exists
    assert sim.n == initial_count


def test_remove_nonexistent_ticker_is_idempotent():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    initial_count = sim.n
    sim.remove_ticker("UNKNOWN_TICKER")
    assert sim.n == initial_count


def test_add_ticker_with_custom_config():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    config = TickerConfig(seed_price=50.0, mu=0.05, sigma=0.20, sector="other")
    sim.add_ticker("CUSTOM", config=config)
    assert "CUSTOM" in sim.tickers
    assert sim.prices[-1] == 50.0


def test_add_ticker_with_default_config():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    sim.add_ticker("NEWCO")
    assert "NEWCO" in sim.tickers
    # Default seed price is 100.0
    assert sim.prices[sim.tickers.index("NEWCO")] == 100.0


def test_tick_after_add_ticker():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    sim.add_ticker("PYPL")
    ticks = sim.tick()
    tickers_in_ticks = [t.ticker for t in ticks]
    assert "PYPL" in tickers_in_ticks


def test_tick_after_remove_ticker():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    sim.remove_ticker("AAPL")
    ticks = sim.tick()
    tickers_in_ticks = [t.ticker for t in ticks]
    assert "AAPL" not in tickers_in_ticks


def test_correlation_matrix_shape():
    tickers = list(DEFAULT_CONFIGS.keys())
    corr = build_correlation_matrix(tickers, DEFAULT_CONFIGS)
    assert corr.shape == (len(tickers), len(tickers))


def test_correlation_matrix_diagonal_is_one():
    tickers = list(DEFAULT_CONFIGS.keys())
    corr = build_correlation_matrix(tickers, DEFAULT_CONFIGS)
    for i in range(len(tickers)):
        assert corr[i, i] == 1.0


def test_correlation_matrix_intra_sector():
    # AAPL and MSFT are both "tech" -- intra-sector correlation
    tickers = ["AAPL", "MSFT"]
    configs = {k: DEFAULT_CONFIGS[k] for k in tickers}
    corr = build_correlation_matrix(tickers, configs)
    assert corr[0, 1] == INTRA_SECTOR_CORR
    assert corr[1, 0] == INTRA_SECTOR_CORR


def test_correlation_matrix_cross_sector():
    # AAPL (tech) and JPM (finance) -- cross-sector
    tickers = ["AAPL", "JPM"]
    configs = {k: DEFAULT_CONFIGS[k] for k in tickers}
    corr = build_correlation_matrix(tickers, configs)
    assert corr[0, 1] == CROSS_SECTOR_CORR
    assert corr[1, 0] == CROSS_SECTOR_CORR


def test_correlation_matrix_is_symmetric():
    tickers = list(DEFAULT_CONFIGS.keys())
    corr = build_correlation_matrix(tickers, DEFAULT_CONFIGS)
    assert np.allclose(corr, corr.T)


def test_correlation_matrix_is_positive_definite():
    tickers = list(DEFAULT_CONFIGS.keys())
    corr = build_correlation_matrix(tickers, DEFAULT_CONFIGS)
    eigenvalues = np.linalg.eigvalsh(corr)
    assert np.all(eigenvalues > 0)


def test_generate_event_shocks_shape():
    rng = np.random.default_rng(42)
    shocks = generate_event_shocks(10, rng)
    assert shocks.shape == (10,)


def test_generate_event_shocks_all_positive():
    rng = np.random.default_rng(42)
    for _ in range(100):
        shocks = generate_event_shocks(10, rng)
        assert np.all(shocks > 0)


def test_generate_event_shocks_most_are_one():
    """With very low event probability, most shocks should be 1.0."""
    rng = np.random.default_rng(42)
    shocks = generate_event_shocks(10, rng)
    # At 0.05% probability per ticker, most should be exactly 1.0
    assert np.sum(shocks == 1.0) >= 8  # at least 8 of 10 should be no-event


def test_simulator_prices_rounded_to_cents():
    sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
    for _ in range(10):
        ticks = sim.tick()
        for tick in ticks:
            assert round(tick.price, 2) == tick.price


def test_single_ticker_simulator():
    """Simulator works with only one ticker (edge case for Cholesky)."""
    configs = {"SOLO": TickerConfig(seed_price=100.0, mu=0.10, sigma=0.30, sector="other")}
    sim = MarketSimulator(configs, seed=42)
    ticks = sim.tick()
    assert len(ticks) == 1
    assert ticks[0].ticker == "SOLO"
    assert ticks[0].price > 0
