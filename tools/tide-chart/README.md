# Tide Chart

> Interactive Flask dashboard combining Synth probabilistic forecasts with wallet connection and direct trading via gTrade (Gains Network) on Arbitrum One.

## Overview

Tide Chart overlays probabilistic price forecasts into a single comparison view with an interactive web interface. It supports both equities (SPY, NVDA, TSLA, AAPL, GOOGL) on the 24h horizon and crypto/commodities (BTC, ETH, SOL, XAU) on both 1h and 24h horizons. All forecasts are normalized to percentage change for direct comparison across different price levels.

Users can see which asset has the strongest forecast outlook, connect their wallet, and open a leveraged trade — all in one workflow. Synth intelligence → DeFi execution.

The tool provides:
- **Probability cones** - Interactive Plotly chart with 5th-95th percentile bands
- **Probability calculator** - Enter a target price to see the exact probability of an asset reaching it
- **Variable time horizons** - Toggle between Intraday (1H) and Next Day (24H) views
- **Live auto-refresh** - Manual refresh button and configurable 5-minute auto-refresh
- **Ranked metrics table** - Sortable table with directional alignment, skew, and relative benchmarks
- **Wallet connection** - Connect MetaMask or any EIP-1193 wallet directly from the dashboard
- **Direct trading via gTrade** - Open leveraged long/short positions on all Synth API assets (equities, crypto, gold) through Gains Network on Arbitrum One, with USDC collateral and built-in protocol guards

## How It Works

### Forecasting

1. Starts a Flask server serving the interactive dashboard at `http://localhost:5000`
2. Fetches `get_prediction_percentiles` and `get_volatility` for assets in the selected horizon
3. Normalizes time steps from raw price to `% change = (percentile - current_price) / current_price * 100`
4. Computes metrics from the final time step (end of forecast window):
   - **Median Move** - 50th percentile % change
   - **Upside/Downside** - 95th and 5th percentile distances
   - **Directional Skew** - upside minus downside (positive = bullish asymmetry)
   - **Range** - total 5th-to-95th percentile width
   - **Relative to Benchmark** - each metric minus benchmark (SPY for equities, BTC for crypto)
5. Ranks assets by median expected move (table columns are sortable by click)
6. Probability calculator uses linear interpolation across 9 percentile levels to estimate P(price <= target)

### Trading

1. User clicks **Connect Wallet** — triggers MetaMask (or any injected EIP-1193 provider) connection
2. If connected to the wrong chain, automatically prompts to switch to Arbitrum One (chain ID 42161)
3. Dashboard shows USDC balance and chain status badge
4. User clicks **Trade** on any asset row in the rankings table (or selects from the trade form dropdown)
5. Trade form shows pair selection, direction (long/short), leverage (2-150x), collateral, optional TP/SL (%), and max slippage (0.1-5%, default 1%)
6. **Protocol guards** enforce gTrade rules client-side — the Execute button is disabled with a reason label whenever a trade would be rejected (leverage out of range, position size below $1,500 minimum, collateral limits exceeded)
7. Live preview shows computed position size, direction, entry price, TP/SL levels, slippage, and protocol details
8. On **Execute Trade**:
   - Server validates parameters (`/api/gtrade/validate-trade`) — mirrors client-side guards
   - Checks USDC allowance and prompts approval if needed
   - Resolves gTrade pair index via backend API (`/api/gtrade/resolve-pair`)
   - Submits `openTrade` transaction to the gTrade Diamond contract on Arbitrum
   - Toast notification shows transaction hash with Arbiscan link on success
9. If on-chain execution fails, a fallback link opens the trade on gTrade's web app

## Synth Endpoints Used

- `get_prediction_percentiles(asset, horizon)` - Provides time-step probabilistic forecast with 9 percentile levels (0.5% to 99.5%). Used for probability cones, metrics, and the probability calculator.
- `get_volatility(asset, horizon)` - Provides forecasted average volatility. Displayed in the ranking table as an independent risk measure.

## Usage

```bash
# Install dependencies
pip install -r requirements.txt

# Run the dashboard server (opens browser automatically)
python main.py

# Custom port
TIDE_CHART_PORT=8080 python main.py

# Run tests
python -m pytest tests/ -v
```

## API Endpoints

### Dashboard & Forecasting
- `GET /` - Serves the interactive dashboard HTML
- `GET /api/data?horizon=24h` - Returns chart traces, table rows, and insights as JSON
- `POST /api/probability` - Calculates target price probability (body: `{"asset": "SPY", "target_price": 600, "horizon": "24h"}`)

### Trading (gTrade)
- `GET /api/gtrade/config` - Returns contract addresses, pair mapping, leverage/collateral limits
- `POST /api/gtrade/validate-trade` - Server-side trade parameter validation with per-group protocol limits (body: `{"asset": "SPY", "direction": "long", "leverage": 10, "collateral_usd": 200}`)
- `GET /api/gtrade/resolve-pair?asset=SPY` - Resolves a ticker to its gTrade pair index via the Gains Network backend API
- `GET /api/gtrade/open-trades?address=0x...` - Proxies a user's open positions from the gTrade backend

## Trading Details

- **Protocol:** gTrade (Gains Network) v9 on Arbitrum One
- **Contract:** Diamond proxy at `0xFF162c694eAA571f685030649814282eA457f169`
- **Collateral:** USDC (native, `0xaf88d065e77c8cC2239327C5EDb3A432268e5831`)
- **Tradeable pairs:** All Synth API assets — BTC/USD, ETH/USD, SOL/USD, XAU/USD, SPY/USD, NVDA/USD, TSLA/USD, AAPL/USD, GOOGL/USD
- **Leverage:** 2x - 150x
- **Collateral range:** $5 - $100,000 USDC
- **Order type:** Market orders with configurable slippage (0.1-5%, default 1%)
- **TP/SL:** Optional Take Profit and Stop Loss percentage inputs
- **Minimum position size:** $1,500 (enforced client-side and server-side)
- **Protocol guards:** Execute button disabled with descriptive reason when trade params would be rejected by gTrade
- **Trade buttons:** Per-row Trade buttons on all asset rows in the rankings table
- **Open positions:** Live viewer for active gTrade positions
- **Toast notifications:** Non-blocking status messages for connect, trade, and error events
- **Auto-reconnect:** Silently reconnects wallet on page reload if previously connected
- **Wallet support:** Any EIP-1193 injected provider (MetaMask, Coinbase Wallet, Rabby, etc.)

## Technical Details

- **Language:** Python 3.10+
- **Dependencies:** plotly, flask, requests
- **Frontend:** ethers.js v6 (CDN), Plotly (CDN)
- **Equities (24h only):** SPY, NVDA, TSLA, AAPL, GOOGL
- **Crypto + Commodities (1h & 24h):** BTC, ETH, SOL, XAU
- **Output:** Flask web server with Plotly CDN (requires internet for fonts/plotly/ethers)
- **Mock Mode:** Works without API key using bundled mock data (trading requires a wallet + Arbitrum)
