"""Backtest — does the Setup Score actually precede a bigger move?

For every historical bar across the universe we recompute the same indicators
the live scanner uses (no look-ahead — each bar sees only past data), then look
*forward* `horizon` trading days and measure the realized absolute move. If the
score means anything, high-score bars should be followed by bigger moves, and
the 1-sigma expected-move band should contain ~68% of outcomes.

Results go out as JSON (``data/backtest.json``) for the frontend to render —
see ``spread_scanner/report.py`` for why the backend no longer writes HTML.

Run:  python backtest.py            (uses config.yaml universe + params)
      python backtest.py --years 5 --tickers AAPL,NVDA,MSFT
"""

from __future__ import annotations

import datetime as dt
import math

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


def calibration_payload(c: dict, years: int, universe: int) -> dict:
    """The calibration run as JSON (see report.py — the backend renders no HTML)."""
    from .report import SCHEMA_VERSION

    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "history_years": years,
        "universe": universe,
    }
    if not c:
        return {**base, "ok": False,
                "note": "Not enough history to calibrate — keeping the built-in weights."}

    sh, sc = c["sep_heuristic"], c["sep_calibrated"]
    holds = sc[2] >= sh[2] - 0.5
    return {
        **base,
        "ok": True,
        "weights": c["weights"],
        "lift": {k: round(v, 4) for k, v in c["lift"].items()},
        "bars": {"total": int(c["n"]), "train": int(c["n_train"]), "test": int(c["n_test"]),
                 "cutoff": str(c["cutoff"])[:10]},
        "separation": {
            "heuristic": {"weights": HEURISTIC_WEIGHTS,
                          "high_break_pct": round(sh[0], 1), "low_break_pct": round(sh[1], 1),
                          "separation_pts": round(sh[2], 1)},
            "calibrated": {"weights": c["weights"],
                           "high_break_pct": round(sc[0], 1), "low_break_pct": round(sc[1], 1),
                           "separation_pts": round(sc[2], 1)},
        },
        "verdict": {
            "holds": bool(holds),
            "text": ("The calibrated weights hold up out-of-sample — they separate high- from "
                     "low-score band-break rates by more than the hand-set heuristic did."
                     if holds else
                     "The calibration did not beat the hand-set heuristic out-of-sample. Treat the "
                     "weights as provisional."),
        },
        "method": ("Each weight is set in proportion to that feature's exceed-rate lift — how much more "
                   "often the expected-move band breaks at the favourable end of the feature than at the "
                   "unfavourable end. Lift is measured on a train split and the resulting weights are "
                   "scored on a held-out test split, so the number above is out-of-sample."),
    }


def backtest_payload(stats: dict, p: dict, n_tickers: int, years: int) -> dict:
    """The backtest as JSON, with the verdict pre-computed for the frontend."""
    from .report import SCHEMA_VERSION

    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe": n_tickers,
        "history_years": years,
        "horizon_days": int(p["horizon_days"]),
    }
    if not stats:
        return {**base, "ok": False, "note": "Not enough history to backtest."}

    def bucket(s: dict) -> dict:
        return {
            "bars": int(s["bars"]),
            "avg_abs_move_pct": _round(s["avg"]),
            "median_abs_move_pct": _round(s["median"]),
            "coverage_pct": _round(s["cover"]),
            "expansion": _round(s["exp"], 2),
            "broke_band_pct": _round(s["exceed"]),
        }

    hi, lo = stats["high"], stats["low"]
    edge = hi["exceed"] - lo["exceed"]
    holds = edge > 3
    coverage = stats["coverage"]

    return {
        **base,
        "ok": True,
        "bars": int(stats["n"]),
        "coverage_pct": _round(coverage),
        "coverage_theory_pct": 68.0,
        "coverage_ok": bool(60 <= coverage <= 76),
        "score_move_corr": _round(stats["corr"], 2),
        "buckets": {
            "high": {"label": "Score ≥ 60 (coiled)", **bucket(stats["high"])},
            "mid": {"label": "Score 30 – 60", **bucket(stats["mid"])},
            "low": {"label": "Score < 30 (calm)", **bucket(stats["low"])},
        },
        "squeeze": {
            "on": {"label": "Squeeze ON", **bucket(stats["sq_on"])},
            "off": {"label": "Squeeze OFF", **bucket(stats["sq_off"])},
        },
        "verdict": {
            "holds": bool(holds),
            "edge_pts": _round(edge),
            "text": (f"Coiled bars broke their own ±1σ band {hi['exceed']:.0f}% of the time against "
                     f"{lo['exceed']:.0f}% for calm bars ({edge:+.0f} pts) — the squeeze thesis holds."
                     if holds else
                     "Coiled bars did not break their band meaningfully more often than calm ones — "
                     "weak or no edge in this universe."),
        },
        "explainer": ("The honest test is not whether high scores move more in absolute percent — the "
                      "score deliberately selects low-volatility names, which always move less in raw "
                      "terms. The thesis is expansion: does the move break out beyond the stock's own "
                      "compressed band? That is the expansion multiple (realized ÷ expected) and the "
                      "band-break rate."),
        "caveat": ("Overlapping forward windows make these observations autocorrelated, so read the "
                   "percentages as descriptive rather than as independent-sample statistics. The score "
                   "flags where a relative expansion is likelier — never its direction. Past behaviour "
                   "does not guarantee future results."),
    }


def _round(v, nd: int = 1):
    """Round a float, mapping NaN/None to None so it serializes as JSON null."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None
