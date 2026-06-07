# Short-Term Spread Scanner

_Last updated: **2026-06-07 01:01 UTC** · horizon: **10 trading days (~2 weeks)** · 30 tickers scanned_

Ranked by **Setup Score** — how coiled each name is right now. A high score means
volatility is compressed and a move is loading; the **±** column and the 1σ
targets show how far price could travel **up or down** over the horizon (≈68%
of the time within ±1σ, ≈95% within ±2σ). Direction is a weak hint only — this
is a *both-ways* (straddle-style) setup tool.

| # | Ticker | Price | Score | Squeeze | ±10d | Down (1σ) | Up (1σ) | Lean | HV% | Debt% | Cash% |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | META | 593.00 | **66** | 🔒 1d | 7.2% | 550.33 | 635.67 | • Neut | 36 | 6% | 5% |
| 2 | PG | 146.54 | **62** | 🔒 2d | 4.8% | 139.52 | 153.56 | • Neut | 24 | 11% | 4% |
| 3 | HD | 310.78 | **38** | — | 4.5% | 296.69 | 324.87 | ▲ Bull | 23 | 21% | 1% |
| 4 | GEV | 933.61 | **37** | — | 7.3% | 865.74 | 1,001.48 | ▼ Bear | 36 | 1% | 4% |
| 5 | AAPL | 307.34 | **36** | — | 3.6% | 296.29 | 318.39 | ▲ Bull | 18 | 2% | 2% |
| 6 | CVX | 187.31 | **34** | — | 4.9% | 178.15 | 196.47 | • Neut | 25 | 12% | 1% |
| 7 | TXN | 285.06 | **31** | — | 9.5% | 257.96 | 312.16 | ▼ Bear | 48 | 5% | 2% |
| 8 | INTC | 99.17 | **28** | — | 16.8% | 82.47 | 115.87 | ▼ Bear | 84 | 9% | 7% |
| 9 | GOOG | 365.76 | **24** | — | 5.8% | 344.59 | 386.93 | ▼ Bear | 29 | 2% | 3% |
| 10 | GOOGL | 368.53 | **24** | — | 6.0% | 346.60 | 390.46 | ▼ Bear | 30 | 2% | 3% |
| 11 | TSLA | 391.00 | **23** | — | 9.3% | 354.71 | 427.29 | ▼ Bear | 47 | 1% | 3% |
| 12 | AMD | 466.38 | **22** | — | 15.4% | 394.46 | 538.30 | • Neut | 77 | 1% | 2% |
| 13 | ABBV | 227.23 | **22** | — | 4.1% | 217.96 | 236.50 | ▲ Bull | 20 | 18% | 2% |
| 14 | XOM | 149.92 | **22** | — | 6.3% | 140.47 | 159.37 | ▼ Bear | 32 | 8% | 1% |
| 15 | NVDA | 205.10 | **18** | — | 9.0% | 186.68 | 223.52 | ▼ Bear | 45 | 0% | 1% |
| 16 | LRCX | 303.28 | **18** | — | 12.0% | 266.99 | 339.57 | • Neut | 60 | 1% | 1% |
| 17 | KLAC | 1,929.20 | **17** | — | 12.2% | 1,693.39 | 2,165.01 | ▲ Bull | 61 | 2% | 2% |
| 18 | KO | 79.48 | **17** | — | 4.0% | 76.29 | 82.67 | ▼ Bear | 20 | 13% | 4% |
| 19 | MSFT | 416.67 | **17** | — | 7.0% | 387.38 | 445.96 | • Neut | 35 | 4% | 3% |
| 20 | JNJ | 232.77 | **17** | — | 4.0% | 223.49 | 242.05 | ▲ Bull | 20 | 10% | 4% |
| 21 | AMAT | 453.01 | **12** | — | 12.0% | 398.81 | 507.21 | ▲ Bull | 60 | 2% | 2% |
| 22 | LLY | 1,131.42 | **12** | — | 6.6% | 1,057.11 | 1,205.73 | ▲ Bull | 33 | 4% | 1% |
| 23 | MRK | 120.79 | **12** | — | 6.4% | 113.01 | 128.57 | ▲ Bull | 32 | 16% | 2% |
| 24 | AVGO | 385.73 | **11** | — | 13.7% | 332.91 | 438.55 | • Neut | 69 | 4% | 1% |
| 25 | QCOM | 215.94 | **7** | — | 20.6% | 171.54 | 260.34 | • Neut | 103 | 7% | 4% |
| 26 | MU | 864.01 | **6** | — | 22.9% | 665.94 | 1,062.08 | ▲ Bull | 115 | 1% | 1% |
| 27 | CSCO | 121.64 | **4** | — | 11.7% | 107.44 | 135.84 | ▲ Bull | 59 | 7% | 3% |
| 28 | ORCL | 213.68 | **2** | — | 14.9% | 181.86 | 245.50 | ▲ Bull | 75 | 26% | 6% |
| 29 | IBM | 284.84 | **0** | — | 15.0% | 242.20 | 327.48 | ▲ Bull | 75 | 26% | 4% |
| 30 | MRVL | 263.47 | **0** | — | 26.2% | 194.54 | 332.40 | ▲ Bull | 131 | 2% | 2% |


<details>
<summary>How to read this</summary>

- **Score** — 0–100. Compression (low Bollinger bandwidth) + room to expand
  (low historical-vol percentile) + active TTM squeeze. Higher = more coiled.
- **Squeeze** — 🔒 means Bollinger Bands are inside the Keltner Channels (the
  TTM squeeze is *on*); the number is how many days it has been building.
- **±10d** — 1-sigma expected move over the horizon, as a % of price.
- **Down/Up (1σ)** — price targets one standard deviation either side.
- **Lean** — faint directional hint from squeeze momentum; do not over-trust it.
- **HV%** — annualized historical volatility.

Full machine-readable output: [`signals.csv`](signals.csv) · [`signals.json`](signals.json)
</details>

> ⚠️ Educational tool, **not financial advice**. Expected-move bands are
> statistical estimates from past volatility, not predictions.
