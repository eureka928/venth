# Options GPS

Turn a trader's view into one clear options decision. Inputs: **symbol**, **market view** (bullish / bearish / neutral / vol), **risk tolerance** (low / medium / high). Output: three strategy cards — **Best Match**, **Safer Alternative**, **Higher Upside** — with chance of profit, max loss, and invalidation.

## What it does

- **Screen 1 (View Setup):** User picks symbol, view, and risk; system summarizes.
- **Market Context:** Shows current price, forecast fusion state, confidence, volatility metrics, and (for vol view) implied vol vs Synth vol comparison with long/short vol bias.
- **Screen 2 (Top Plays):** Three ranked cards: Best Match (highest score for view), Safer Alternative (higher win probability), Higher Upside (higher expected payoff). Each shows why it fits, chance of profit, max loss, "Review again at" time.
- **Screen 3 (Why This Works):** Distribution view and plain-English explanation for the best match (Synth 1h + 24h fusion state, required market behavior).
- **Screen 4 (If Wrong):** Exit rule, convert/roll rule, time-based reassessment rule.
- **Screen 5 (Execution):** When `--execute` or `--dry-run` is used, shows order plan, optional confirmation (live only), and per-leg fill results with net cost.

**Guardrails:** No-trade state when confidence is low, signals conflict (e.g. 1h vs 24h countermove), volatility is very high (directional views), or no vol edge exists (vol view with similar Synth/market IV).

## How it works

1. **Data:** Synth forecasts (1h and 24h prediction percentiles), option pricing, and volatility via `SynthClient`.
2. **Forecast Fusion:** Compares 1h and 24h median vs current price → **Aligned** (both same direction), **Countermove** (opposite), or **Unclear**.
3. **Implied Volatility Estimation (vol view):** Derives market IV from ATM option premiums using the Brenner-Subrahmanyam approximation: `IV ≈ premium × √(2π) / (price × √T)`. Parses actual time-to-expiry from option data; falls back to 1-day if unavailable. Compares against Synth's forecasted volatility to determine a **vol bias**: `long_vol` (Synth > IV by >15%), `short_vol` (Synth < IV by >15%), or `neutral_vol` (no edge).
4. **Strategy Generator:** Builds candidates from option strikes based on view and risk:
   - **Bullish:** Long call, call debit spread, bull put credit spread.
   - **Bearish:** Long put, put debit spread, bear call credit spread.
   - **Neutral:** Iron condor, long call butterfly, ATM call/put.
   - **Vol (long vol bias):** Long straddle (buy ATM call + put), long strangle (buy OTM call + put).
   - **Vol (short vol bias):** Short straddle (sell ATM call + put, high risk only), short strangle (sell OTM call + put, medium/high risk), iron condor (defined-risk short vol).
5. **Payoff + Probability Engine:** Uses Synth percentile distribution (CDF-weighted) at horizon to compute probability of profit (PoP) and expected value (EV) for each strategy. PnL formulas cover all strategy types including straddles and strangles.
6. **Ranking Engine:** Scores with `fit_to_view + pop + expected_return - tail_penalty`; weighting shifts by risk (low → more PoP, high → more EV). For vol view, vol bias adjusts view fit: long_vol boosts long straddle/strangle scores, short_vol boosts iron condor/short straddle scores. Fusion bonus is skipped for vol view (direction-agnostic). **Market Line Shopping** (crypto only): compares Synth fair value against Deribit/Aevo exchange prices; strategies where the market price is cheaper than fair get an additive score bonus (clamped ±0.15). Picks Best Match, Safer Alternative, Higher Upside.
7. **Guardrails:** Filters no-trade when fusion is countermove/unclear with directional view, volatility exceeds threshold (directional views), confidence is too low, or vol bias is neutral (vol view — no exploitable divergence between Synth and market IV).
8. **Risk Management:** Each strategy type has a specific risk plan (invalidation trigger, adjustment/reroute rule, review schedule). Short straddle/strangle are labeled "unlimited risk" with hard stops at 2x credit loss; they are risk-gated (high-only for short straddle, medium+ for short strangle).

## Exchange integration architecture

Data flow for crypto assets (BTC, ETH, SOL):

1. **Synth** → forecast percentiles, option pricing, volatility (via `SynthClient`).
2. **Pipeline** → strategy generation, payoff/EV, ranking (with optional exchange divergence bonus).
3. **Exchange (read)** → `exchange.py` fetches live or mock quotes from Deribit and Aevo; `leg_divergences()` computes per-leg best venue and price (lowest ask for BUY, highest bid for SELL).
4. **Execution** → `executor.py` builds an `ExecutionPlan` from the chosen strategy card, resolves instrument names per exchange (Deribit: `BTC-DDMonYY-STRIKE-C|P`; Aevo: `BTC-STRIKE-C|P`), and either simulates (dry-run) or submits orders. Deribit uses REST with Bearer token auth; Aevo uses REST with HMAC-SHA256 signing. When `--exchange` is not set, each leg is auto-routed to its best venue (per `leg_divergences`).

Credentials are read from the environment (no secrets in code). Dry-run requires no credentials.

## Execution

