"""Event studies. Not strategies: the samples are a handful of dates, so every
event is listed rather than averaged into a number that looks more certain than
it is."""

from __future__ import annotations

import numpy as np
import pandas as pd

HORIZONS = {"1m": 21, "3m": 63, "6m": 126, "12m": 252}
CLUSTER_GAP = 20            # trading days between clusters
MIN_INVERSION = 60          # trading days inverted before a re-steepening counts
PEAK_TROUGH_WINDOW = 756    # trading days (3 years) searched for the next trough


def first_of_clusters(cond: pd.Series, gap: int = CLUSTER_GAP) -> list[pd.Timestamp]:
    """First date of each run of True days, where a new cluster starts only
    after at least ``gap`` trading days with no True day."""
    pos = np.flatnonzero(cond.fillna(False).to_numpy(dtype=bool))
    out, last = [], None
    for p in pos:
        if last is None or p - last >= gap:
            out.append(cond.index[p])
        last = p
    return out


def forward_table(close: pd.Series, dates: list[pd.Timestamp]) -> pd.DataFrame:
    """SPY forward returns from the signal day's close, and the worst close
    after the signal (relative to the signal close) before SPY recovers.

    "Recovers" means SPY closes at or above the all-time high it had made by
    the signal date. Recovering to the signal-day close is not used, because
    that is often a 2–3 day bounce in the middle of a falling market: SPY closed
    above its 2008-09-16 level three days later, then fell another 40%."""
    rows = []
    vals = close.to_numpy()
    peak_so_far = np.maximum.accumulate(vals)
    for d in dates:
        i = close.index.get_loc(d)
        row = {"date": d.date().isoformat(), "spy_close": vals[i],
               "below_prior_peak": vals[i] / peak_so_far[i] - 1.0}
        for k, h in HORIZONS.items():
            row[k] = vals[i + h] / vals[i] - 1.0 if i + h < len(vals) else np.nan
        rec = np.flatnonzero(vals[i + 1:] >= peak_so_far[i])
        j = i + 1 + rec[0] if len(rec) else len(vals) - 1
        row["max_dd_before_recovery"] = min(0.0, vals[i:j + 1].min() / vals[i] - 1.0)
        row["days_to_recover"] = int(j - i) if len(rec) else None   # None: not by the end of the data
        rows.append(row)
    return pd.DataFrame(rows)


def capitulation_dates(close: pd.Series, sma200: pd.Series, vix: pd.Series, vix3m: pd.Series,
                       credit_exit: pd.Series) -> list[pd.Timestamp]:
    """SPY below its 200-day SMA, VIX in backwardation (VIX > VIX3M) and the
    credit-velocity exit condition, all on the same close. VIX inputs and the
    credit condition are expected already aligned and lagged."""
    cond = (close < sma200) & (vix > vix3m) & credit_exit.fillna(False).astype(bool)
    return first_of_clusters(cond)


def resteepening_dates(curve: pd.Series, min_days: int = MIN_INVERSION) -> list[pd.Timestamp]:
    """Days the (aligned, lagged) 10y–2y spread turns positive after at least
    ``min_days`` consecutive non-positive days that include an inversion."""
    out, run, inverted = [], 0, False
    for d, v in curve.items():
        if np.isnan(v):
            continue
        if v <= 0:
            run += 1
            inverted = inverted or v < 0
        else:
            if run >= min_days and inverted:
                out.append(d)
            run, inverted = 0, False
    return out


def peak_trough(close: pd.Series, dates: list[pd.Timestamp],
                window: int = PEAK_TROUGH_WINDOW) -> pd.DataFrame:
    """For each signal: the lowest SPY close in the next ``window`` trading days
    (the trough), the highest close between the signal and that trough (the
    peak), and how many trading days after the signal each came."""
    rows = []
    vals = close.to_numpy()
    for d in dates:
        i = close.index.get_loc(d)
        end = min(len(vals), i + window + 1)
        seg = vals[i:end]
        t = int(np.argmin(seg))
        p = int(np.argmax(seg[:t + 1]))
        rows.append({
            "date": d.date().isoformat(),
            "days_to_peak": p, "peak_date": close.index[i + p].date().isoformat(),
            "days_to_trough": t, "trough_date": close.index[i + t].date().isoformat(),
            "peak_to_trough": seg[t] / seg[p] - 1.0,
            "window_complete": end - i == window + 1,
        })
    return pd.DataFrame(rows)
