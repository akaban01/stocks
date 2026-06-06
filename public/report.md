# Short-Term Spread Scanner

_Last updated: **2026-06-06 20:02 UTC** · horizon: **10 trading days (~2 weeks)** · 31 tickers scanned_

Ranked by **Setup Score** — how coiled each name is right now. A high score means
volatility is compressed and a move is loading; the **±** column and the 1σ
targets show how far price could travel **up or down** over the horizon (≈68%
of the time within ±1σ, ≈95% within ±2σ). Direction is a weak hint only — this
is a *both-ways* (straddle-style) setup tool.

| # | Ticker | Price | Score | Squeeze | ±10d | Down (1σ) | Up (1σ) | Lean | HV% |
|---|---|---|---|---|---|---|---|---|---|
| 1 | META | 593.00 | **66** | 🔒 1d | 7.2% | 550.33 | 635.67 | • Neut | 36 |
| 2 | UBER | 70.71 | **44** | — | 5.4% | 66.89 | 74.53 | ▼ Bear | 27 |
| 3 | AAPL | 307.34 | **36** | — | 3.6% | 296.29 | 318.39 | ▲ Bull | 18 |
| 4 | CVX | 187.31 | **34** | — | 4.9% | 178.15 | 196.47 | • Neut | 25 |
| 5 | TXN | 285.06 | **31** | — | 9.5% | 257.96 | 312.16 | ▼ Bear | 48 |
| 6 | INTC | 99.17 | **28** | — | 16.8% | 82.47 | 115.87 | ▼ Bear | 84 |
| 7 | GOOGL | 368.53 | **24** | — | 6.0% | 346.60 | 390.46 | ▼ Bear | 30 |
| 8 | TSLA | 391.00 | **23** | — | 9.3% | 354.71 | 427.29 | ▼ Bear | 47 |
| 9 | AMZN | 246.03 | **23** | — | 5.4% | 232.78 | 259.28 | ▼ Bear | 27 |
| 10 | AMD | 466.38 | **22** | — | 15.4% | 394.46 | 538.30 | • Neut | 77 |
| 11 | XOM | 149.92 | **22** | — | 6.3% | 140.47 | 159.37 | ▼ Bear | 32 |
| 12 | NVDA | 205.10 | **18** | — | 9.0% | 186.68 | 223.52 | ▼ Bear | 45 |
| 13 | LRCX | 303.28 | **18** | — | 12.0% | 266.99 | 339.57 | • Neut | 60 |
| 14 | SHOP | 109.54 | **18** | — | 12.3% | 96.07 | 123.01 | ▲ Bull | 62 |
| 15 | SPUS | 56.64 | **18** | — | 3.8% | 54.51 | 58.77 | • Neut | 19 |
| 16 | ADBE | 251.44 | **17** | — | 9.5% | 227.44 | 275.44 | ▲ Bull | 48 |
| 17 | JNJ | 232.77 | **17** | — | 4.0% | 223.49 | 242.05 | ▲ Bull | 20 |
| 18 | MSFT | 416.67 | **17** | — | 7.0% | 387.38 | 445.96 | • Neut | 35 |
| 19 | PLTR | 135.53 | **16** | — | 11.9% | 119.43 | 151.63 | • Neut | 60 |
| 20 | HLAL | 70.53 | **15** | — | 3.5% | 68.04 | 73.02 | • Neut | 18 |
| 21 | AMAT | 453.01 | **12** | — | 12.0% | 398.81 | 507.21 | ▲ Bull | 60 |
| 22 | LLY | 1,131.42 | **12** | — | 6.6% | 1,057.11 | 1,205.73 | ▲ Bull | 33 |
| 23 | AVGO | 385.73 | **11** | — | 13.7% | 332.91 | 438.55 | • Neut | 69 |
| 24 | CRM | 185.66 | **9** | — | 11.6% | 164.04 | 207.28 | ▲ Bull | 58 |
| 25 | SMCI | 41.64 | **9** | — | 17.9% | 34.17 | 49.11 | ▲ Bull | 90 |
| 26 | QCOM | 215.94 | **7** | — | 20.6% | 171.54 | 260.34 | • Neut | 103 |
| 27 | MU | 864.01 | **6** | — | 22.9% | 665.94 | 1,062.08 | ▲ Bull | 115 |
| 28 | NOW | 112.45 | **3** | — | 17.1% | 93.26 | 131.64 | ▲ Bull | 86 |
| 29 | ORCL | 213.68 | **2** | — | 14.9% | 181.86 | 245.50 | ▲ Bull | 75 |
| 30 | ARM | 342.93 | **1** | — | 23.3% | 262.92 | 422.94 | ▲ Bull | 117 |


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
