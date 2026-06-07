"""Backtest — does the Setup Score actually precede a bigger move?

For every historical bar across the universe we recompute the same indicators
the live scanner uses (no look-ahead — each bar sees only past data), then look
*forward* `horizon` trading days and measure the realized absolute move. If the
score means anything, high-score bars should be followed by bigger moves, and
the 1-sigma expected-move band should contain ~68% of outcomes.

Run:  python backtest.py            (uses config.yaml universe + params)
      python backtest.py --years 5 --tickers AAPL,NVDA,MSFT
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import indicators as ind
from . import scanner


def _consecutive_true(flags: pd.Series) -> pd.Series:
    """Running count of consecutive True values, resetting on False."""
    s = flags.fillna(False).astype(int)
    reset = (s == 0).cumsum()          # new group each time it goes False
    return s.groupby(reset).cumsum()


def _score_series(squeeze_on, squeeze_days, bw_pctile, hv_pctile, weights=None) -> pd.Series:
    """Vectorized scanner._setup_score over a whole series, using the shared
    SCORE_WEIGHTS (or an override, used by the calibrator)."""
    w = weights or scanner.SCORE_WEIGHTS
    floor = scanner.SQUEEZE_FLOOR
    squeeze_signal = np.where(squeeze_on, floor + (1 - floor) * np.minimum(squeeze_days, 15) / 15, 0.0)
    raw = (w["compression"] * (1 - bw_pctile.fillna(0.5))
           + w["vol_room"] * (1 - hv_pctile.fillna(0.5))
           + w["squeeze"] * pd.Series(squeeze_signal, index=bw_pctile.index))
    return (raw * 100).clip(0, 100)


def _per_ticker_records(df: pd.DataFrame, p: dict) -> pd.DataFrame:
    """One row per historical bar: score, squeeze, expected vs realized move."""
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    if len(df) < p["percentile_lookback"] + p["horizon_days"] + 5:
        return pd.DataFrame()

    close = df["Close"]
    _, _, _, bandwidth = ind.bollinger_bands(close, p["bb_length"], p["bb_mult"])
    squeeze = ind.ttm_squeeze(df, p["bb_length"], p["bb_mult"], p["kc_length"], p["kc_mult"]).fillna(False)
    hv = ind.historical_volatility(close, p["vol_lookback"])
    bw_pctile = ind.rolling_percentile(bandwidth, p["percentile_lookback"]).reindex(df.index)
    hv_pctile = ind.rolling_percentile(hv, p["percentile_lookback"]).reindex(df.index)
    squeeze_days = _consecutive_true(squeeze)

    score = _score_series(squeeze, squeeze_days, bw_pctile, hv_pctile)

    # Expected move (1-sigma, %) from trailing daily vol — known at the bar.
    logret = np.log(close / close.shift(1))
    sigma_d = logret.rolling(p["vol_lookback"]).std(ddof=0)
    em_pct = sigma_d * np.sqrt(p["horizon_days"]) * 100

    # Realized absolute move over the FORWARD horizon (the outcome).
    fwd_abs = (close.shift(-p["horizon_days"]) / close - 1).abs() * 100

    out = pd.DataFrame({
        "date": df.index,
        "score": score,
        "squeeze_on": squeeze,
        "squeeze_days": squeeze_days,
        "bw_pctile": bw_pctile,
        "hv_pctile": hv_pctile,
        "em_pct": em_pct,
        "fwd_abs": fwd_abs,
    }).dropna()
    out = out[out["em_pct"] > 0]
    out["within_band"] = out["fwd_abs"] <= out["em_pct"]
    # Expansion = realized move as a multiple of its OWN expected (compressed)
    # band. This is the squeeze thesis, free of cross-sectional vol differences:
    # >1 means the move broke out beyond what the quiet range implied.
    out["expansion"] = out["fwd_abs"] / out["em_pct"]
    out["broke_band"] = ~out["within_band"]
    return out


def run_backtest(data: dict[str, pd.DataFrame], p: dict) -> tuple[pd.DataFrame, dict]:
    """Aggregate per-bar records across the universe and compute summary stats."""
    frames = [_per_ticker_records(df, p) for df in data.values()]
    frames = [f for f in frames if not f.empty]
    recs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if recs.empty:
        return recs, {}

    def bucket_stats(mask) -> dict:
        sub = recs[mask]
        if sub.empty:
            return {"bars": 0, "avg": float("nan"), "median": float("nan"),
                    "cover": float("nan"), "exp": float("nan"), "exceed": float("nan")}
        return {
            "bars": len(sub),
            "avg": sub["fwd_abs"].mean(),
            "median": sub["fwd_abs"].median(),
            "cover": sub["within_band"].mean() * 100,
            "exp": sub["expansion"].mean(),            # avg realized / expected
            "exceed": (~sub["within_band"]).mean() * 100,  # % that broke the band
        }

    stats = {
        "n": len(recs),
        "coverage": recs["within_band"].mean() * 100,
        "corr": recs["score"].corr(recs["fwd_abs"]),
        "high": bucket_stats(recs["score"] >= 60),
        "mid": bucket_stats((recs["score"] >= 30) & (recs["score"] < 60)),
        "low": bucket_stats(recs["score"] < 30),
        "sq_on": bucket_stats(recs["squeeze_on"]),
        "sq_off": bucket_stats(~recs["squeeze_on"]),
    }
    return recs, stats


# Original hand-set heuristic, kept only as the calibration baseline to beat.
HEURISTIC_WEIGHTS = {"compression": 0.35, "vol_room": 0.20, "squeeze": 0.45}


def _exceed_rate(sub: pd.DataFrame) -> float:
    return float(sub["broke_band"].mean()) if len(sub) else float("nan")


def _weights_from_lift(recs: pd.DataFrame) -> tuple[dict, dict]:
    """Weights ∝ each feature's exceed-rate lift (favorable vs unfavorable end)."""
    def lift_continuous(col: str) -> float:
        return _exceed_rate(recs[recs[col] <= 0.30]) - _exceed_rate(recs[recs[col] >= 0.70])

    lift = {
        "compression": max(lift_continuous("bw_pctile"), 0.0),
        "vol_room": max(lift_continuous("hv_pctile"), 0.0),
        "squeeze": max(_exceed_rate(recs[recs["squeeze_on"]])
                       - _exceed_rate(recs[~recs["squeeze_on"]]), 0.0),
    }
    total = sum(lift.values()) or 1.0
    weights = {k: round(v / total, 2) for k, v in lift.items()}
    drift = round(1.0 - sum(weights.values()), 2)   # rounding can drift off 1.0
    if drift:
        biggest = max(weights, key=weights.get)
        weights[biggest] = round(weights[biggest] + drift, 2)
    return weights, lift


