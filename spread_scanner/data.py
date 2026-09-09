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
import time

import pandas as pd
import yfinance as yf

from .net import BASE_DELAY, retry

# How many extra passes to make for tickers that came back with nothing. See
# `download` — for this endpoint a failure is a *result*, not an exception, so
# `net.retry` around the call cannot see it.
MISSING_RETRIES = 1

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


def slice_period(frames: dict[str, pd.DataFrame], period: str,
                 now: pd.Timestamp | None = None) -> dict[str, pd.DataFrame]:
    """Trim an already-downloaded {ticker: frame} to the tail `period` covers.

    The scan wants a year and the charts want a decade of the same daily bars,
    and the decade is a strict superset. Slicing it halves the requests made to
    a free endpoint on every run. Returns the frames untouched when the period
    cannot be parsed — better a second download than a silently wrong window.

    The window is measured back from **today**, not from each frame's own last
    bar, which is what `period="1y"` meant when this was a second download. The
    difference is a ticker that stopped trading: asking Yahoo for a year of a
    name last quoted in 2023 returns nothing and it drops out of the scan, but
    slicing a year off the end of its own history hands back a full year of
    stale bars that score exactly like live ones."""
    days = period_days(period)
    if not days:
        return frames
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in frames.items():
        if df is None or df.empty:
            continue
        try:
            end = df.index.max()
            today = now if now is not None else pd.Timestamp.now(tz=getattr(end, "tz", None))
            sub = df[df.index >= today - pd.Timedelta(days=days)]
        except (TypeError, ValueError):
            sub = df                      # not a datetime index — leave it alone
        if not sub.empty:
            out[ticker] = sub
    return out


def _download_once(tickers: list[str], period: str, interval: str) -> dict[str, pd.DataFrame]:
    """One batched request, normalized to {ticker: frame}."""
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


def download(tickers: list[str], period: str = "6mo", interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Return {ticker: OHLCV DataFrame}. Tickers with no data are skipped.

    Retried at the level the failure actually appears at. `yf.download` does not
    raise when a ticker fails: `_download_one` catches everything, files an empty
    frame under that symbol and logs it, and the batch returns normally — so
    wrapping the call in `net.retry` (which we still do, for the failures that
    *are* exceptions) never sees a rate-limited ticker. In this version the error
    detail lives in a per-call context object that is discarded on return, and
    the module-level `shared._ERRORS` nothing writes to any more, so the only
    signal available to a caller is that a requested ticker is not in the result.

    That is what this retries: the missing subset, once. A ticker that is simply
    dead stays missing and costs one extra request per run, which is the price of
    not silently dropping a live one for the day over a single 429."""
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not tickers:
        return {}

    out = _download_once(tickers, period, interval)
    for attempt in range(MISSING_RETRIES):
        missing = [t for t in tickers if t not in out]
        if not missing:
            break
        delay = BASE_DELAY * (2 ** attempt)
        print(f"  ! {len(missing)} of {len(tickers)} ticker(s) came back empty "
              f"({', '.join(missing[:8])}{'…' if len(missing) > 8 else ''}) — "
              f"retrying just those in {delay:.0f}s")
        time.sleep(delay)
        # Only the missing ones: a second full batch would re-request everything
        # that already worked, which is what got rate-limited in the first place.
        out.update(_download_once(missing, period, interval))

    return out
