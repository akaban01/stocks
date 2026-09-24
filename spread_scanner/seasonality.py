"""Calendar-month seasonality — which months a name tends to rise or fall in.

The charts window already holds several years of daily closes per ticker, so the
month-by-month record is sitting there unused. This module reduces it to one
number per calendar month: take the last close of every month, chain them into
monthly returns, then group those returns by month-of-year.

Two honesty rules are built in rather than left to the reader:

* **Only whole months count.** A window that starts mid-March contributes no
  March, and the month in progress at the end is dropped — a return measured
  over eleven days is not a March. Gaps in the history break the chain too, so a
  missing month never silently merges into its neighbour.
* **Thin months are not ranked.** With five years of history a month has five
  observations; ``MIN_YEARS`` is the floor below which a month is still reported
  (with its sample count) but is never named best or worst.

Nothing here is a forecast. Monthly averages over a handful of years are noisy,
and the tickers in one screen move together, so the pooled row is closer to
"N years of evidence" than to "N × tickers".
"""

from __future__ import annotations

import statistics

import numpy as np
import pandas as pd

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# A month needs at least this many distinct years before it can be called the
# best or the worst. Fewer than three and one earnings blow-up is the pattern.
MIN_YEARS = 3

# Naming a best and a worst month means picking the extremes of twelve noisy
# averages, and twelve averages always have extremes. A month is named only
# when its average is more extreme than the best (or worst) month is in at least
# 95% of histories with the calendar months shuffled — `extreme_p` below.
SHUFFLES = 1000
ALPHA = 0.05



def month_end_closes(closes: pd.Series) -> pd.Series:
    """The last close of each whole month, indexed by month period.

    The trailing month is dropped when the history stops short of its end, so a
    part-month is never mistaken for a month.
    """
    if closes is None or len(closes) < 2:
        return pd.Series(dtype="float64")
    s = closes.dropna()
    if s.empty:
        return pd.Series(dtype="float64")
    s.index = pd.to_datetime(s.index)
    if getattr(s.index, "tz", None) is not None:
        # to_period would warn and drop the zone anyway; a daily bar has no
        # meaningful time of day, so shed it here rather than in the log.
        s.index = s.index.tz_localize(None)

    monthly = s.groupby(s.index.to_period("M")).last()

    # The month in progress is not a month. If another session is still due
    # before the month turns, drop it — a calendar-day slack would admit a
    # January on the 27th and then let its value drift with every daily run.
    # BDay knows weekends but not holidays, so a month whose final session is
    # followed by a weekday holiday is dropped rather than half-counted: rarer
    # than the error it replaces, and the safe direction to be wrong in.
    last = s.index[-1]
    if (last + pd.offsets.BDay(1)).month == last.month:
        monthly = monthly.iloc[:-1]
    return monthly if not monthly.empty else pd.Series(dtype="float64")


def monthly_returns(closes: pd.Series) -> pd.Series:
    """Percent return of each whole calendar month, indexed by month period.

    Reindexing onto a gapless month range before chaining is what keeps a hole
    in the history from being charged to the month that follows it: both sides
    of a gap come out NaN and drop away.
    """
    monthly = month_end_closes(closes)
    if len(monthly) < 2:
        return pd.Series(dtype="float64")
    monthly = monthly.reindex(pd.period_range(monthly.index[0], monthly.index[-1], freq="M"))
    # fill_method=None explicitly: through pandas 2.x ``pct_change`` still pads
    # by default, which would forward-fill the very NaN the reindex above just
    # created — turning a gap into a fabricated flat month plus a double move
    # in the month after it. The whole rule lives in that keyword.
    return (monthly.pct_change(fill_method=None) * 100.0).dropna()


def _month_row(month: int, vals: pd.Series) -> dict:
    n = int(len(vals))
    if not n:
        return {"month": month, "name": MONTH_NAMES[month - 1], "n": 0, "years": 0,
                "avg_pct": None, "median_pct": None, "win_rate_pct": None,
                "best_pct": None, "worst_pct": None, "by_year": []}
    by_year = sorted(({"year": int(idx.year), "pct": round(float(v), 2)}
                      for idx, v in vals.items()), key=lambda r: r["year"])
    return {
        "month": month,
        "name": MONTH_NAMES[month - 1],
        "n": n,
        "years": int(len(set(vals.index.year))),
        "avg_pct": round(float(vals.mean()), 2),
        "median_pct": round(float(vals.median()), 2),
        "win_rate_pct": round(float((vals > 0).mean() * 100.0), 1),
        "best_pct": round(float(vals.max()), 2),
        "worst_pct": round(float(vals.min()), 2),
        "by_year": by_year,
    }


def month_rows(returns: pd.Series) -> list[dict]:
    """One row per calendar month, January first, months with no data included."""
    empty = pd.Series(dtype="float64")
    if returns is None or len(returns) == 0:
        return [_month_row(m, empty) for m in range(1, 13)]
    return [_month_row(m, returns[returns.index.month == m]) for m in range(1, 13)]


def _row_years(row: dict) -> int:
    return row["years"]


def _extremes(rows: list[dict], min_years: int = MIN_YEARS, years_of=_row_years):
    """Best and worst month numbers, ignoring months with too little history.

    ``years_of`` says what "enough history" counts: for one ticker that is the
    row's own year count, for the pooled rows the typical per-ticker one.
    """
    ranked = [r for r in rows if r["avg_pct"] is not None and years_of(r) >= min_years]
    if not ranked:
        return None, None
    return (max(ranked, key=lambda r: r["avg_pct"])["month"],
            min(ranked, key=lambda r: r["avg_pct"])["month"])


