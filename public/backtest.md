# Spread Scanner Backtest

_30 tickers · 5y history · horizon 10 trading days · 35,476 signal-bars_

The honest test isn't "do high scores move more in absolute %" — the score
deliberately selects **low-volatility** names, which always move less in raw
terms. The squeeze thesis is about **expansion**: does the move break out beyond
the stock's *own* compressed expected band? That's the **Expand** column
(realized ÷ expected move) and **Broke band** (% exceeding ±1σ).

## By Setup Score

| Score bucket | bars | avg \|move\| | Expand (×) | Broke band |
|---|---|---|---|---|
| ≥ 60 (coiled) | 6,770 | 5.4% | 1.17× | 47% |
| 30 – 60 | 14,330 | 5.3% | 0.96× | 38% |
| < 30 | 14,376 | 5.7% | 0.74× | 26% |

**✅ coiled names expand beyond their own band more often — the squeeze thesis holds.**
Coiled bars broke their ±1σ band **47%** of the time vs **26%** for calm
bars (Δ +21 pts). In raw absolute size the buckets barely differ
(5.4% vs 5.7%, Δ -0.3 pts; score↔|move| r = -0.03) — as expected,
since the score targets quiet names.

## Squeeze on vs off

| State | bars | avg \|move\| | Expand (×) | Broke band |
|---|---|---|---|---|
| squeeze ON | 2,824 | 5.3% | 1.09× | 44% |
| squeeze OFF | 32,652 | 5.5% | 0.90× | 34% |

## Expected-move calibration

Across all bars the realized move landed inside the ±1σ band **65%** of the
time (theory ≈ 68%). Bands are well-calibrated.

> Overlapping forward windows make these observations autocorrelated, so treat
> the percentages as descriptive, not independent-sample statistics. The score
> flags *where* a relative expansion is more likely — never its direction. Past
> behaviour does not guarantee future results.
