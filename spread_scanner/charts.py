"""Write the per-ticker price history to ``data/charts.json``.

Previously this module rendered a 900 KB page of inline SVG. It now emits the
series themselves and lets the frontend draw them — the same reason the rest of
the backend stopped producing HTML.

Each ticker gets a downsampled closing-price series (``DEFAULT_POINTS`` points,
first and last bar always kept so the endpoints stay honest) plus the summary
numbers a card wants: last price, window high/low, trailing-12-month change and
change over the whole window.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from .report import SCHEMA_VERSION, write_json

DEFAULT_POINTS = 220


# ---- small numeric helpers ------------------------------------------------

def _nearest_close_on_or_before(closes: pd.Series, when: pd.Timestamp):
    """Last close at or before ``when`` (so 1y-ago lands on a trading day)."""
    sub = closes[closes.index <= when]
    return float(sub.iloc[-1]) if not sub.empty else None


def _change_pct(closes: pd.Series, days: int | None = None):
    """Percent change over the trailing ``days`` calendar days (whole window if
    ``days`` is None). Returns None when there isn't enough history."""
    if closes is None or len(closes) < 2:
        return None
    last = float(closes.iloc[-1])
    if days is None:
        ref = float(closes.iloc[0])
    else:
        ref = _nearest_close_on_or_before(closes, closes.index[-1] - pd.Timedelta(days=days))
    if not ref:
        return None
    return round((last / ref - 1.0) * 100.0, 2)


def _closes(df: pd.DataFrame) -> pd.Series | None:
    """Extract a clean close-price series with a DatetimeIndex, or None."""
    if df is None or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    if len(s) < 2:
        return None
    s.index = pd.to_datetime(s.index)
    return s


def downsample(closes: pd.Series, points: int = DEFAULT_POINTS) -> pd.Series:
    """Thin the series to ~``points`` bars, always keeping the first and last."""
    n = len(closes)
    if n <= points:
        return closes
    step = n / points
    idx = sorted({int(i * step) for i in range(points)} | {0, n - 1})
    return closes.iloc[idx]


def series_payload(ticker: str, closes: pd.Series, points: int = DEFAULT_POINTS) -> dict:
    thin = downsample(closes, points)
    return {
        "ticker": ticker,
        "last": round(float(closes.iloc[-1]), 2),
        "low": round(float(closes.min()), 2),
        "high": round(float(closes.max()), 2),
        "change_1y_pct": _change_pct(closes, days=365),
        "change_window_pct": _change_pct(closes, days=None),
        "start": closes.index[0].strftime("%Y-%m-%d"),
        "end": closes.index[-1].strftime("%Y-%m-%d"),
        "bars": int(len(closes)),
        "dates": [d.strftime("%Y-%m-%d") for d in thin.index],
        "closes": [round(float(v), 2) for v in thin.tolist()],
    }


def build_charts(data: dict[str, pd.DataFrame], period_label: str = "",
                 points: int = DEFAULT_POINTS) -> dict:
    series = {t: s for t in sorted(data) if (s := _closes(data[t])) is not None}
    payload = [series_payload(t, s, points) for t, s in series.items()]

    if series:
        spans = [s.index for s in series.values()]
        window = {"start": min(idx[0] for idx in spans).strftime("%Y-%m-%d"),
                  "end": max(idx[-1] for idx in spans).strftime("%Y-%m-%d")}
    else:
        window = {"start": None, "end": None}

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "period": period_label,
        "window": window,
        "count": len(payload),
        "series": payload,
    }


def write_charts(data: dict[str, pd.DataFrame], outdir: str | Path,
                 period_label: str = "", points: int = DEFAULT_POINTS) -> Path:
    """Write ``<outdir>/data/charts.json``."""
    return write_json(Path(outdir) / "data" / "charts.json",
                      build_charts(data, period_label, points))
