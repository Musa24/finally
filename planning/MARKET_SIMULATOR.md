# Market Simulator

The simulator generates realistic stock prices using Geometric Brownian Motion (GBM) with correlated sector moves and random events. It runs as an async background task at 500ms intervals with zero external dependencies.

---

## Mathematical Model

### GBM Exact Solution

Each tick, every price updates via:

```
S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)
```

| Symbol | Meaning |
|--------|---------|
| `S(t)` | Current price |
| `mu` | Annualized drift (expected return) |
| `sigma` | Annualized volatility |
| `dt` | Time step in years |
| `Z` | Standard normal random variable |
| `-sigma^2/2` | Ito correction — prevents phantom upward drift |

The exponential form guarantees prices stay positive. The Ito correction ensures the expected price growth rate equals `mu`, not `mu + sigma^2/2`.

### Time Step Calibration

```
dt = update_interval / SECONDS_PER_TRADING_YEAR
   = 0.5 / (252 * 6.5 * 3600)
   = 0.5 / 5_896_800
   ~ 8.48e-8
```

Per-tick moves are tiny (~0.007% std dev for 25% annual vol), but they compound correctly over a trading day to produce realistic daily ranges.

---

## Ticker Configuration

```python
from dataclasses import dataclass


@dataclass
class TickerConfig:
    seed_price: float
    mu: float          # annualized drift
    sigma: float       # annualized volatility
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
```

**Parameter rationale:**
- TSLA/NVDA have high sigma (0.45-0.55) — produces dramatic swings visible during a short demo
- JPM/V have low sigma (0.18-0.20) — financials are steadier
- Drift (mu) is 0.08-0.15 — nearly invisible over short sessions; prevents systematic downward bias

---

## Correlated Moves via Cholesky Decomposition

### Why Correlation Matters

Without correlation, AAPL might tick up while MSFT ticks down every update. Real markets have sector-level co-movement that users intuitively expect.

### Correlation Matrix

```python
import numpy as np


def build_correlation_matrix(
    tickers: list[str],
    configs: dict[str, TickerConfig],
) -> np.ndarray:
    """Build correlation matrix from sector groupings.

    Same sector: 0.7 correlation
    Cross sector: 0.3 baseline market correlation
    Self: 1.0
    """
    n = len(tickers)
    corr = np.full((n, n), 0.3)
    for i in range(n):
        for j in range(n):
            if i == j:
                corr[i][j] = 1.0
            elif configs[tickers[i]].sector == configs[tickers[j]].sector:
                corr[i][j] = 0.7
    return corr
```

### Cholesky Decomposition

The Cholesky factor `L` satisfies `L @ L^T = C` (the correlation matrix). To produce correlated normals:

```python
cholesky_L = np.linalg.cholesky(corr_matrix)

# Each tick:
z_independent = rng.standard_normal(n)
z_correlated = cholesky_L @ z_independent
```

`z_correlated` has the exact pairwise correlations specified in the matrix. The decomposition is computed once at init and recomputed only when tickers are added/removed (cheap for N < 50).

---

## Random Events

Occasional sudden moves add drama — simulating earnings surprises, news events, or sentiment shifts.

```python
EVENT_PROBABILITY = 0.0005   # per ticker per tick
EVENT_MIN_PCT = 0.02         # 2% minimum move
EVENT_MAX_PCT = 0.05         # 5% maximum move
```

At 2 ticks/second, each ticker gets an event roughly every 17 minutes — noticeable during a demo but not overwhelming.

```python
def generate_event_shocks(
    n_tickers: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """Return multipliers centered at 1.0. Most are exactly 1.0 (no event)."""
    shocks = np.ones(n_tickers)
    for i in range(n_tickers):
        if rng.random() < EVENT_PROBABILITY:
            magnitude = rng.uniform(EVENT_MIN_PCT, EVENT_MAX_PCT)
            direction = rng.choice([-1.0, 1.0])
            shocks[i] = 1.0 + direction * magnitude
    return shocks
```

Events are applied multiplicatively after the GBM step: `prices *= event_shocks`.

---

## Simulator Implementation

```python
import asyncio
import numpy as np
from time import time


class MarketSimulator:
    """GBM simulator with correlated sectors and random events."""

    SECONDS_PER_YEAR = 252 * 6.5 * 3600  # trading seconds in a year

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

        # State as numpy arrays for vectorized operations
        self.prices = np.array([c.seed_price for c in ticker_configs.values()])
        self.prev_prices = self.prices.copy()
        self.mu = np.array([c.mu for c in ticker_configs.values()])
        self.sigma = np.array([c.sigma for c in ticker_configs.values()])

        # Annualized time step
        self.dt = update_interval / self.SECONDS_PER_YEAR

        # Precomputed terms (recomputed on ticker add/remove)
        self._recompute_terms()

    def _recompute_terms(self) -> None:
        """Recompute Cholesky factor and cached drift/diffusion terms."""
        corr = build_correlation_matrix(self.tickers, self.configs)
        self.cholesky_L = np.linalg.cholesky(corr)
        self.drift_term = (self.mu - 0.5 * self.sigma ** 2) * self.dt
        self.diffusion_term = self.sigma * np.sqrt(self.dt)

    def tick(self) -> list:
        """Advance one step. Returns list of PriceTick."""
        from .interface import PriceTick

        self.prev_prices = self.prices.copy()

        # Correlated GBM step (vectorized)
        z = self.cholesky_L @ self.rng.standard_normal(self.n)
        self.prices = self.prices * np.exp(
            self.drift_term + self.diffusion_term * z
        )

        # Random events
        events = generate_event_shocks(self.n, self.rng)
        self.prices *= events

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
        """Add a ticker. Uses default config if none provided."""
        if ticker in self.configs:
            return
        if config is None:
            config = TickerConfig(
                seed_price=100.0, mu=0.10, sigma=0.30, sector="other"
            )
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
```

---

## Performance

All per-tick math is vectorized with numpy:

| Operation | Complexity |
|-----------|-----------|
| `rng.standard_normal(n)` | O(n) |
| `cholesky_L @ z` | O(n^2) — trivial for n < 50 |
| `prices * np.exp(...)` | O(n) |
| Event check | O(n) |

For 10 tickers at 500ms intervals, each tick takes microseconds. The `asyncio.sleep(0.5)` dominates.

---

## Dynamic Watchlist

When tickers are added or removed via the API:

1. Simulator arrays (`prices`, `mu`, `sigma`) are extended/shrunk
2. Correlation matrix is rebuilt
3. Cholesky factor is recomputed

Rebuilding Cholesky for n=20 costs ~microseconds. No performance concern.

New tickers that are not in `DEFAULT_CONFIGS` get a generic config (seed_price=100, mu=0.10, sigma=0.30, sector="other"). The backend could also look up a reasonable seed price from the Massive API if available, but the simulator works fine with defaults.

---

## Testing

The `seed` parameter on `MarketSimulator` makes runs deterministic:

```python
sim = MarketSimulator(DEFAULT_CONFIGS, seed=42)
ticks = sim.tick()
# Same seed always produces the same sequence
```

This enables snapshot tests and assertions on exact price values.