def extreme_p(returns: pd.Series, months: set[int], seed: int = 0,
              shuffles: int = SHUFFLES) -> tuple[float | None, float | None]:
    """(p for the best month, p for the worst), from a calendar-shuffle test.

    Each shuffle re-deals which calendar month every *period* belongs to — the
    same deal for every name in a pooled series, so names that moved together
    in one month still move together — and records the highest and lowest
    monthly average among `months`. p is the share of shuffles at least as
    extreme as what was observed. Deterministic (fixed seed)."""
    if returns is None or returns.empty or not months:
        return None, None
    periods = pd.PeriodIndex(returns.index)
    codes, uniques = pd.factorize(periods)
    labels = np.asarray(uniques.month) - 1
    vals = returns.to_numpy(dtype=float)
    eligible = np.array(sorted(m - 1 for m in months))

    def extremes(lab):
        rows = lab[codes]
        sums = np.bincount(rows, weights=vals, minlength=12)
        counts = np.bincount(rows, minlength=12)
        with np.errstate(invalid="ignore", divide="ignore"):
            means = sums / counts
        means = means[eligible]
        return np.nanmax(means), np.nanmin(means)

    obs_max, obs_min = extremes(labels)
    rng = np.random.default_rng(seed)
    hi = lo = 0
    for _ in range(shuffles):
        mx, mn = extremes(rng.permutation(labels))
        hi += mx >= obs_max - 1e-12
        lo += mn <= obs_min + 1e-12
    return (hi + 1) / (shuffles + 1), (lo + 1) / (shuffles + 1)


def _judge(summary: dict, returns: pd.Series, years_of=_row_years) -> None:
    """Keep the named best/worst month only if it beats the shuffle test, and
    add each month's average relative to the name's (or pool's) average month."""
    rows = summary["months"]
    overall = float(returns.mean()) if len(returns) else None
    for r in rows:
        r["excess_pct"] = (round(r["avg_pct"] - overall, 2)
                           if r["avg_pct"] is not None and overall is not None else None)
    months = {r["month"] for r in rows if r["avg_pct"] is not None and years_of(r) >= MIN_YEARS}
    p_best, p_worst = extreme_p(returns, months)
    summary["best_p"] = None if p_best is None else round(p_best, 3)
    summary["worst_p"] = None if p_worst is None else round(p_worst, 3)
    summary["avg_month_pct"] = None if overall is None else round(overall, 2)
    if p_best is None or p_best >= ALPHA:
        summary["best_month"] = None
    if p_worst is None or p_worst >= ALPHA:
        summary["worst_month"] = None
    summary["alpha"] = ALPHA


def summarize(returns: pd.Series) -> dict | None:
    """Reduce a series of monthly returns to the payload the frontend draws."""
    if returns is None or returns.empty:
        return None
    rows = month_rows(returns)
    best, worst = _extremes(rows)
    years = sorted(set(returns.index.year))
    out = {
        "months": rows,
        "best_month": best,
        "worst_month": worst,
        "observations": int(len(returns)),
        "years": {"start": int(years[0]), "end": int(years[-1]), "count": len(years)},
    }
    _judge(out, returns)
    return out


def pooled(returns_by_ticker: dict[str, pd.Series]) -> dict | None:
    """The same summary across every ticker at once — one row per month.

    Every ticker-month is one observation, so a month's ``n`` counts names as
    well as years; ``years`` and ``tickers`` are reported alongside it because
    names in one screen are correlated and ``n`` alone would overstate the
    evidence.

    ``years`` here spans the concatenated names, so one long history can carry
    it while everything else is short. The ranking therefore gates on
    ``ticker_years.median`` — the typical name's year count for that month —
    and both it and the minimum ship in the payload, so the headline cannot
    lean on a single ten-year name.
    """
    parts = [r for r in returns_by_ticker.values() if r is not None and not r.empty]
    if not parts:
        return None
    summary = summarize(pd.concat(parts))
    if summary is None:
        return None

    named = [(t, r) for t, r in returns_by_ticker.items() if r is not None and not r.empty]
    for row in summary["months"]:
        matches = [(t, r[r.index.month == row["month"]]) for t, r in named]
        matches = [(t, m) for t, m in matches if not m.empty]
        counts = sorted(len(set(m.index.year)) for _, m in matches)
        row["tickers"] = len(counts)
        # median_low keeps it an integer and rounds toward the shorter history.
        row["ticker_years"] = ({"min": counts[0], "median": int(statistics.median_low(counts))}
                               if counts else None)
        # The concatenated series behind ``summary`` loses which name each
        # observation came from — rebuild by_year here, labelled, so a click on
        # the pooled row can name names instead of just years.
        row["by_year"] = sorted(
            ({"ticker": t, "year": int(idx.year), "pct": round(float(v), 2)}
             for t, m in matches for idx, v in m.items()),
            key=lambda e: (e["year"], e["ticker"]))

    typical = lambda r: (r["ticker_years"] or {}).get("median", 0)          # noqa: E731
    summary["best_month"], summary["worst_month"] = _extremes(summary["months"], years_of=typical)
    _judge(summary, pd.concat(parts), years_of=typical)
    summary["tickers"] = len(parts)
    summary["min_years"] = MIN_YEARS
    return summary
