## Summary

Options GPS can now **autonomously trade options** on Deribit and Aevo directly from the CLI. This adds a full execution engine with order placement, order lifecycle management (place → monitor → cancel), slippage protection, position sizing, and safety guardrails — transforming the tool from analysis-only to end-to-end autonomous execution.

**Before:** CLI showed recommended venue/price per leg but could not place orders. No `--execute` or Screen 5.

**After:** `--dry-run` simulates execution (no credentials needed); `--execute best` (or `safer`/`upside`) submits live orders to Deribit/Aevo. Each leg is auto-routed to its best venue. Orders are monitored with configurable timeout, protected by slippage limits, and partially-filled multi-leg strategies are automatically unwound on failure.

## Related Issues

Closes #26

## Type of Change

- [ ] Bug fix
- [x] Improvement to existing tool
- [ ] Documentation
- [ ] Other (describe below)

## What Changed

### `executor.py` — New execution engine (719 lines)

**Data model:**
- `OrderRequest` / `OrderResult` / `ExecutionPlan` / `ExecutionReport` dataclasses
- `OrderResult` tracks `timestamp` (ISO 8601), `slippage_pct`, and `latency_ms` per fill
- `ExecutionPlan` supports `max_slippage_pct`, `timeout_seconds`, `quantity_override`
- `ExecutionReport` includes `started_at`, `finished_at`, `cancelled_orders`

**Executors (abstract `BaseExecutor` with full order lifecycle):**
- `DryRunExecutor` — simulates fills from quote data; tracks orders for status/cancel
- `DeribitExecutor` — POST + JSON-RPC 2.0 with OAuth token caching, USD↔BTC price conversion, live order book pricing, tick-size alignment
- `AevoExecutor` — REST with EIP-712 L2 order signing (Ethereum private key), instrument ID resolution via `/markets`, 6-decimal price/amount scaling

**Order lifecycle:** `place_order()` → `get_order_status()` → `cancel_order()` on all executors

**Execution engine:**
- `build_execution_plan()` — auto-routes per leg via `leg_divergences`, builds exchange-specific instrument names (Deribit: `BTC-27MAR26-71000-C`, Aevo: `BTC-71000-C`), applies quantity override
- `validate_plan()` — pre-flight checks (instruments, prices, quantities, actions)
- `execute_plan()` — sequential execution with:
  - **Order monitoring:** polls `get_order_status()` until filled or timeout, then cancels
  - **Slippage protection:** rejects fills exceeding `--max-slippage` threshold
  - **Auto-cancel on partial failure:** cancels already-filled legs when a later leg fails
- `get_executor()` factory — reads credentials from env vars, supports testnet

### `main.py` — CLI integration

**New flags:**
| Flag | Purpose |
|------|---------|
| `--execute best\|safer\|upside` | Which strategy card to trade |
| `--dry-run` | Simulate without placing real orders |
| `--force` | Override no-trade guardrail for live execution |
| `--exchange deribit\|aevo` | Force all legs to one venue (default: auto-route) |
| `--max-slippage N` | Max allowed slippage % per fill (0=off) |
| `--quantity N` | Override contract quantity for all legs (0=default) |
| `--timeout N` | Seconds to wait for order fill before cancel (0=fire-and-forget) |
| `--screen none` | Skip analysis screens 1-4, show only execution (Screen 5) |

**Screen 5 (Execution):** order plan display, live confirmation prompt, per-leg results with slippage/latency metrics, auto-cancelled orders, execution timestamps.

**Decision log:** `execution` block now includes `started_at`, `finished_at`, per-fill `slippage_pct`/`latency_ms`/`timestamp`, `cancelled_orders`, `max_slippage_pct`, `timeout_seconds`, `quantity_override`.

### `README.md` — Updated documentation

- Exchange integration architecture (Synth → pipeline → quotes → routing → execution → lifecycle → safety)
- All CLI flags and env vars documented
- Test coverage description updated

## Testing

- [x] Tested against Synth API
- [x] Manually tested
- [x] Tests added/updated

### Test breakdown

**`test_executor.py`** — 54 tests:
- `TestInstrumentNames` (7) — Deribit/Aevo name builders, roundtrip parsing, edge cases
- `TestBuildPlan` (6) — single/multi-leg, exchange override, aevo names, auto-route, estimated cost
- `TestValidatePlan` (5) — valid plan, empty orders, zero price, zero quantity, empty instrument
- `TestDryRunExecutor` (12) — authenticate, buy/sell fills, missing strike, no-match fallback, **get_order_status, status not found, cancel_order, cancel not found, timestamp on result, slippage tracked**
- `TestExecuteFlow` (7) — single/multi-leg, net cost, auto-routing factory, **summary message, report timestamps**
- `TestGetExecutor` (5) — dry-run, dry-run ignores exchange, missing creds (Deribit/Aevo), unknown exchange
- `TestSlippage` (8) — buy worse/better, sell worse/better, zero expected, **slippage protection rejects, slippage protection allows**
- `TestQuantityOverride` (3) — **default quantity, override applied (single-leg), override applied (multi-leg)**

**`test_executor_e2e.py`** — 8 tests:
- Full pipeline → dry-run execution
- Guardrail: refuse live when no-trade, allow with force, allow dry-run, allow when no guardrail
- **Multi-leg execution pipeline** (spread → rank → build → dry-run → verify all legs + timestamps)
- **Non-crypto symbol skips execution** (SPY returns no exchange quotes)
- **Slippage protection E2E** (full pipeline with slippage guard → all pass)

**Verify:** `python3 -m pytest tools/options-gps/tests/ -v` → **178 passed** (119 existing + 59 new).

## Edge Cases Handled

| # | Scenario | Behavior |
|---|----------|----------|
| E1 | No API credentials | `get_executor` raises with clear env var message |
| E2 | Non-crypto symbol + execute | Exit 1: "Execution only supported for crypto assets" |
| E3 | No exchange quotes available | Exit 1: "exchange data not available" |
| E4 | Multi-leg partial failure | Auto-cancel filled legs; report which were cancelled |
| E5 | Instrument names per exchange | Deribit: `BTC-27MAR26-71000-C`; Aevo: `BTC-71000-C` |
| E6 | No-trade guardrail active | Refuse live execution unless `--force`; dry-run always allowed |
| E7 | Dry-run mode | No HTTP calls to exchange; simulates from quote data |
| E8 | Default CLI (no execute flags) | Unchanged — analysis screens only |
| E9 | Slippage exceeds threshold | Order rejected + cancelled; partial fills unwound |
| E10 | Order timeout | Polls status; cancels if still open after deadline |
| E11 | Negative sleep on monitor deadline | Clamped to 0 — no `ValueError` crash |
| E12 | Quantity override = 0 | Uses strategy default leg quantities |

## Environment Variables (live execution)

| Variable | Purpose |
|----------|---------|
| `DERIBIT_CLIENT_ID` / `DERIBIT_CLIENT_SECRET` | Deribit API credentials |
| `DERIBIT_TESTNET=1` | Use Deribit testnet |
| `AEVO_API_KEY` / `AEVO_API_SECRET` | Aevo REST API credentials |
| `AEVO_SIGNING_KEY` | Ethereum private key for EIP-712 order signing |
| `AEVO_WALLET_ADDRESS` | Maker wallet address (Ethereum) |
| `AEVO_TESTNET=1` | Use Aevo testnet |

## Checklist

- [x] Code follows project style guidelines
- [x] Self-review completed
- [x] Changes are documented (if applicable)

## Demo Video

https://screenrec.com/share/xM8Ibk2COs