def compute_weights(recs: pd.DataFrame) -> dict:
    """Production weights from ALL available history (max signal, no holdout)."""
    return _weights_from_lift(recs)[0]


def calibrate_weights(recs: pd.DataFrame, train_frac: float = 0.7) -> dict:
    """Production weights (full history) + an out-of-sample sanity check: weights
    derived from a TRAIN split are scored on a held-out TEST split to confirm the
    lift-weighting generalizes rather than overfitting."""
    recs = recs.sort_values("date")
    weights, lift = _weights_from_lift(recs)                 # production: full history

    cutoff = recs["date"].quantile(train_frac)
    train, test = recs[recs["date"] <= cutoff], recs[recs["date"] > cutoff]
    train_weights, _ = _weights_from_lift(train)

    def separation(w: dict) -> tuple[float, float, float]:
        score = _score_series(test["squeeze_on"], test["squeeze_days"],
                              test["bw_pctile"], test["hv_pctile"], weights=w)
        hi = float(test["broke_band"][score >= 60].mean()) * 100
        lo = float(test["broke_band"][score < 30].mean()) * 100
        return hi, lo, hi - lo

    return {
        "lift": lift, "weights": weights, "cutoff": cutoff,
        "n": len(recs), "n_train": len(train), "n_test": len(test),
        "sep_heuristic": separation(HEURISTIC_WEIGHTS),
        "sep_calibrated": separation(train_weights),  # train-derived, tested OOS
    }


