"""Data access — pulls daily OHLCV from Yahoo Finance via yfinance.

yfinance needs no API key, which makes it ideal for an unattended GitHub
Action. We download all tickers in one batched call and normalize the
result into a {ticker: DataFrame} mapping regardless of how many tickers
were requested.
"""

from __future__ import annotations

import pandas as pd
import yfinance as yf


def download(tickers: list[str], period: str = "6mo", interval: str = "1d") -> dict[str, pd.DataFrame]:
    """Return {ticker: OHLCV DataFrame}. Tickers with no data are skipped."""
    tickers = [t.strip().upper() for t in tickers if t.strip()]
    if not tickers:
        return {}

    raw = yf.download(
        tickers=tickers,
        period=period,
        interval=interval,
        auto_adjust=True,     # split/dividend adjusted; drops the 'Adj Close' col
        group_by="ticker",
        threads=True,
        progress=False,
    )

    out: dict[str, pd.DataFrame] = {}

    if isinstance(raw.columns, pd.MultiIndex):
        available = set(raw.columns.get_level_values(0))
        for t in tickers:
            if t not in available:
                continue
            sub = raw[t].dropna(how="all")
            if not sub.empty:
                out[t] = sub
    else:
        # Single ticker -> flat columns.
        sub = raw.dropna(how="all")
        if not sub.empty:
            out[tickers[0]] = sub

    return out
