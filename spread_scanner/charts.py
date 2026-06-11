"""Render a year-to-year price chart for every ticker to ``charts.html``.

Self-contained, no external assets — each stock gets an inline-SVG line chart of
its closing price over the download window, with faint vertical gridlines marking
each calendar-year boundary so you can read the move *year to year*. The page is
built from already-downloaded OHLCV (no extra indicators), so it never needs the
scanner to have produced a signal for a name to show its chart.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd


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
    return (last / ref - 1.0) * 100.0


def _chg_span(pct, suffix: str) -> str:
    if pct is None:
        return f'<span class="chg neut">—{(" " + suffix) if suffix else ""}</span>'
    cls = "up" if pct >= 0 else "down"
    return f'<span class="chg {cls}">{pct:+.1f}%{(" " + suffix) if suffix else ""}</span>'


# ---- SVG line chart -------------------------------------------------------

def _sparkline(closes: pd.Series, width: int = 320, height: int = 116) -> str:
    """An inline SVG line chart of ``closes`` with year-boundary gridlines."""
    closes = closes.dropna()
    n = len(closes)
    if n < 2:
        return '<div class="nochart">not enough history</div>'

    pad_l, pad_r, pad_t, pad_b = 6, 6, 8, 18
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    base_y = pad_t + plot_h

    lo, hi = float(closes.min()), float(closes.max())
    if hi <= lo:
        hi = lo + 1.0

    def X(i: int) -> float:
        return pad_l + (i / (n - 1)) * plot_w

    def Y(v: float) -> float:
        return pad_t + (1 - (v - lo) / (hi - lo)) * plot_h

    vals = closes.tolist()
    pts = " ".join(f"{X(i):.1f},{Y(v):.1f}" for i, v in enumerate(vals))
    up = vals[-1] >= vals[0]
    color = "#5fd07a" if up else "#f0816f"

    # Soft area fill under the line.
    area = (f"M {X(0):.1f},{base_y:.1f} L " + pts.replace(" ", " L ")
            + f" L {X(n - 1):.1f},{base_y:.1f} Z")

    # Vertical gridline + label at each calendar-year boundary.
    idx = closes.index
    grid = [f'<text x="{X(0) + 2:.1f}" y="{height - 5}" class="yr">{idx[0].year}</text>']
    for k in range(1, n):
        if idx[k].year != idx[k - 1].year:
            x = X(k)
            grid.append(f'<line x1="{x:.1f}" y1="{pad_t}" x2="{x:.1f}" y2="{base_y:.1f}" class="grid"/>')
            grid.append(f'<text x="{x + 2:.1f}" y="{height - 5}" class="yr">{idx[k].year}</text>')
    grid_svg = "".join(grid)

    return (
        f'<svg viewBox="0 0 {width} {height}" class="chart" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg">'
        f'<line x1="{pad_l}" y1="{base_y:.1f}" x2="{width - pad_r}" y2="{base_y:.1f}" class="axis"/>'
        f'{grid_svg}'
        f'<path d="{area}" fill="{color}" fill-opacity="0.12"/>'
        f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.6" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'<circle cx="{X(n - 1):.1f}" cy="{Y(vals[-1]):.1f}" r="2.6" fill="{color}"/>'
        f'</svg>'
    )


def _card(ticker: str, closes: pd.Series) -> str:
    closes = closes.dropna()
    last = float(closes.iloc[-1])
    lo, hi = float(closes.min()), float(closes.max())
    yoy = _change_pct(closes, days=365)
    full = _change_pct(closes, days=None)
    return f"""    <div class="card">
      <div class="chd">
        <span class="tkr">{ticker}</span>
        <span class="px">{last:,.2f}</span>
        {_chg_span(yoy, "1y")}
      </div>
      {_sparkline(closes)}
      <div class="cft">range <b>{lo:,.2f} – {hi:,.2f}</b> · full {_chg_span(full, "")}</div>
    </div>"""


def _closes(df: pd.DataFrame) -> pd.Series | None:
    """Extract a clean close-price series with a DatetimeIndex, or None."""
    if df is None or "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    if len(s) < 2:
        return None
    s.index = pd.to_datetime(s.index)
    return s


def write_charts(data: dict[str, pd.DataFrame], outdir: str | Path,
                 period_label: str = "") -> Path:
    """Write ``charts.html`` — one year-to-year price chart per ticker."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    series = {t: s for t in sorted(data) if (s := _closes(data[t])) is not None}

    now_dt = dt.datetime.now(dt.timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    now_utc = now_dt.strftime("%Y-%m-%d %H:%M UTC")

    if series:
        cards = "\n".join(_card(t, s) for t, s in series.items())
        # Window actually shown (from the data), e.g. "Jun 2021 – Jun 2026".
        spans = [s.index for s in series.values()]
        start = min(idx[0] for idx in spans).strftime("%b %Y")
        end = max(idx[-1] for idx in spans).strftime("%b %Y")
        window = f"{start} – {end}"
    else:
        cards = '<p class="nochart">No price data available.</p>'
        window = period_label or "—"

    period_note = f" · {period_label} window" if period_label else ""
    html = _page(cards, now_iso, now_utc, len(series), window, period_note)
    path = outdir / "charts.html"
    path.write_text(html, encoding="utf-8")
    return path


def _page(cards: str, now_iso: str, now_utc: str, count: int, window: str,
          period_note: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Halal Spread Scanner — Price Charts</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#0d1117; color:#e6edf3;
    font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }}
  .wrap {{ max-width:1100px; margin:0 auto; padding:28px 18px 60px; }}
  h1 {{ font-size:1.5rem; margin:0 0 4px; }}
  .meta {{ color:#8b949e; font-size:.85rem; margin-bottom:18px; }}
  .lede {{ color:#c9d1d9; background:#161b22; border:1px solid #30363d;
    border-radius:10px; padding:12px 16px; margin-bottom:20px; }}
  .grid {{ display:grid; grid-template-columns:repeat(auto-fill, minmax(300px, 1fr));
    gap:14px; }}
  .card {{ background:#161b22; border:1px solid #30363d; border-radius:10px;
    padding:12px 14px; }}
  .chd {{ display:flex; align-items:baseline; gap:8px; margin-bottom:6px; }}
  .tkr {{ font-weight:700; font-size:1rem; }}
  .px {{ color:#c9d1d9; font-variant-numeric:tabular-nums; }}
  .chg {{ margin-left:auto; font-weight:600; font-size:.85rem;
    font-variant-numeric:tabular-nums; }}
  .chg.up {{ color:#5fd07a; }} .chg.down {{ color:#f0816f; }} .chg.neut {{ color:#8b949e; }}
  .chart {{ display:block; width:100%; height:auto; }}
  .chart .grid {{ stroke:#30363d; stroke-width:1; stroke-dasharray:2 3; }}
  .chart .axis {{ stroke:#21262d; stroke-width:1; }}
  .chart .yr {{ fill:#8b949e; font-size:9px;
    font-family:-apple-system,Segoe UI,Roboto,sans-serif; }}
  .cft {{ color:#8b949e; font-size:.8rem; margin-top:6px;
    font-variant-numeric:tabular-nums; }}
  .cft b {{ color:#c9d1d9; font-weight:600; }}
  .nochart {{ color:#8b949e; font-size:.85rem; padding:10px 0; }}
  a {{ color:#58a6ff; }}
</style></head><body><div class="wrap">
  <h1>📈 Price Charts — year to year</h1>
  <div class="meta">Updated <b><span id="updated" data-utc="{now_iso}">{now_utc}</span></b>
    · {count} tickers · {window}{period_note}
    · <a href="index.html">← back to scanner</a></div>
  <div class="lede">
    Closing price for every halal-screened name over the download window. Faint
    vertical lines mark each <b>calendar-year boundary</b> so you can read the move
    <b>year to year</b>; the <b>1y</b> badge is the trailing-12-month change and
    <b>full</b> is over the whole window. Green = up over the window, red = down.
  </div>
  <div class="grid">
{cards}
  </div>
</div>
<script>
(function(){{
  var el = document.getElementById('updated');
  if (!el) return;
  var d = new Date(el.getAttribute('data-utc'));
  if (isNaN(d.getTime())) return;
  var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
  el.textContent = d.toLocaleString(undefined, {{
    year:'numeric', month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'
  }}) + ' (' + tz + ')';
  el.title = el.getAttribute('data-utc');
}})();
</script>
</body></html>"""