def format_calibration(c: dict) -> str:
    if not c:
        return "# Score Calibration\n\n_Not enough history to calibrate._\n"
    w, lift = c["weights"], c["lift"]
    sh, sc = c["sep_heuristic"], c["sep_calibrated"]
    improved = sc[2] >= sh[2] - 0.5
    return f"""# Setup-Score Calibration

Weights set ∝ each feature's **exceed-rate lift** (how much more often the band
breaks at the favorable end of the feature vs. the unfavorable end), measured on
a **train** split and validated **out-of-sample** on a held-out **test** split.

_train: {c['n_train']:,} bars (≤ {str(c['cutoff'])[:10]}) · test: {c['n_test']:,} bars (after)_

## Measured lift (train)

| Feature | exceed-rate lift | → weight |
|---|---|---|
| compression (low BB bandwidth %ile) | {lift['compression']*100:+.1f} pts | {w['compression']:.0%} |
| vol room (low HV %ile) | {lift['vol_room']*100:+.1f} pts | {w['vol_room']:.0%} |
| squeeze (on vs off) | {lift['squeeze']*100:+.1f} pts | {w['squeeze']:.0%} |

The production weights above use **all** history. To prove the lift-weighting
generalizes (rather than overfits), we also derive weights from a **train** split
and score them on a held-out **test** split:

| Weights (derived on train) | score ≥ 60 | score < 30 | separation |
|---|---|---|---|
| heuristic (35/20/45) | {sh[0]:.0f}% | {sh[1]:.0f}% | {sh[2]:+.0f} pts |
| **calibrated (lift-weighted)** | {sc[0]:.0f}% | {sc[1]:.0f}% | **{sc[2]:+.0f} pts** |

**{'✅ calibrated weights hold up out-of-sample.' if improved else '⚠️ calibration did not beat the heuristic out-of-sample.'}**

> Written to `weights.json` and loaded by the scanner each run. Regenerated daily
> by the GitHub Action. Past behaviour ≠ future results.
"""


def format_report(stats: dict, p: dict, n_tickers: int, years: int) -> str:
    if not stats:
        return "# Spread Scanner Backtest\n\n_Not enough history to backtest._\n"

    def row(label, s):
        return (f"| {label} | {s['bars']:,} | {s['avg']:.1f}% | {s['exp']:.2f}× "
                f"| {s['exceed']:.0f}% |")

    hi, lo = stats["high"], stats["low"]
    abs_edge = hi["avg"] - lo["avg"]
    exp_edge = hi["exceed"] - lo["exceed"]
    expansion_works = exp_edge > 3  # coiled bars break their band meaningfully more often
    verdict = (
        "✅ coiled names expand beyond their own band more often — the squeeze thesis holds"
        if expansion_works else
        "⚠️ coiled names don't expand more than calm ones — weak/no edge"
    )

    return f"""# Spread Scanner Backtest

_{n_tickers} tickers · {years}y history · horizon {p['horizon_days']} trading days · {stats['n']:,} signal-bars_

The honest test isn't "do high scores move more in absolute %" — the score
deliberately selects **low-volatility** names, which always move less in raw
terms. The squeeze thesis is about **expansion**: does the move break out beyond
the stock's *own* compressed expected band? That's the **Expand** column
(realized ÷ expected move) and **Broke band** (% exceeding ±1σ).

## By Setup Score

| Score bucket | bars | avg \\|move\\| | Expand (×) | Broke band |
|---|---|---|---|---|
{row("≥ 60 (coiled)", stats["high"])}
{row("30 – 60", stats["mid"])}
{row("< 30", stats["low"])}

**{verdict}.**
Coiled bars broke their ±1σ band **{hi['exceed']:.0f}%** of the time vs **{lo['exceed']:.0f}%** for calm
bars (Δ {exp_edge:+.0f} pts). In raw absolute size the buckets barely differ
({hi['avg']:.1f}% vs {lo['avg']:.1f}%, Δ {abs_edge:+.1f} pts; score↔|move| r = {stats['corr']:+.2f}) — as expected,
since the score targets quiet names.

## Squeeze on vs off

| State | bars | avg \\|move\\| | Expand (×) | Broke band |
|---|---|---|---|---|
{row("squeeze ON", stats["sq_on"])}
{row("squeeze OFF", stats["sq_off"])}

## Expected-move calibration

Across all bars the realized move landed inside the ±1σ band **{stats['coverage']:.0f}%** of the
time (theory ≈ 68%). {'Bands are well-calibrated.' if 60 <= stats['coverage'] <= 76 else 'Bands look mis-calibrated — consider tuning vol_lookback.'}

> Overlapping forward windows make these observations autocorrelated, so treat
> the percentages as descriptive, not independent-sample statistics. The score
> flags *where* a relative expansion is more likely — never its direction. Past
> behaviour does not guarantee future results.
"""


