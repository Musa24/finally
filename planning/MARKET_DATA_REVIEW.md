# Market Data Backend — Code Review

Reviewer: Claude (Opus 4.6)
Scope: `backend/src/market/**`, `backend/src/main.py`, `backend/tests/**`
Spec: `planning/MARKET_DATA_DESIGN.md`, `planning/PLAN.md`

## Summary

The implementation closely follows `MARKET_DATA_DESIGN.md`. The simulator engine, price cache, factory, and Massive REST poller are all implemented as specified, and the unit‑test suite is generous (65 of 71 tests pass in <2s). However, **all 6 SSE tests hang indefinitely**, the FastAPI lifespan has a sequencing bug that silently drops any non-default tickers, and the Massive base URL deviates from the spec. Details below.

## Test Results

Command: `uv run pytest`

| File | Tests | Pass | Fail/Hang |
|---|---|---|---|
| `tests/test_cache.py` | ✓ all | pass | — |
| `tests/test_factory.py` | ✓ all | pass | — |
| `tests/test_massive.py` | ✓ all | pass | — |
| `tests/test_simulator.py` | ✓ all | pass | — |
| `tests/test_simulator_engine.py` | ✓ all | pass | — |
| `tests/test_sse.py` | 6 | 0 | **6 hang forever** |

Workaround used to verify the rest of the suite: `uv run pytest --ignore=tests/test_sse.py` → **65 passed in 1.17s**.

The whole-suite run hangs after the 65th dot on `tests/test_sse.py::test_sse_returns_200`. Killed with `pkill -9 pytest`.

## Findings

### High — All SSE tests hang (`tests/test_sse.py`, `src/market/sse.py`)

The `event_generator` in `sse.py:13-35` is an unbounded `while True` loop that exits only when `request.is_disconnected()` returns True. Under `httpx.ASGITransport` + `client.stream(...)`, exiting the `async with client.stream(...)` block does **not** cause the ASGI request to report `is_disconnected=True`, so the generator (and hence the test) never returns. Every test in `test_sse.py` follows this pattern (e.g. `test_sse_returns_200` at `tests/test_sse.py:33-38`) and therefore hangs.

`test_sse_empty_cache_skips_event` (`tests/test_sse.py:106-123`) is doubly broken: even if disconnect were detected, the generator yields nothing when the cache is empty, so `aiter_lines()` produces no lines and the `count >= 3` break is unreachable.

Recommendations:
- Add a real disconnect signal to the test (e.g. `asyncio.wait_for(...)` around the stream block, or a timeout in `aiter_lines`), and/or
- Make `event_generator` honor an idle/heartbeat path so the empty‑cache test has something to read, and/or
- Add `pytest-timeout` to `pyproject.toml` and set a default per-test timeout so a future regression like this fails loudly instead of hanging CI.

This is a blocker: as it stands the SSE endpoint has effectively zero passing test coverage.

### High — Lifespan adds tickers before `start()`, silently dropping them (`src/main.py:25-28`, `src/market/simulator.py:50-53`)

`main.py` does:

```python
for ticker in DEFAULT_CONFIGS:
    await source.add_ticker(ticker)
await source.start()
```

`SimulatorDataSource.add_ticker` is a no-op when `self._simulator is None` (`simulator.py:51`), so every one of those calls does nothing. It only happens to "work" because `start()` constructs the simulator with `DEFAULT_CONFIGS` anyway. The moment this loop is replaced with the real watchlist (per PLAN §10 “Load initial tickers from the database watchlist”), all of those tickers will be silently dropped and the simulator will run with the hardcoded defaults instead.

`MassiveDataSource.add_ticker` does not have this bug — it appends to a plain list regardless of `start()` — so the symptom would be sim-only.

Recommendations:
- Either start the source first then add tickers, or
- Have `SimulatorDataSource` buffer pre‑start `add_ticker` calls and apply them in `start()`, or
- Accept an initial ticker list in `start()` / the constructor.

Either way, add a regression test that calls `add_ticker` before `start()` and asserts the ticker is present after `start()`.

### Medium — Massive base URL contradicts the design (`src/market/massive.py:15`)

The design doc specifies `MASSIVE_BASE_URL = "https://api.massive.com"` (`MARKET_DATA_DESIGN.md:464`), but the implementation uses `https://api.polygon.io`. PLAN.md and the design both name the service "Massive (formerly Polygon.io)". Pick one and make it consistent — either update the design to acknowledge the real Polygon URL, or change the constant to match the spec. Right now a reader following the design will be confused, and a search for "massive" will not find the actual outbound host.

### Medium — `_run_loop` / `_poll_loop` swallow background errors