Execution is supported only for **crypto assets** (BTC, ETH, SOL). Use `--execute` to submit live orders or `--dry-run` to simulate without placing orders. Non-crypto symbols exit with an error.

**CLI flags:**

| Flag | Description |
|------|-------------|
| `--execute [best\|safer\|upside]` | Submit live orders for the chosen card (default: `best`). |
| `--dry-run [best\|safer\|upside]` | Simulate execution using exchange quotes; no API keys, no real orders. |
| `--exchange deribit\|aevo` | Force all legs to one exchange. Default: auto-route each leg to best venue. |
| `--force` | Override no-trade guardrail for live execution. |
| `--size N` | Position size multiplier (scales all leg quantities and max loss). |
| `--max-slippage PCT` | Halt execution if any fill exceeds this slippage percentage. |
| `--max-loss USD` | Pre-trade risk check: reject if strategy max loss exceeds this budget. |
| `--timeout SECS` | Order monitoring timeout in seconds (default: 30). |
| `--log-file PATH` | Save full execution report JSON to file (audit trail). |

**Exchange protocols:**

- **Deribit**: JSON-RPC 2.0 over POST with Bearer token auth. Uses `contracts` parameter for unambiguous option sizing. Converts USD prices to BTC via index price lookup (`_get_index_price`), snaps to live order book best bid/ask (`_get_book_price`), and aligns to tick size (0.0005 BTC). Retries on transient errors (429, 502, 503, timeout) with exponential backoff.
- **Aevo**: REST API with per-request HMAC-SHA256 signing (`AEVO-KEY`, `AEVO-TIMESTAMP`, `AEVO-SIGNATURE` headers). Retries on transient errors.

**Auto-routing**: When `--exchange` is not set, each leg is auto-routed to its best venue via `leg_divergences()`. For live execution, a per-exchange executor factory creates and caches separate authenticated sessions.

**Safety features:**
- Guardrail blocks live execution when no-trade reason is active (override with `--force`). Dry-run is always allowed.
- Slippage protection halts multi-leg execution and warns about filled legs needing manual close.
- Max loss budget rejects plans before any orders are sent.
- Partial fill detection warns about filled legs on failure.
- Execution log JSON includes timestamp, per-fill slippage, and complete order/result audit.

**Environment variables (live execution only):**

| Variable | Purpose |
|----------|---------|
| `DERIBIT_CLIENT_ID` / `DERIBIT_CLIENT_SECRET` | Deribit API credentials |
| `DERIBIT_TESTNET=1` | Use Deribit testnet |
| `AEVO_API_KEY` / `AEVO_API_SECRET` | Aevo API credentials |
| `AEVO_TESTNET=1` | Use Aevo testnet |

None needed for `--dry-run`.

## Synth API usage

- **`get_prediction_percentiles(asset, horizon)`** — 1h and 24h probabilistic price forecasts; used for fusion state and for payoff/EV (outcome distribution at expiry).
- **`get_option_pricing(asset)`** — Theoretical call/put prices by strike; used to build strategies, costs, and to derive market implied volatility (vol view).
- **`get_volatility(asset, horizon)`** — Forecast and realized volatility; used in guardrails (no trade when volatility very high) and as the Synth vol signal for vol view comparison against market IV.

## Usage

```bash
# From repo root
pip install -r tools/options-gps/requirements.txt
python tools/options-gps/main.py

# Vol view directly from CLI
python tools/options-gps/main.py --symbol BTC --view vol --risk medium --no-prompt

# Simulate execution — best match (default), no API keys needed
python tools/options-gps/main.py --symbol BTC --view bullish --risk medium --dry-run --no-prompt

# Dry-run the safer alternative instead
python tools/options-gps/main.py --symbol BTC --view bullish --risk medium --dry-run safer --no-prompt

# Execute best match on exchange (requires credentials)
python tools/options-gps/main.py --symbol BTC --view bullish --risk medium --execute --no-prompt

# Execute the higher-upside card on Deribit, 3x size, with risk controls
python tools/options-gps/main.py --symbol ETH --view bearish --risk high \
  --execute upside --exchange deribit --size 3 --max-slippage 2.0 --max-loss 5000 \
  --log-file /tmp/eth_exec.json --no-prompt

# Force execution despite no-trade guardrail
python tools/options-gps/main.py --symbol BTC --view bullish --risk medium --execute --force --no-prompt
```

Prompts: symbol (default BTC), view (bullish/bearish/neutral/vol), risk (low/medium/high). Uses mock data when no `SYNTH_API_KEY` is set.

## Tests

From repo root: `python -m pytest tools/options-gps/tests/ -v`. No API key required (mock data).

Test coverage includes: forecast fusion, strategy generation (all views including vol), PnL calculations for all strategy types, CDF-weighted PoP/EV, ranking with vol bias, vol-specific guardrails, IV estimation, vol comparison, risk plans, hard filters, exchange data fetching/parsing, divergence computation, line shopping ranking integration, execution (instrument names, plan build/validate, dry-run executor, execution flow, auto-routing factory, slippage computation/protection, max loss budget validation, size multiplier, execution log save/load, retry logic for transient errors, per-exchange factory routing), full-pipeline-to-dry-run E2E, and end-to-end scripted tests.
