# Venth

Tools and utilities built on top of [Synth](https://www.synthdata.co/) (SN50) API

This project is developed using [Gittensor](https://subnetalpha.ai/subnet/gittensor/), the Bittensor subnet that incentivizes open-source contributions.

## About

Venth is a collection of developer tools that extend and integrate with the Synth subnet's forecasting capabilities. The project is being built as a submission to the [Synth Hackathon](https://dashboard.synthdata.co/hackathon/).

## Getting Started

**You do NOT need a Synth API key to build a tool.** The repo includes mock data from every Synth endpoint and a client wrapper that automatically loads it when no API key is present.

### 1. Fork & Clone

```bash
git clone https://github.com/YOUR_USERNAME/venth.git
cd venth
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Start Building

```python
from synth_client import SynthClient

client = SynthClient()  # auto-detects mock mode (no API key needed)

# Get price forecast percentiles for BTC
forecast = client.get_prediction_percentiles("BTC", horizon="24h")
print(forecast["current_price"])
print(forecast["forecast_future"]["percentiles"])

# Get volatility for ETH
vol = client.get_volatility("ETH")
print(vol["forecast_future"]["average_volatility"])

# Get option pricing for SPY
options = client.get_option_pricing("SPY")
print(options["call_options"])
```

When no `SYNTH_API_KEY` environment variable is set, the client automatically loads data from the `mock_data/` directory. When an API key is present, it hits the real Synth API.

### 4. Copy the Template

```bash
cp -r tools/_template tools/my-tool
```

Edit `tools/my-tool/main.py` and `tools/my-tool/README.md` with your tool's logic and documentation.

## Supported Assets

| Asset | Symbol |
|---|---|
| Bitcoin | `BTC` |
| Ethereum | `ETH` |
| Solana | `SOL` |
| Gold | `XAU` |
| S&P 500 | `SPY` |
| NVIDIA | `NVDA` |
| Tesla | `TSLA` |
| Apple | `AAPL` |
| Alphabet | `GOOGL` |

## Available Endpoints

The `SynthClient` wraps all Synth API endpoints:

| Method | Description | Assets | Horizons |
|---|---|---|---|
| `get_prediction_percentiles(asset, horizon)` | Probabilistic price forecasts with percentile distributions | All 9 | `24h`, `1h`\* |
| `get_volatility(asset, horizon)` | Forecasted & realized volatility metrics | All 9 | `24h`, `1h`\* |
| `get_option_pricing(asset)` | Theoretical call/put option prices | All except XAU | — |
| `get_liquidation(asset)` | Liquidation probability at various price changes | All 9 | — |
| `get_lp_bounds(asset)` | Optimal LP ranges with impermanent loss estimates | All 9 | — |
| `get_lp_probabilities(asset)` | Price level probabilities for LP decisions | All 9 | — |
| `get_polymarket_daily()` | Daily up/down: Synth vs Polymarket (BTC) | BTC only | — |
| `get_polymarket_hourly()` | Hourly up/down: Synth vs Polymarket (BTC) | BTC only | — |
| `get_polymarket_range()` | Price range comparison: Synth vs Polymarket | BTC only | — |
| `get_leaderboard(asset, days, limit)` | Top-performing miner rankings | All 9 | — |

\* The `1h` horizon is only available for **crypto assets** (BTC, ETH, SOL) and **XAU**. Equities (SPY, NVDA, TSLA, AAPL, GOOGL) only support the `24h` horizon.

## Project Structure

```
venth/
├── synth_client/             # Dual-mode SDK wrapper (mock + live)
│   ├── __init__.py
│   └── client.py
├── mock_data/                # Real API responses for offline development
│   ├── prediction_percentiles/
│   ├── volatility/
│   ├── option_pricing/
│   ├── liquidation/
│   ├── lp_bounds/
│   ├── lp_probabilities/
│   ├── polymarket/
│   └── leaderboard/
├── tools/
│   ├── _template/            # Starter kit — copy this to begin
│   │   ├── README.md
│   │   ├── main.py
│   │   ├── requirements.txt
│   │   └── tests/
│   └── your-tool/            # Your tool goes here
├── tests/                    # Root test suite for synth_client
├── scripts/                  # Utilities (mock data generator)
└── .github/workflows/        # CI: automated mock tests on every PR
```

## PR Lifecycle

1. **Fork** the repository
2. **Build** your tool using mock data (no API key needed)
3. **Test** locally: `python -m pytest tools/your-tool/tests/ -v`
4. **Push** and open a pull request
5. **CI runs** — automated mock tests verify your tool doesn't crash
6. **Maintainer reviews** — pulls your PR locally & tests with the real API
7. **Merge** 🎉

## Contributing

Contributions are welcome! This project uses Gittensor to reward contributors for meaningful work.

### Hackathon Submission Notice

> **By contributing to this repository, you acknowledge that your contributions will become part of this project and will be included in our submission to the [Synth Hackathon](https://dashboard.synthdata.co/hackathon/). In the event that this submission is selected as a winning entry, your contributions will be part of that winning submission. By submitting a pull request, you agree to these terms.**

To contribute:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/your-feature`)
3. Copy the template: `cp -r tools/_template tools/my-tool`
4. Build your tool using the `SynthClient`
5. Include a 1-page technical document as `tools/my-tool/README.md`
6. Add tests in `tools/my-tool/tests/`
7. Commit your changes and open a pull request

## Links

- [Synth Dashboard](https://dashboard.synthdata.co/)
- [Synth API Docs](https://docs.synthdata.co/)
- [Synth Subnet Repo](https://github.com/mode-network/synth-subnet)
- [Gittensor](https://subnetalpha.ai/subnet/gittensor/)
- [Bittensor](https://bittensor.com/)

## License

This project is licensed under the [MIT License](LICENSE).