Both `SimulatorDataSource._run_loop` (`simulator.py:64-69`) and `MassiveDataSource._poll_loop` (`massive.py:72-78`) are launched via `asyncio.create_task` with no `add_done_callback` and no top-level try/except. If the simulator’s `tick()` ever raises (e.g. numpy linalg failure on a degenerate correlation matrix after `add_ticker`), the task dies silently; the cache stops updating but the process keeps running and the SSE stream keeps pushing the last known prices forever. There is no log line, no health signal.

Recommendation: wrap the loop body in `try/except Exception` with `logger.exception(...)` and either re-raise to crash the worker or sleep and retry. At minimum, attach a `done_callback` that logs unhandled exceptions.

### Low — Massive `change_pct` is derived from rounded `change` (`src/market/massive.py:133-134`)

```python
change = round(price - prev, 2)
change_pct = round((change / prev) * 100, 4) if prev else 0.0
```

`change_pct` is computed from the **rounded** `change`, which throws away sub-cent precision. For high‑priced tickers (e.g. NVDA at $880) with sub-cent moves this is fine; but the simulator computes `change_pct` from the unrounded delta (`simulator_engine.py:141-146`), so the two sources are subtly inconsistent. Compute `change_pct` from `(price - prev) / prev` and round only the final value.

### Low — Simulator rounds prices to cents in-place, biasing GBM compounding (`src/market/simulator_engine.py:131`)

```python
self.prices = np.round(self.prices, 2)
```

The rounded prices are then fed back into the next `tick()` step, so the rounding error compounds across thousands of ticks. For a 0.5s tick this is ~7,200 ticks/hour and the bias is small, but the cleanest fix is to keep an unrounded `self.prices` for the GBM update and only round when constructing the `PriceTick` for output. Not urgent.

### Low — Factory ignores `poll_interval` (`src/market/factory.py:24`)

`MassiveDataSource` accepts a `poll_interval`, but the factory always constructs it with the default (15s free-tier). There is no env var (`MASSIVE_POLL_INTERVAL`?) or constructor argument exposed, so paid-tier users have no way to dial it down without editing source. Consider reading `MASSIVE_POLL_INTERVAL` from the environment, defaulting to `FREE_TIER_INTERVAL`.

### Low — `MassiveDataSource.start()` does not validate the API key

If the key is empty/garbage, the failure only surfaces on the first poll cycle (15s later) as a 401/403 log warning. A quick `GET /v2/market/status` (or similar cheap call) at `start()` would surface bad credentials immediately. Optional polish.

### Low — `SimulatorDataSource.add_ticker` accepts only the ticker symbol

There is no way to pass a `TickerConfig`, so all runtime-added tickers fall back to `seed_price=100, mu=0.10, sigma=0.30, sector="other"` (`simulator_engine.py:155-156`). For a demo this is fine; just worth noting it produces unrealistic prices (e.g. PYPL would show as $100 instead of ~$60).

### Nit — `SimulatorDataSource.add_ticker` logs success even when it’s a no‑op (`simulator.py:50-53`)

If `_simulator` is None, nothing is logged at all (silent drop — see High finding above). If it’s not None, success is logged unconditionally even when the ticker already exists (the engine treats this as idempotent at `simulator_engine.py:153`). Minor inconsistency.

### Nit — `MASSIVE_BASE_URL` is module-global, not configurable

Tests cannot override it without monkeypatching the module attribute. Acceptable but worth a `respx`-friendly indirection if integration tests against a fake server are added later.

### Nit — Unused import (`src/main.py:8`)

`from fastapi.responses import FileResponse` and `from fastapi.staticfiles import StaticFiles` are imported but unused. Will become relevant when the static frontend is mounted, but right now they’re dead code.

## What Was Done Well

- Simulator engine math matches the spec exactly: GBM exact-solution form with Itô correction, Cholesky-correlated draws, vectorized via numpy, deterministic under `seed`.
- `PriceCache` is minimal and exactly what the design called for (single-thread asyncio, no locks).
- Test coverage of the simulator engine is excellent — 24 tests covering positivity, determinism, correlation matrix shape/symmetry/positive-definiteness, idempotent add/remove, custom configs, and rounding. These caught nothing but exist as strong regression nets.
- `MassiveDataSource._parse_snapshot_response` is cleanly separated from the HTTP layer and is independently unit‑testable, which is exactly the right shape.
- HTTP error handling correctly distinguishes status errors from network errors and degrades to "keep last cached prices" rather than crashing the loop.

## Recommended Actions (Priority Order)

1. **Fix the SSE test hang.** No SSE coverage today. (High)
2. **Fix the lifespan add_ticker → start() ordering bug** in `main.py`, or buffer pre-start adds in `SimulatorDataSource`. (High)
3. Reconcile the Massive base URL with the design doc (one source of truth). (Medium)
4. Wrap `_run_loop`/`_poll_loop` bodies in try/except with logging so background failures are visible. (Medium)
5. Compute Massive `change_pct` from the unrounded delta. (Low)
6. Add `pytest-timeout` and a default per-test timeout to prevent silent hangs. (Low)
