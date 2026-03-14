# PR: Options GPS Autonomous Execution (Issue #26)

## Summary

Adds **autonomous trade execution** to Options GPS. The tool now supports submitting the recommended strategy directly to Deribit or Aevo, with a dry-run mode for simulated execution using mock exchange data. No changes to `pipeline.py` or `exchange.py` — the executor consumes their data classes and functions.

Closes #26

## What's New

- **`executor.py`** — new execution engine with:
  - **Data classes**: `OrderRequest` (with strike/option_type for self-contained quote lookup), `OrderResult`, `ExecutionPlan`, `ExecutionReport`
  - **Instrument name builders**: `deribit_instrument_name()` → `BTC-26FEB26-67500-C` (ISO 8601 → DDMonYY), `aevo_instrument_name()` → `BTC-67500-C`
  - **ABC executor pattern**: `BaseExecutor` with three implementations:
    - `DryRunExecutor` — offline simulation using exchange quote data, no network calls
    - `DeribitExecutor` — REST API with Bearer token auth (`/public/auth` → `/private/buy|sell`)
    - `AevoExecutor` — REST API with per-request HMAC-SHA256 signing (`AEVO-KEY`, `AEVO-TIMESTAMP`, `AEVO-SIGNATURE` headers)
  - **Orchestration**: `build_execution_plan()` with auto-routing via `leg_divergences()` from `exchange.py`, `validate_plan()` pre-flight checks, `execute_plan()` with partial-fill warnings, `get_executor()` factory reading credentials from env vars

- **CLI integration** (`main.py`) — 3 new flags:
  - `--execute` — submit live orders (requires exchange credentials)
  - `--dry-run` — simulate execution with mock exchange data (no API keys needed)
  - `--exchange deribit|aevo` — force exchange (default: auto-route each leg to best venue)
  - **Screen 5: Execution** — displays order plan, confirmation prompt (live mode), per-leg fill results, net cost summary
  - Decision log JSON includes `"execution"` key with mode, fills, net cost

- **Test coverage** — 37 new tests (156 total, all passing):
  - `test_executor.py`: class-grouped unit tests — `TestInstrumentNames` (7), `TestBuildPlan` (6), `TestValidatePlan` (5), `TestDryRunExecutor` (6), `TestExecuteFlow` (4), `TestGetExecutor` (5)
  - `test_executor_e2e.py`: full pipeline E2E — mock data → rank → build plan → dry-run execute → verify report

## Files Changed

| File | Change |
|------|--------|
| `tools/options-gps/executor.py` | **New** — execution engine (~290 lines) |
| `tools/options-gps/main.py` | Add `--execute`, `--dry-run`, `--exchange` flags + Screen 5 (~105 lines) |
| `tools/options-gps/tests/test_executor.py` | **New** — 33 unit tests in 6 class groups |
| `tools/options-gps/tests/test_executor_e2e.py` | **New** — 3 E2E tests |

**Not modified**: `pipeline.py`, `exchange.py`, `requirements.txt`, `conftest.py`

## Environment Variables

| Variable | Purpose | Required for |
|---|---|---|
| `DERIBIT_CLIENT_ID` | Deribit API key | `--execute --exchange deribit` |
| `DERIBIT_CLIENT_SECRET` | Deribit API secret | same |
| `DERIBIT_TESTNET=1` | Use Deribit testnet | optional |
| `AEVO_API_KEY` | Aevo API key | `--execute --exchange aevo` |
| `AEVO_API_SECRET` | Aevo HMAC secret | same |
| `AEVO_TESTNET=1` | Use Aevo testnet | optional |

None needed for `--dry-run`.

## Test Plan

- [ ] `python3 -m pytest tools/options-gps/tests/ -v` — all 156 tests pass
- [ ] `python3 tools/options-gps/main.py --symbol BTC --view bullish --risk medium --dry-run --no-prompt` — full dry-run flow with Screen 5
- [ ] `python3 tools/options-gps/main.py --symbol BTC --view bullish --risk medium --no-prompt` — analysis-only flow unchanged (no Screen 5)
- [ ] `python3 tools/options-gps/main.py --symbol SPY --view bullish --risk medium --dry-run --no-prompt` — non-crypto graceful skip
- [ ] `python3 tools/options-gps/main.py --symbol BTC --view bullish --risk low --dry-run --exchange deribit --no-prompt` — forced exchange routing