def format_html(stats: dict, p: dict, n_tickers: int, years: int) -> str:
    """Self-contained dark-themed validation page for GitHub Pages."""
    if not stats:
        body = "<p>Not enough history to backtest.</p>"
    else:
        def row(label, s):
            return (f"<tr><td>{label}</td><td class='n'>{s['bars']:,}</td>"
                    f"<td class='n'>{s['avg']:.1f}%</td><td class='n'>{s['exp']:.2f}×</td>"
                    f"<td class='n'>{s['exceed']:.0f}%</td></tr>")
        hi, lo = stats["high"], stats["low"]
        exp_edge = hi["exceed"] - lo["exceed"]
        ok = exp_edge > 3
        verdict = ("✅ coiled names expand beyond their own band more often — the squeeze thesis holds"
                   if ok else "⚠️ coiled names don't expand more than calm ones — weak/no edge")
        body = f"""
  <p class="lede">The honest test isn't "do high scores move more in absolute %" — the score
  deliberately picks <b>low-volatility</b> names, which always move less in raw terms. The thesis is
  <b>expansion</b>: does the move break out beyond the stock's <i>own</i> compressed band?
  That's <b>Expand</b> (realized ÷ expected) and <b>Broke band</b> (% exceeding ±1σ).</p>
  <h2>By Setup Score</h2>
  <table><thead><tr><th>Score bucket</th><th>bars</th><th>avg |move|</th><th>Expand</th><th>Broke band</th></tr></thead>
  <tbody>{row("≥ 60 (coiled)", hi)}{row("30 – 60", stats["mid"])}{row("&lt; 30", lo)}</tbody></table>
  <p class="{'good' if ok else 'warn'}"><b>{verdict}.</b> Coiled bars broke their ±1σ band
  <b>{hi['exceed']:.0f}%</b> of the time vs <b>{lo['exceed']:.0f}%</b> for calm bars (Δ {exp_edge:+.0f} pts).
  Raw absolute size barely differs ({hi['avg']:.1f}% vs {lo['avg']:.1f}%; r = {stats['corr']:+.2f}).</p>
  <h2>Squeeze on vs off</h2>
  <table><thead><tr><th>State</th><th>bars</th><th>avg |move|</th><th>Expand</th><th>Broke band</th></tr></thead>
  <tbody>{row("squeeze ON", stats["sq_on"])}{row("squeeze OFF", stats["sq_off"])}</tbody></table>
  <h2>Calibration</h2>
  <p>Realized move landed inside the ±1σ band <b>{stats['coverage']:.0f}%</b> of the time (theory ≈ 68%).</p>
  <p class="fine">Overlapping forward windows make these autocorrelated — descriptive, not
  independent-sample stats. The score flags <i>where</i> a relative expansion is likelier, never its
  direction. Past behaviour does not guarantee future results.</p>"""

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Spread Scanner — Backtest</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0d1117; color:#e6edf3; font:15px/1.6 -apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:820px; margin:0 auto; padding:28px 18px 60px; }}
  h1 {{ font-size:1.5rem; margin:0 0 4px; }} h2 {{ font-size:1.05rem; margin:26px 0 10px; }}
  .meta {{ color:#8b949e; font-size:.85rem; margin-bottom:18px; }}
  .lede {{ background:#161b22; border:1px solid #30363d; border-radius:10px; padding:12px 16px; }}
  table {{ width:100%; border-collapse:collapse; font-size:.92rem; margin:6px 0; }}
  th,td {{ padding:8px 10px; border-bottom:1px solid #21262d; text-align:left; }}
  th {{ color:#8b949e; font-size:.78rem; text-transform:uppercase; }}
  td.n {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .good {{ color:#5fd07a; }} .warn {{ color:#f0c992; }} .fine {{ color:#8b949e; font-size:.82rem; }}
  a {{ color:#58a6ff; }}
</style></head><body><div class="wrap">
  <h1>🔬 Setup-Score Backtest</h1>
  <div class="meta">{n_tickers} tickers · {years}y history · horizon {p['horizon_days']} trading days
    · {stats.get('n', 0):,} signal-bars · <a href="index.html">← back to scanner</a></div>
  {body}
</div></body></html>"""
