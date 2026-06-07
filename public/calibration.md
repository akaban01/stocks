# Setup-Score Calibration

Weights set ∝ each feature's **exceed-rate lift** (how much more often the band
breaks at the favorable end of the feature vs. the unfavorable end), measured on
a **train** split and validated **out-of-sample** on a held-out **test** split.

_train: 24,854 bars (≤ 2024-12-18) · test: 10,650 bars (after)_

## Measured lift (train)

| Feature | exceed-rate lift | → weight |
|---|---|---|
| compression (low BB bandwidth %ile) | +12.8 pts | 29% |
| vol room (low HV %ile) | +21.4 pts | 48% |
| squeeze (on vs off) | +10.1 pts | 23% |

## Out-of-sample check (test) — band-break rate, high-score vs low-score

| Weights | score ≥ 60 | score < 30 | separation |
|---|---|---|---|
| heuristic (35/20/45) | 44% | 30% | +14 pts |
| **calibrated (29%/48%/23%)** | 47% | 27% | **+19 pts** |

**✅ calibrated weights hold up out-of-sample.**

> Weights are baked into `scanner.SCORE_WEIGHTS`. Re-run `python calibrate.py`
> after changing the universe or horizon. Past behaviour ≠ future results.
