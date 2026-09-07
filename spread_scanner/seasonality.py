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

import pandas as pd

MONTH_NAMES = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# A month needs at least this many distinct years before it can be called the
# best or the worst. Fewer than three and one earnings blow-up is the pattern.
MIN_YEARS = 3

# The last month is "in progress" — and so excluded — unless the history runs to
# within this many calendar days of the month end (a slack that covers a month
# ending on a weekend or a holiday).
PARTIAL_MONTH_DAYS = 4


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

    monthly = s.groupby(s.index.to_period("M")).last()
    last = s.index[-1]
    if (last + pd.offsets.MonthEnd(0) - last).days > PARTIAL_MONTH_DAYS:
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
    return (monthly.pct_change() * 100.0).dropna()


def _month_row(month: int, vals: pd.Series) -> dict:
    n = int(len(vals))
    if not n:
        return {"month": month, "name": MONTH_NAMES[month - 1], "n": 0, "years": 0,
                "avg_pct": None, "median_pct": None, "win_rate_pct": None,
                "best_pct": None, "worst_pct": None}
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
    }


def month_rows(returns: pd.Series) -> list[dict]:
    """One row per calendar month, January first, months with no data included."""
    empty = pd.Series(dtype="float64")
    if returns is None or len(returns) == 0:
        return [_month_row(m, empty) for m in range(1, 13)]
    return [_month_row(m, returns[returns.index.month == m]) for m in range(1, 13)]


def _extremes(rows: list[dict], min_years: int = MIN_YEARS):
    """Best and worst month numbers, ignoring months with too little history."""
    ranked = [r for r in rows if r["avg_pct"] is not None and r["years"] >= min_years]
    if not ranked:
        return None, None
    return (max(ranked, key=lambda r: r["avg_pct"])["month"],
            min(ranked, key=lambda r: r["avg_pct"])["month"])


def summarize(returns: pd.Series) -> dict | None:
    """Reduce a series of monthly returns to the payload the frontend draws."""
    if returns is None or returns.empty:
        return None
    rows = month_rows(returns)
    best, worst = _extremes(rows)
    years = sorted(set(returns.index.year))
    return {
        "months": rows,
        "best_month": best,
        "worst_month": worst,
        "observations": int(len(returns)),
        "years": {"start": int(years[0]), "end": int(years[-1]), "count": len(years)},
    }


def pooled(returns_by_ticker: dict[str, pd.Series]) -> dict | None:
    """The same summary across every ticker at once — one row per month.

    Every ticker-month is one observation, so a month's ``n`` counts names as
    well as years; ``years`` and ``tickers`` are reported alongside it because
    names in one screen are correlated and ``n`` alone would overstate the
    evidence.
    """
    parts = [r for r in returns_by_ticker.values() if r is not None and not r.empty]
    if not parts:
        return None
    summary = summarize(pd.concat(parts))
    if summary is None:
        return None
    for row in summary["months"]:
        row["tickers"] = sum(1 for r in parts if bool((r.index.month == row["month"]).any()))
    summary["tickers"] = len(parts)
    summary["min_years"] = MIN_YEARS
    return summary
