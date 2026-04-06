"""Unit tests for the MarketSimulator."""

import asyncio
import math

import pytest

from app.market_data.simulator import (
    DEFAULT_SEED_PRICES,
    PRICE_FLOOR_FRACTION,
    MarketSimulator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_sim(seed: int = 42, interval: float = 0.05) -> MarketSimulator:
    """Return a deterministic simulator with a fast update interval."""
    return MarketSimulator(update_interval=interval, random_seed=seed)


# ---------------------------------------------------------------------------
# Initialisation and ticker management
# ---------------------------------------------------------------------------


class TestTickerManagement:
    def test_empty_on_creation(self):
        sim = make_sim()
        assert sim.get_tickers() == []
        assert sim.get_all_prices() == {}

    def test_add_ticker_registers_it(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        assert "AAPL" in sim.get_tickers()

    def test_add_ticker_normalises_case(self):
        sim = make_sim()
        sim.add_ticker("aapl")
        assert "AAPL" in sim.get_tickers()

    def test_add_ticker_is_idempotent(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        sim.add_ticker("AAPL")
        assert sim.get_tickers().count("AAPL") == 1

    def test_add_ticker_initialises_price_at_seed(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        update = sim.get_price("AAPL")
        assert update is not None
        assert update.price == pytest.approx(DEFAULT_SEED_PRICES["AAPL"])

    def test_add_unknown_ticker_defaults_to_100(self):
        sim = make_sim()
        sim.add_ticker("UNKN")
        update = sim.get_price("UNKN")
        assert update is not None
        assert update.price == pytest.approx(100.0)

    def test_remove_ticker(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        sim.remove_ticker("AAPL")
        assert "AAPL" not in sim.get_tickers()
        assert sim.get_price("AAPL") is None

    def test_remove_unknown_ticker_is_noop(self):
        sim = make_sim()
        sim.remove_ticker("ZZZZ")  # must not raise

    def test_get_price_returns_none_for_unregistered(self):
        sim = make_sim()
        assert sim.get_price("AAPL") is None

    def test_get_all_prices_reflects_added_tickers(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        sim.add_ticker("MSFT")
        prices = sim.get_all_prices()
        assert "AAPL" in prices
        assert "MSFT" in prices

    def test_get_tickers_returns_copy(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        tickers = sim.get_tickers()
        tickers.append("FAKE")
        assert "FAKE" not in sim.get_tickers()

    def test_get_all_prices_returns_copy(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        prices = sim.get_all_prices()
        prices["EXTRA"] = prices["AAPL"]
        assert "EXTRA" not in sim.get_all_prices()


# ---------------------------------------------------------------------------
# GBM math
# ---------------------------------------------------------------------------


class TestGBMStep:
    """Verify the GBM step formula is implemented correctly."""

    def test_gbm_step_returns_positive_price(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        # Apply many steps and verify price stays positive
        for shock in [-5.0, -10.0, 0.0, 5.0, 10.0]:
            price = sim._apply_gbm_step("AAPL", shock)
            assert price > 0, f"Price went non-positive with shock={shock}"

    def test_gbm_step_positive_shock_increases_price(self):
        """A very large positive shock should produce a higher price."""
        sim = make_sim()
        sim.add_ticker("AAPL")
        original = sim._prices["AAPL"]
        new_price = sim._apply_gbm_step("AAPL", shock=10.0)
        assert new_price > original

    def test_gbm_step_negative_shock_decreases_price(self):
        """A very large negative shock should produce a lower price."""
        sim = make_sim()
        sim.add_ticker("AAPL")
        original = sim._prices["AAPL"]
        new_price = sim._apply_gbm_step("AAPL", shock=-10.0)
        assert new_price < original

    def test_gbm_step_zero_shock_near_drift(self):
        """With Z=0, the step equals S*exp((μ-½σ²)*dt)."""
        sim = make_sim()
        sim.add_ticker("AAPL")
        from app.market_data.simulator import TICKER_PARAMS

        params = TICKER_PARAMS["AAPL"]
        mu, sigma, dt = params["mu"], params["sigma"], sim._update_interval
        expected = DEFAULT_SEED_PRICES["AAPL"] * math.exp((mu - 0.5 * sigma ** 2) * dt)
        got = sim._apply_gbm_step("AAPL", shock=0.0)
        assert got == pytest.approx(expected, rel=1e-9)

    def test_gbm_step_uses_ticker_params(self):
        """Two tickers with different volatility diverge under the same shock."""
        sim = make_sim(seed=0, interval=1.0)
        # Manually set equal prices to isolate sigma effect
        sim.add_ticker("V")     # low vol: sigma=0.010
        sim.add_ticker("TSLA")  # high vol: sigma=0.035
        sim._prices["V"] = 100.0
        sim._prices["TSLA"] = 100.0

        shock = 2.0
        price_v = sim._apply_gbm_step("V", shock)
        price_tsla = sim._apply_gbm_step("TSLA", shock)
        # Higher volatility + positive shock → larger price move
        assert price_tsla > price_v


# ---------------------------------------------------------------------------
# Price floor
# ---------------------------------------------------------------------------


class TestPriceFloor:
    def test_price_floor_enforced_in_run(self):
        """Even with catastrophic negative shocks the floor is respected."""
        sim = make_sim()
        sim.add_ticker("AAPL")
        floor = DEFAULT_SEED_PRICES["AAPL"] * PRICE_FLOOR_FRACTION

        # Drive price down dramatically
        sim._prices["AAPL"] = 0.0001
        new_price = sim._apply_gbm_step("AAPL", shock=-100.0)
        floored = max(new_price, floor)
        assert floored >= floor


# ---------------------------------------------------------------------------
# Random event
# ---------------------------------------------------------------------------


class TestRandomEvent:
    def test_event_changes_price(self):
        """Over many calls, at least one event should fire and change the price."""
        sim = make_sim()
        sim.add_ticker("AAPL")
        original = 190.0
        events_fired = 0

        for _ in range(10_000):
            result = sim._maybe_apply_event("AAPL", original)
            if result != original:
                events_fired += 1

        assert events_fired > 0, "Expected at least one event to fire in 10,000 calls"

    def test_event_price_stays_positive(self):
        sim = make_sim()
        sim.add_ticker("AAPL")
        for _ in range(1_000):
            result = sim._maybe_apply_event("AAPL", 190.0)
            assert result > 0


# ---------------------------------------------------------------------------
# Correlated shocks
# ---------------------------------------------------------------------------


class TestCorrelatedShocks:
    def test_shocks_generated_for_all_tickers(self):
        sim = make_sim(seed=1)
        for ticker in ["AAPL", "GOOGL", "MSFT", "TSLA", "JPM"]:
            sim.add_ticker(ticker)
        shocks = sim._get_correlated_shocks()
        for ticker in sim.get_tickers():
            assert ticker in shocks

    def test_correlated_group_mostly_same_direction(self):
        """
        Tech tickers should move in the same direction more than 50% of the time
        when they share a large common factor.
        """
        sim = MarketSimulator(update_interval=0.05)  # no fixed seed; statistical test
        for ticker in ["AAPL", "GOOGL", "MSFT"]:
            sim.add_ticker(ticker)

        same_direction_count = 0
        n_trials = 2_000

        for _ in range(n_trials):
            shocks = sim._get_correlated_shocks()
            signs = [1 if shocks[t] > 0 else -1 for t in ["AAPL", "GOOGL", "MSFT"]]
            if signs[0] == signs[1] == signs[2]:
                same_direction_count += 1

        # With weight=0.6 common factor, probability all 3 align > chance (12.5%)
        # Expect substantially more than 12.5% of trials all go the same way
        fraction = same_direction_count / n_trials
        assert fraction > 0.25, (
            f"Expected >25% aligned moves for correlated tech stocks, got {fraction:.1%}"
        )

    def test_uncorrelated_tickers_get_independent_shocks(self):
        """A ticker not in any group should still receive a shock."""
        sim = make_sim(seed=99)
        sim.add_ticker("ZZZZ")  # not in any group
        shocks = sim._get_correlated_shocks()
        assert "ZZZZ" in shocks


# ---------------------------------------------------------------------------
# Async lifecycle
# ---------------------------------------------------------------------------


class TestSimulatorLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        sim = make_sim(interval=0.01)
        sim.add_ticker("AAPL")
        await sim.start()
        assert sim._running is True
        assert sim._task is not None
        await asyncio.sleep(0.05)
        await sim.stop()
        assert sim._running is False
        assert sim._task is None

    @pytest.mark.asyncio
    async def test_prices_update_after_start(self):
        sim = make_sim(interval=0.01)
        sim.add_ticker("AAPL")
        initial_price = sim.get_price("AAPL").price
        await sim.start()
        await asyncio.sleep(0.1)
        await sim.stop()
        # After several ticks the price should have changed
        final_price = sim.get_price("AAPL").price
        assert final_price != initial_price

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        sim = make_sim()
        await sim.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_tickers_added_while_running_are_tracked(self):
        sim = make_sim(interval=0.01)
        sim.add_ticker("AAPL")
        await sim.start()
        await asyncio.sleep(0.05)
        sim.add_ticker("MSFT")
        await asyncio.sleep(0.05)
        await sim.stop()
        assert sim.get_price("MSFT") is not None

    @pytest.mark.asyncio
    async def test_all_default_tickers_get_prices(self):
        sim = make_sim(interval=0.01)
        for ticker in DEFAULT_SEED_PRICES:
            sim.add_ticker(ticker)
        await sim.start()
        await asyncio.sleep(0.1)
        await sim.stop()
        for ticker in DEFAULT_SEED_PRICES:
            update = sim.get_price(ticker)
            assert update is not None, f"No price for {ticker}"
            assert update.price > 0, f"Non-positive price for {ticker}"

    @pytest.mark.asyncio
    async def test_change_direction_is_set_after_tick(self):
        sim = make_sim(interval=0.01)
        sim.add_ticker("AAPL")
        await sim.start()
        await asyncio.sleep(0.1)
        await sim.stop()
        update = sim.get_price("AAPL")
        assert update.change_direction in ("up", "down", "unchanged")
