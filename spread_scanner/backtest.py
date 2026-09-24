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


def _per_ticker_records(df: pd.DataFrame, p: dict, weights: dict | None = None,
                        ticker: str = "") -> pd.DataFrame:
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

    score = _score_series(squeeze, squeeze_days, bw_pctile, hv_pctile, weights=weights)

    # Expected move (1-sigma, %) from trailing daily vol — known at the bar.
    logret = np.log(close / close.shift(1))
    sigma_d = logret.rolling(p["vol_lookback"]).std(ddof=0)
    em_pct = sigma_d * np.sqrt(p["horizon_days"]) * 100

    # The same band sized off the longer `iv_hv_lookback` window — the realized
    # vol the options layer compares implied vol against. The 20-day band above
    # is the one the Setup Score *selects* for being small, so "broke its own
    # band" partly measures that selection. An option is not priced off that
    # shrunken window; this longer one is the nearest free stand-in for what the
    # market would have charged until real implied-vol history accumulates
    # (see `implied_backtest`).
    hv_long = p.get("iv_hv_lookback", 60)
    sigma_long = logret.rolling(hv_long).std(ddof=0)
    em_long_pct = sigma_long * np.sqrt(p["horizon_days"]) * 100

    # Realized absolute move over the FORWARD horizon (the outcome).
    fwd_abs = (close.shift(-p["horizon_days"]) / close - 1).abs() * 100

    out = pd.DataFrame({
        "ticker": ticker,
        "pos": np.arange(len(df)),
        "date": df.index,
        "score": score,
        "squeeze_on": squeeze,
        "squeeze_days": squeeze_days,
        "bw_pctile": bw_pctile,
        "hv_pctile": hv_pctile,
        "em_pct": em_pct,
        "em_long_pct": em_long_pct,
        "fwd_abs": fwd_abs,
    }).dropna()
    out = out[(out["em_pct"] > 0) & (out["em_long_pct"] > 0)]
    out["within_band"] = out["fwd_abs"] <= out["em_pct"]
    # Expansion = realized move as a multiple of its OWN expected (compressed)
    # band. This is the squeeze thesis, free of cross-sectional vol differences:
    # >1 means the move broke out beyond what the quiet range implied.
    out["expansion"] = out["fwd_abs"] / out["em_pct"]
    out["broke_band"] = ~out["within_band"]
    out["broke_long_band"] = out["fwd_abs"] > out["em_long_pct"]
    return out


def non_overlapping(recs: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """Every `horizon`-th bar per ticker, so no two kept outcomes share a day.

    Consecutive daily bars share nine of their ten forward days, so the full
    record set counts one ten-day move about ten times. Stepping by the horizon
    keeps one observation per window. Positions are per-ticker bar counts
    anchored at 0, so names on the same calendar land on the same dates and the
    cross-section stays aligned for the date-block bootstrap below."""
    if recs.empty or "pos" not in recs:
        return recs
    return recs[recs["pos"] % max(int(horizon), 1) == 0]


def bootstrap_edge(recs: pd.DataFrame, hi_mask, lo_mask, col: str = "broke_band",
                   reps: int = 1000, seed: int = 0) -> dict:
    """95% interval on (break rate | hi) − (break rate | lo), resampling *dates*.

    Resampling whole dates rather than rows keeps each day's cross-section
    together: 30 large caps on one day are one market move, not 30 independent
    draws. Run it on the non-overlapping sample, whose dates are a horizon
    apart, and the blocks are close to independent. Deterministic (fixed seed)
    so the published interval does not wobble between identical runs."""
    empty = {"edge_pts": None, "lo_pts": None, "hi_pts": None, "dates": 0, "reps": 0}
    if recs.empty:
        return empty
    frame = pd.DataFrame({
        "date": recs["date"].values,
        "hk": (recs[col] & hi_mask).astype(int).values, "hn": hi_mask.astype(int).values,
        "lk": (recs[col] & lo_mask).astype(int).values, "ln": lo_mask.astype(int).values,
    })
    g = frame.groupby("date")[["hk", "hn", "lk", "ln"]].sum()
    if g["hn"].sum() == 0 or g["ln"].sum() == 0:
        return empty
    arr = g.to_numpy(dtype=float)

    def edge(tot):
        return (tot[..., 0] / tot[..., 1] - tot[..., 2] / tot[..., 3]) * 100

    point = float(edge(arr.sum(axis=0)))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(arr), size=(reps, len(arr)))
    tots = arr[idx].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        draws = edge(tots)
    draws = draws[np.isfinite(draws)]
    if not len(draws):
        return empty
    lo, hi = np.percentile(draws, [2.5, 97.5])
    return {"edge_pts": point, "lo_pts": float(lo), "hi_pts": float(hi),
            "dates": int(len(arr)), "reps": int(len(draws))}


