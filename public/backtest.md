# Spread Scanner Backtest

_31 tickers · 5y history · horizon 10 trading days · 36,839 signal-bars_

The honest test isn't "do high scores move more in absolute %" — the score
deliberately selects **low-volatility** names, which always move less in raw
terms. The squeeze thesis is about **expansion**: does the move break out beyond
the stock's *own* compressed expected band? That's the **Expand** column
(realized ÷ expected move) and **Broke band** (% exceeding ±1σ).

## By Setup Score

| Score bucket | bars | avg \|move\| | Expand (×) | Broke band |
|---|---|---|---|---|
| ≥ 60 (coiled) | 7,011 | 6.5% | 1.18× | 47% |
| 30 – 60 | 14,899 | 6.4% | 0.96× | 37% |
| < 30 | 14,929 | 7.0% | 0.73× | 25% |

**✅ coiled names expand beyond their own band more often — the squeeze thesis holds.**
Coiled bars broke their ±1σ band **47%** of the time vs **25%** for calm
bars (Δ +21 pts). In raw absolute size the buckets barely differ
(6.5% vs 7.0%, Δ -0.5 pts; score↔|move| r = -0.04) — as expected,
since the score targets quiet names.

## Squeeze on vs off

| State | bars | avg \|move\| | Expand (×) | Broke band |
|---|---|---|---|---|
| squeeze ON | 2,558 | 6.8% | 1.09× | 44% |
| squeeze OFF | 34,281 | 6.6% | 0.89× | 33% |

## Expected-move calibration

Across all bars the realized move landed inside the ±1σ band **66%** of the
time (theory ≈ 68%). Bands are well-calibrated.

> Overlapping forward windows make these observations autocorrelated, so treat
> the percentages as descriptive, not independent-sample statistics. The score
> flags *where* a relative expansion is more likely — never its direction. Past
> behaviour does not guarantee future results.
