"""Data access — pulls daily OHLCV from Yahoo Finance via yfinance.

yfinance needs no API key, which makes it ideal for an unattended GitHub
Action. We download all tickers in one batched call and normalize the
result into a {ticker: DataFrame} mapping regardless of how many tickers
were requested.

Two shapes come back and both are handled explicitly, because the difference
between them is silent: a multi-ticker request returns a MultiIndex column
frame keyed by ticker, and a single-ticker request returns flat OHLCV columns
with the ticker nowhere in the frame. Guessing which one you got — and filing
flat columns under ``tickers[0]`` regardless — mislabels a whole price history
as another company's the first time a batch request comes back flat.
"""

from __future__ import annotations

import re

import pandas as pd
import yfinance as yf

from .net import retry

# yfinance period strings, in trading-ish calendar days. Used only to slice a
# longer download down to a shorter window (see `slice_period`), never to
# request one.
_PERIOD_DAYS = {"d": 1, "mo": 30, "y": 365}
_PERIOD_RE = re.compile(r"^(\d+)(d|mo|y)$")


def period_days(period: str) -> int | None:
    """Calendar days in a yfinance period string ("6mo" -> 180). None for
    anything unparseable, including "max" and "ytd"."""
    m = _PERIOD_RE.match(str(period or "").strip().lower())
    if not m:
        return None
    return int(m.group(1)) * _PERIOD_DAYS[m.group(2)]


def slice_period(frames: dict[str, pd.DataFrame], period: str) -> dict[str, pd.DataFrame]:
    """Trim an already-downloaded {ticker: frame} to the tail `period` covers.

    The scan wants a year and the charts want a decade of the same daily bars,
    and the decade is a strict superset. Slicing it halves the requests made to
    a free endpoint on every run. Returns the frames untouched when the period
    cannot be parsed — better a second download than a silently wrong window."""
    days = period_days(period)
    if not days:
        return frames
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in frames.items():
        if df is None or df.empty:
            continue
        try:
            cutoff = df.index.max() - pd.Timedelta(days=days)
            sub = df[df.index >= cutoff]
        except (TypeError, ValueError):
            sub = df                      # not a datetime index — leave it alone
        if not sub.empty:
            out[ticker] = sub
    return out


def download(tickers: list[str], period: str = "6mo", interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Return {ticker: OHLCV DataFrame}. Tickers with no data are skipped."""
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not tickers:
        return {}

    raw = retry(lambda: yf.download(
        tickers=tickers,
        period=period,
        interval=interval,
        auto_adjust=True,     # split/dividend adjusted; drops the 'Adj Close' col
        group_by="ticker",
        threads=True,
        progress=False,
    ), label=f"price download ({len(tickers)} tickers, {period})")

    out: dict[str, pd.DataFrame] = {}

    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t not in available:
                continue
            sub = raw[t].dropna(how="all")
            if not sub.empty:
                out[t] = sub
    elif len(tickers) == 1:
        # Single ticker -> flat columns, and only then is the ticker knowable.
        sub = raw.dropna(how="all")
        if not sub.empty:
            out[tickers[0]] = sub
    else:
        # Flat columns for a multi-ticker request: one survivor, or a shape
        # change in yfinance. Either way the frame carries no ticker, so filing
        # it under the first one requested would be a guess with a company name
        # on it. Say so and return nothing rather than mislabel a price series.
        print(f"  ! price download for {len(tickers)} tickers came back with flat columns "
              f"— no ticker labels to file it under, so this batch is dropped. "
              f"(yfinance shape change, or only one ticker had data.)")

    return out