def run_backtest(data: dict[str, pd.DataFrame], p: dict,
                 weights: dict | None = None) -> tuple[pd.DataFrame, dict]:
    """Aggregate per-bar records across the universe and compute summary stats.

    The bucket numbers are descriptive and use every bar. The statistics that
    claim anything — the independent-sample edge and its interval, and the
    comparison against the longer realized-vol band — use the non-overlapping
    sample (`non_overlapping`)."""
    frames = [_per_ticker_records(df, p, weights=weights, ticker=t) for t, df in data.items()]
    frames = [f for f in frames if not f.empty]
    recs = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if recs.empty:
        return recs, {}

    def bucket_stats(mask) -> dict:
        sub = recs[mask]
        if sub.empty:
            return {"bars": 0, "avg": float("nan"), "median": float("nan"),
                    "cover": float("nan"), "exp": float("nan"), "exceed": float("nan"),
                    "exceed_long": float("nan")}
        return {
            "bars": len(sub),
            "avg": sub["fwd_abs"].mean(),
            "median": sub["fwd_abs"].median(),
            "cover": sub["within_band"].mean() * 100,
            "exp": sub["expansion"].mean(),            # avg realized / expected
            "exceed": (~sub["within_band"]).mean() * 100,  # % that broke the band
            "exceed_long": sub["broke_long_band"].mean() * 100,
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

    indep = non_overlapping(recs, p["horizon_days"])
    hi_m, lo_m = indep["score"] >= 60, indep["score"] < 30
    stats["independent"] = {
        "n": len(indep),
        "horizon": int(p["horizon_days"]),
        "own_band": bootstrap_edge(indep, hi_m, lo_m, "broke_band"),
        "long_band": bootstrap_edge(indep, hi_m, lo_m, "broke_long_band"),
    }
    return recs, stats


# Bumped whenever *how* the calibration is measured changes. calibrate.py only
# reuses a committed fit carrying the current number, so a methodology fix
# reaches the page on the next run instead of after the refit interval.
# 2: embargoed split, quintile separation, train-split weights shown.
CALIBRATION_METHOD = 2

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


def _quintile_separation(test: pd.DataFrame, w: dict) -> tuple[float, float, float]:
    """(top-quintile break %, bottom-quintile break %, difference) under weights `w`.

    Quintiles of *each score's own ranking*, not fixed 60/30 cutoffs: different
    weights put different shares of bars above 60, so fixed cutoffs compared a
    thin extreme slice for one weight set against a fat middling one for the
    other. Ranking within each score compares the same number of bars."""
    score = _score_series(test["squeeze_on"], test["squeeze_days"],
                          test["bw_pctile"], test["hv_pctile"], weights=w)
    ranks = score.rank(pct=True, method="first")
    hi = float(test["broke_band"][ranks > 0.8].mean()) * 100
    lo = float(test["broke_band"][ranks <= 0.2].mean()) * 100
    return hi, lo, hi - lo


def calibrate_weights(recs: pd.DataFrame, train_frac: float = 0.7, embargo: int = 10) -> dict:
    """Production weights (full history) + an out-of-sample sanity check: weights
    derived from a TRAIN split are scored on a held-out TEST split to confirm the
    lift-weighting generalizes rather than overfitting.

    `embargo` trading days are dropped after the cutoff. A train bar's outcome is
    the move over the *next* horizon days, so without the gap the last train
    bars' outcomes are priced off days that belong to the test split. Pass the
    horizon."""
    recs = recs.sort_values("date")
    weights, lift = _weights_from_lift(recs)                 # production: full history

    cutoff = recs["date"].quantile(train_frac)
    train = recs[recs["date"] <= cutoff]
    after = np.sort(recs.loc[recs["date"] > cutoff, "date"].unique())
    test_start = after[min(max(int(embargo), 0), len(after) - 1)] if len(after) else None
    test = recs[recs["date"] >= test_start] if test_start is not None else recs.iloc[0:0]
    train_weights, _ = _weights_from_lift(train)

    return {
        "lift": lift, "weights": weights, "train_weights": train_weights,
        "cutoff": cutoff, "test_start": test_start, "embargo": int(embargo),
        "n": len(recs), "n_train": len(train), "n_test": len(test),
        "sep_heuristic": _quintile_separation(test, HEURISTIC_WEIGHTS),
        "sep_calibrated": _quintile_separation(test, train_weights),  # train-derived, tested OOS
    }


def calibration_payload(c: dict, years: int, universe: int,
                        as_of: str | None = None) -> dict:
    """The calibration run as JSON (see report.py — the backend renders no HTML).

    `as_of` is the stamp written into weights.json by the same run, so the page
    can tell whether the scan it is sitting next to actually scored with these
    weights. It can't otherwise: weights.json is a working file and gitignored,
    while this payload is committed, so a day when the calibration step fails
    leaves yesterday's fit on the page beside a scan that used the built-in
    weights."""
    from .report import SCHEMA_VERSION

    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": as_of or dt.date.today().isoformat(),
        "history_years": years,
        "universe": universe,
        "method_version": CALIBRATION_METHOD,
    }
    if not c:
        return {**base, "ok": False,
                "note": "Not enough history to calibrate — keeping the built-in weights."}

    sh, sc = c["sep_heuristic"], c["sep_calibrated"]
    # Strictly better, or it does not get to say "more than".
    holds = bool(math.isfinite(sc[2]) and math.isfinite(sh[2]) and sc[2] > sh[2])
    return {
        **base,
        "ok": True,
        "weights": c["weights"],
        "lift": {k: round(v, 4) for k, v in c["lift"].items()},
        "bars": {"total": int(c["n"]), "train": int(c["n_train"]), "test": int(c["n_test"]),
                 "embargoed": int(c["n"]) - int(c["n_train"]) - int(c["n_test"]),
                 "cutoff": str(c["cutoff"])[:10],
                 "test_start": str(c["test_start"])[:10] if c.get("test_start") is not None else None},
        # Top vs bottom quintile of each score on the test split.
        "separation_basis": "quintile",
        "separation": {
            "heuristic": {"weights": HEURISTIC_WEIGHTS,
                          "high_break_pct": _round(sh[0]), "low_break_pct": _round(sh[1]),
                          "separation_pts": _round(sh[2])},
            # The weights that actually produced this row: fitted on the train
            # split only. `weights` above is the full-history fit the scanner
            # uses, which never saw a held-out split and must not be shown
            # beside an out-of-sample number as if it had.
            "calibrated": {"weights": c["train_weights"],
                           "high_break_pct": _round(sc[0]), "low_break_pct": _round(sc[1]),
                           "separation_pts": _round(sc[2])},
        },
        "verdict": {
            "holds": bool(holds),
            "text": ("Weights fitted on the train split alone separate the top from the bottom "
                     "score quintile on the held-out test split by more than the hand-set "
                     "heuristic does."
                     if holds else
                     "The calibration did not beat the hand-set heuristic out-of-sample. Treat the "
                     "weights as provisional."),
        },
        "method": ("Each weight is set in proportion to that feature's exceed-rate lift — how much more "
                   "often the expected-move band breaks at the favourable end of the feature than at the "
                   "unfavourable end. Lift is measured on a train split, a gap of one horizon is dropped "
                   "so no training outcome reaches into the test period, and the train-split weights "
                   "are scored on the held-out rest by comparing the top and bottom fifth of the "
                   "score. The weights the scanner runs on are refitted on all history."),
    }


