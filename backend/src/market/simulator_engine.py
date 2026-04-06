"""GBM market simulator engine."""

from dataclasses import dataclass
from time import time

import numpy as np

from .models import PriceTick


@dataclass
class TickerConfig:
    """Per-ticker simulation parameters."""

    seed_price: float
    mu: float       # annualized drift (expected return)
    sigma: float    # annualized volatility
    sector: str


DEFAULT_CONFIGS: dict[str, TickerConfig] = {
    "AAPL":  TickerConfig(seed_price=192.0,  mu=0.10, sigma=0.25, sector="tech"),
    "GOOGL": TickerConfig(seed_price=176.0,  mu=0.12, sigma=0.28, sector="tech"),
    "MSFT":  TickerConfig(seed_price=420.0,  mu=0.11, sigma=0.24, sector="tech"),
    "AMZN":  TickerConfig(seed_price=185.0,  mu=0.13, sigma=0.30, sector="tech"),
    "TSLA":  TickerConfig(seed_price=175.0,  mu=0.08, sigma=0.55, sector="tech"),
    "NVDA":  TickerConfig(seed_price=880.0,  mu=0.15, sigma=0.45, sector="tech"),
    "META":  TickerConfig(seed_price=510.0,  mu=0.12, sigma=0.35, sector="tech"),
    "JPM":   TickerConfig(seed_price=198.0,  mu=0.08, sigma=0.20, sector="finance"),
    "V":     TickerConfig(seed_price=280.0,  mu=0.09, sigma=0.18, sector="finance"),
    "NFLX":  TickerConfig(seed_price=620.0,  mu=0.11, sigma=0.38, sector="media"),
}

# Correlation constants
INTRA_SECTOR_CORR = 0.7
CROSS_SECTOR_CORR = 0.3

# Random event constants
EVENT_PROBABILITY = 0.0005  # per ticker per tick (~1 event per ticker every 17 min)
EVENT_MIN_PCT = 0.02        # 2% minimum move
EVENT_MAX_PCT = 0.05        # 5% maximum move


def build_correlation_matrix(
    tickers: list[str],
    configs: dict[str, TickerConfig],
) -> np.ndarray:
    """Build a correlation matrix based on sector groupings.

    Same sector: 0.7, cross sector: 0.3, diagonal: 1.0.
    """
    n = len(tickers)
    corr = np.full((n, n), CROSS_SECTOR_CORR)
    for i in range(n):
        corr[i, i] = 1.0
        for j in range(i + 1, n):
            if configs[tickers[i]].sector == configs[tickers[j]].sector:
                corr[i, j] = INTRA_SECTOR_CORR
                corr[j, i] = INTRA_SECTOR_CORR
    return corr


def generate_event_shocks(
    n_tickers: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return multiplicative shock factors. Most are 1.0 (no event)."""
    shocks = np.ones(n_tickers)
    for i in range(n_tickers):
        if rng.random() < EVENT_PROBABILITY:
            magnitude = rng.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
            direction = rng.choice([-1.0, 1.0])
            shocks[i] = 1.0 + direction * magnitude
    return shocks


class MarketSimulator:
    """GBM price simulator with correlated sectors and random events.

    All math is vectorized via numpy. The tick() method is synchronous and
    returns a list of PriceTick objects -- the async wrapper lives in
    SimulatorDataSource.
    """

    SECONDS_PER_YEAR = 252 * 6.5 * 3600  # ~5,896,800 trading seconds

    def __init__(
        self,
        ticker_configs: dict[str, TickerConfig],
        update_interval: float = 0.5,
        seed: int | None = None,
    ) -> None:
        self.configs = dict(ticker_configs)
        self.update_interval = update_interval
        self.rng = np.random.default_rng(seed)

        self.tickers = list(ticker_configs.keys())
        self.n = len(self.tickers)

        self.prices = np.array([c.seed_price for c in ticker_configs.values()])
        self.prev_prices = self.prices.copy()
        self.mu = np.array([c.mu for c in ticker_configs.values()])
        self.sigma = np.array([c.sigma for c in ticker_configs.values()])

        self.dt = update_interval / self.SECONDS_PER_YEAR
        self._recompute_terms()

    def _recompute_terms(self) -> None:
        """Recompute Cholesky factor and cached drift/diffusion terms."""
        corr = build_correlation_matrix(self.tickers, self.configs)
        self.cholesky_L = np.linalg.cholesky(corr)
        self.drift_term = (self.mu - 0.5 * self.sigma ** 2) * self.dt
        self.diffusion_term = self.sigma * np.sqrt(self.dt)

    def tick(self) -> list[PriceTick]:
        """Advance one time step and return new price ticks."""
        self.prev_prices = self.prices.copy()

        # Correlated normal draws via Cholesky decomposition
        z = self.cholesky_L @ self.rng.standard_normal(self.n)

        # GBM exact solution: S(t+dt) = S(t) * exp(drift + diffusion * Z)
        self.prices = self.prices * np.exp(
            self.drift_term + self.diffusion_term * z
        )

        # Apply random event shocks
        self.prices *= generate_event_shocks(self.n, self.rng)

        # Round to cents
        self.prices = np.round(self.prices, 2)

        now = time()
        return [
            PriceTick(
                ticker=self.tickers[i],
                price=float(self.prices[i]),
                previous_price=float(self.prev_prices[i]),
                timestamp=now,
                change=round(float(self.prices[i] - self.prev_prices[i]), 2),
                change_pct=round(
                    (self.prices[i] - self.prev_prices[i])
                    / self.prev_prices[i]
                    * 100,
                    4,
                ),
            )
            for i in range(self.n)
        ]

    def add_ticker(self, ticker: str, config: TickerConfig | None = None) -> None:
        """Add a ticker at runtime. Uses generic config if none provided."""
        if ticker in self.configs:
            return
        if config is None:
            config = TickerConfig(seed_price=100.0, mu=0.10, sigma=0.30, sector="other")
        self.configs[ticker] = config
        self.tickers.append(ticker)
        self.n += 1
        self.prices = np.append(self.prices, config.seed_price)
        self.prev_prices = np.append(self.prev_prices, config.seed_price)
        self.mu = np.append(self.mu, config.mu)
        self.sigma = np.append(self.sigma, config.sigma)
        self._recompute_terms()

    def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the simulation."""
        if ticker not in self.configs:
            return
        idx = self.tickers.index(ticker)
        del self.configs[ticker]
        self.tickers.pop(idx)
        self.n -= 1
        self.prices = np.delete(self.prices, idx)
        self.prev_prices = np.delete(self.prev_prices, idx)
        self.mu = np.delete(self.mu, idx)
        self.sigma = np.delete(self.sigma, idx)
        self._recompute_terms()
