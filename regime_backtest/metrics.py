"""Performance statistics over a daily strategy frame from ``engine.run``."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .engine import TRADING_DAYS

CRISIS_WINDOWS: list[tuple[str, str, str]] = [
    ("2000–02", "2000-01-01", "2002-12-31"),
    ("2007–09", "2007-01-01", "2009-12-31"),
    ("2011", "2011-01-01", "2011-12-31"),
    ("2015–16", "2015-01-01", "2016-12-31"),
    ("2018 Q4", "2018-10-01", "2018-12-31"),
    ("2020", "2020-01-01", "2020-12-31"),
    ("2022", "2022-01-01", "2022-12-31"),
    ("2025", "2025-01-01", "2025-12-31"),
]


def max_drawdown(ret: pd.Series) -> float:
    eq = (1.0 + ret).cumprod()
    peak = np.maximum(eq.cummax(), 1.0)       # the starting capital counts as a peak
    return float((eq / peak - 1.0).min())


def drawdown_series(ret: pd.Series) -> pd.Series:
    eq = (1.0 + ret).cumprod()
    return eq / np.maximum(eq.cummax(), 1.0) - 1.0


def spells(pos: pd.Series) -> list[int]:
    """Lengths, in trading days, of each uninterrupted in-market spell."""
    out, run = [], 0
    for p in pos.to_numpy():
        if p >= 0.5:
            run += 1
        elif run:
            out.append(run)
            run = 0
    if run:
        out.append(run)
    return out


def sharpe(ret: pd.Series, cash: pd.Series) -> float:
    ex = ret - cash
    sd = ex.std(ddof=1)
    return float(ex.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else float("nan")


def summarize(df: pd.DataFrame) -> dict:
    r, c, pos = df["ret"], df["cash"], df["pos"]
    n = len(r)
    if n < 2:
        return {}
    years = (r.index[-1] - r.index[0]).days / 365.25 + 1 / TRADING_DAYS
    total = float((1.0 + r).prod())
    cagr = total ** (1.0 / years) - 1.0
    ex = r - c
    downside = np.sqrt(np.mean(np.minimum(ex.to_numpy(), 0.0) ** 2)) * np.sqrt(TRADING_DAYS)
    mdd = max_drawdown(r)
    roll12 = (1.0 + r).rolling(TRADING_DAYS).apply(np.prod, raw=True) - 1.0
    sp = spells(pos)
    switches = int((pos.diff().abs() > 0).sum())
    return {
        "start": r.index[0].date().isoformat(),
        "end": r.index[-1].date().isoformat(),
        "years": round(years, 2),
        "cagr": cagr,
        "vol": float(r.std(ddof=1) * np.sqrt(TRADING_DAYS)),
        "sharpe": sharpe(r, c),
        "sortino": float(ex.mean() * TRADING_DAYS / downside) if downside > 0 else float("nan"),
        "max_dd": mdd,
        "calmar": cagr / abs(mdd) if mdd < 0 else float("nan"),
        "worst_12m": float(roll12.min()) if roll12.notna().any() else float("nan"),
        "pct_invested": float(pos.mean()),
        "switches": switches,
        "switches_per_year": switches / years,
        "avg_hold_days": float(np.mean(sp)) if sp else 0.0,
    }


def window_stats(df: pd.DataFrame, start: str, end: str) -> dict:
    """Total return and max drawdown inside [start, end], or NaNs when the
    strategy has no history covering the window's start."""
    if df.empty or df.index[0] > pd.Timestamp(start) + pd.Timedelta(days=7):
        return {"ret": float("nan"), "max_dd": float("nan")}
    sub = df.loc[start:end, "ret"]
    if sub.empty:
        return {"ret": float("nan"), "max_dd": float("nan")}
    return {"ret": float((1.0 + sub).prod() - 1.0), "max_dd": max_drawdown(sub)}


def sharpe_diff_ci(a: pd.DataFrame, b: pd.DataFrame, reps: int = 2000, block: int = 63,
                   seed: int = 7, level: float = 0.90) -> tuple[float, float, float]:
    """Sharpe(a) − Sharpe(b) with a paired circular block bootstrap CI.

    Days are resampled in blocks (the same blocks for both strategies), so the
    correlation between the two and the autocorrelation within each survive.
    A fixed seed keeps the report reproducible."""
    idx = a.index.intersection(b.index)
    ea = (a.loc[idx, "ret"] - a.loc[idx, "cash"]).to_numpy()
    eb = (b.loc[idx, "ret"] - b.loc[idx, "cash"]).to_numpy()
    n = len(idx)

    def sr(x: np.ndarray) -> np.ndarray:
        return x.mean(axis=-1) / x.std(axis=-1, ddof=1) * np.sqrt(TRADING_DAYS)

    point = float(sr(ea) - sr(eb))
    rng = np.random.default_rng(seed)
    nblocks = int(np.ceil(n / block))
    diffs = []
    for chunk in range(0, reps, 100):                   # chunked to keep memory flat
        k = min(100, reps - chunk)
        starts = rng.integers(0, n, size=(k, nblocks))
        rows = (starts[:, :, None] + np.arange(block)[None, None, :]).reshape(k, -1)[:, :n] % n
        diffs.append(sr(ea[rows]) - sr(eb[rows]))
    diffs = np.concatenate(diffs)
    lo, hi = np.quantile(diffs, [(1 - level) / 2, 1 - (1 - level) / 2])
    return point, float(lo), float(hi)