def backtest_payload(stats: dict, p: dict, n_tickers: int, years: int,
                     weights: dict | None = None, weights_as_of: str | None = None) -> dict:
    """The backtest as JSON, with the verdict pre-computed for the frontend.

    `weights` is the weight set the scores below were actually computed with, so
    the page can say whether it is reading the built-in heuristic or a fitted
    model — and, when it is the fitted one, that the fit saw this same history."""
    from .report import SCHEMA_VERSION

    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "universe": n_tickers,
        "history_years": years,
        "horizon_days": int(p["horizon_days"]),
        "long_band_days": int(p.get("iv_hv_lookback", 60)),
        "weights": {
            "values": dict(weights or scanner.SCORE_WEIGHTS),
            "as_of": weights_as_of,
            "source": "auto-calibrated" if weights_as_of else "default",
            # compute_weights() fits on all available history and this measures
            # the resulting score on that same history. Saying so is the whole
            # of the fix: the out-of-sample number lives in calibration.json,
            # which splits train from test.
            "in_sample": bool(weights_as_of),
        },
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
            "broke_long_band_pct": _round(s.get("exceed_long")),
        }

    def interval(b: dict) -> dict:
        return {"edge_pts": _round(b.get("edge_pts")), "ci95_pts": [_round(b.get("lo_pts")),
                                                                    _round(b.get("hi_pts"))],
                "dates": int(b.get("dates") or 0)}

    hi, lo = stats["high"], stats["low"]
    indep = stats.get("independent") or {}
    own, long_ = indep.get("own_band") or {}, indep.get("long_band") or {}
    edge = own.get("edge_pts")
    ci_lo = own.get("lo_pts")
    # The thesis holds only when the interval on the independent sample clears
    # zero — a point estimate off 35,000 overlapping bars is not evidence.
    holds = ci_lo is not None and ci_lo > 0
    long_edge, long_lo, long_hi = long_.get("edge_pts"), long_.get("lo_pts"), long_.get("hi_pts")
    coverage = stats["coverage"]
    if long_edge is None:
        long_text = "Not enough independent bars to compare against the longer band."
    elif long_lo is not None and long_lo > 0:
        long_text = (f"Against the {p.get('iv_hv_lookback', 60)}-day realized-vol band — the "
                     f"nearest free stand-in for what options would have charged — coiled names "
                     f"still broke out more often ({long_edge:+.0f} pts, 95% CI {long_lo:+.0f} to "
                     f"{long_hi:+.0f}).")
    else:
        long_text = (f"Against the {p.get('iv_hv_lookback', 60)}-day realized-vol band — the "
                     f"nearest free stand-in for what options would have charged — the edge is "
                     f"{long_edge:+.0f} pts (95% CI {long_lo:+.0f} to {long_hi:+.0f}): coiled names "
                     "do not out-move a longer-run vol estimate, so buying premium on the score "
                     "alone is not supported. The own-band edge is mostly volatility returning "
                     "to normal.")

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
        "independent": {
            "bars": int(indep.get("n") or 0),
            "step_days": int(indep.get("horizon") or p["horizon_days"]),
            "own_band": interval(own),
            "long_band": interval(long_),
        },
        "verdict": {
            "holds": bool(holds),
            "edge_pts": _round(edge),
            "ci95_pts": [_round(ci_lo), _round(own.get("hi_pts"))],
            "text": ((f"Coiled bars broke their own ±1σ band {hi['exceed']:.0f}% of the time against "
                      f"{lo['exceed']:.0f}% for calm bars. On non-overlapping windows the gap is "
                      f"{edge:+.0f} pts (95% CI {ci_lo:+.0f} to {own['hi_pts']:+.0f}), so the "
                      "expansion is real — relative to each name's own quiet band.")
                     if holds else
                     "On non-overlapping windows, coiled bars did not break their band reliably more "
                     "often than calm ones — the 95% interval includes zero."),
            "long_band_text": long_text,
        },
        "explainer": ("The honest test is not whether high scores move more in absolute percent — the "
                      "score deliberately selects low-volatility names, which always move less in raw "
                      "terms. The thesis is expansion: does the move break out beyond the stock's own "
                      "compressed band? That is the expansion multiple (realized ÷ expected) and the "
                      "band-break rate."),
        "caveat": ("The bucket rows use every bar, and overlapping forward windows make those "
                   "autocorrelated — read them as descriptive. The verdict and its interval use one "
                   "bar per horizon per name and resample whole dates, so they are not. The "
                   "universe is also whatever passes the screen *today*, measured backwards: names "
                   "that would have dragged these numbers down are the ones no longer in it. And "
                   "when the score being tested comes from calibrated weights, those weights were "
                   "fitted on this same history — the honest out-of-sample separation is the one on "
                   "the calibration panel, which holds a test split back. The score flags where a "
                   "relative expansion is likelier — never its direction. Past behaviour does not "
                   "guarantee future results."),
    }


def _round(v, nd: int = 1):
    """Round a float, mapping NaN/None to None so it serializes as JSON null."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, nd) if math.isfinite(f) else None
