"""Render scan results to Markdown, CSV and JSON in the public/ directory."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd


def _pct(v) -> str:
    """Format a fraction (0.02) as a percent ('2%'), or '—' if missing."""
    try:
        if v is None or pd.isna(v):
            return "—"
        return f"{float(v) * 100:.0f}%"
    except (TypeError, ValueError):
        return "—"


def _squeeze_cell(r) -> str:
    """Squeeze status: 🔥 = just fired (with break direction), 🔒 = building, — = none."""
    if bool(r.get("squeeze_fired", False)):
        arrow = {"up": "▲", "down": "▼"}.get(r.get("fired_dir", ""), "")
        return f"🔥 {arrow}".strip()
    if r["squeeze_on"]:
        return f"🔒 {int(r['squeeze_days'])}d"
    return "—"


def _earnings_window(horizon_days: int) -> int:
    """Approximate the trading-day horizon as a calendar-day window (~7/5)."""
    return round(horizon_days * 1.4)


def _earnings_cell(days, horizon_days: int) -> str:
    """'⚠ Nd' if earnings fall inside the horizon, plain 'Nd' if known but later, else '—'."""
    if days is None or pd.isna(days):
        return "—"
    days = int(days)
    if days < 0:
        return "—"
    return f"⚠ {days}d" if days <= _earnings_window(horizon_days) else f"{days}d"


def _implied_cell(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):.1f}%"


def _verdict_cell(v) -> str:
    return {"cheap": "🟢 cheap", "rich": "🔴 rich", "fair": "• fair"}.get(v, "—")


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No signals — no tickers returned usable data._\n"

    horizon = int(df["horizon_days"].iloc[0])
    has_halal = "debt_ratio" in df.columns
    has_earn = "earnings_in_days" in df.columns
    has_opt = "implied_move_pct" in df.columns

    headers = ["#", "Ticker", "Price", "Score", "Squeeze", f"±{horizon}d"]
    if has_opt:
        headers += ["Implied", "Vol"]
    headers += ["Down (1σ)", "Up (1σ)", "Lean", "HV%"]
    if has_earn:
        headers.append("Earnings")
    if has_halal:
        headers += ["Debt%", "Cash%"]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]

    for _, r in df.iterrows():
        lean = {"Bullish": "▲ Bull", "Bearish": "▼ Bear", "Neutral": "• Neut"}[r["lean"]]
        cells = [
            str(int(r["rank"])),
            str(r["ticker"]),
            f"{r['price']:,.2f}",
            f"**{r['score']:.0f}**",
            _squeeze_cell(r),
            f"{r['em_pct']:.1f}%",
        ]
        if has_opt:
            cells += [_implied_cell(r["implied_move_pct"]), _verdict_cell(r.get("vol_verdict"))]
        cells += [
            f"{r['down_1sigma']:,.2f}",
            f"{r['up_1sigma']:,.2f}",
            lean,
            f"{r['hv_annual']:.0f}",
        ]
        if has_earn:
            cells.append(_earnings_cell(r["earnings_in_days"], horizon))
        if has_halal:
            cells += [_pct(r["debt_ratio"]), _pct(r["cash_ratio"])]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _weights_line(weights: dict | None, as_of: str | None) -> str:
    if not weights:
        return ""
    w = weights
    src = f"auto-calibrated {as_of}" if as_of else "default"
    return (f"compression {w['compression']:.0%} · vol-room {w['vol_room']:.0%} · "
            f"squeeze {w['squeeze']:.0%} ({src})")


def write_reports(df: pd.DataFrame, outdir: str | Path, params: dict, top: int = 25,
                  weights: dict | None = None, weights_as_of: str | None = None) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    now_dt = dt.datetime.now(dt.timezone.utc)
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")           # static fallback text
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")       # parsed by the browser into local time
    horizon = params["horizon_days"]

    # Machine-readable copies (full set).
    if not df.empty:
        df.to_csv(outdir / "signals.csv", index=False)
        df.to_json(outdir / "signals.json", orient="records", indent=2)

    ranked = df.head(top) if not df.empty else df
    wline = _weights_line(weights, weights_as_of)
    md_weights = f"\n_Score weights: {wline}_\n" if wline else ""
    md = f"""# Short-Term Spread Scanner

_Last updated: **{now}** · horizon: **{horizon} trading days (~2 weeks)** · {len(df)} tickers scanned_
{md_weights}

Ranked by **Setup Score** — how coiled each name is right now. A high score means
volatility is compressed and a move is loading; the **±** column and the 1σ
targets show how far price could travel **up or down** over the horizon (≈68%
of the time within ±1σ, ≈95% within ±2σ). Direction is a weak hint only — this
is a *both-ways* (straddle-style) setup tool.

{_md_table(ranked)}

<details>
<summary>How to read this</summary>

- **Score** — 0–100. Compression (low Bollinger bandwidth) + room to expand
  (low historical-vol percentile) + active TTM squeeze. Higher = more coiled.
- **Squeeze** — 🔒 means Bollinger Bands are inside the Keltner Channels (the
  TTM squeeze is *on*); the number is how many days it has been building.
- **±{horizon}d** — 1-sigma expected move over the horizon, as a % of price.
- **Down/Up (1σ)** — price targets one standard deviation either side.
- **Lean** — faint directional hint from squeeze momentum; do not over-trust it.
- **HV%** — annualized historical volatility.

Full machine-readable output: [`signals.csv`](signals.csv) · [`signals.json`](signals.json)
</details>

> ⚠️ Educational tool, **not financial advice**. Expected-move bands are
> statistical estimates from past volatility, not predictions.
"""
    report_path = outdir / "report.md"
    report_path.write_text(md, encoding="utf-8")

    # GitHub Pages dashboard (self-contained, no external assets).
    (outdir / "index.html").write_text(
        _html_page(ranked, now_iso, now, horizon, len(df), wline), encoding="utf-8")

    return report_path


def _html_page(df: pd.DataFrame, now_iso: str, now_utc: str, horizon: int, scanned: int,
               weights_line: str = "") -> str:
    has_halal = (not df.empty) and ("debt_ratio" in df.columns)
    has_earn = (not df.empty) and ("earnings_in_days" in df.columns)
    has_opt = (not df.empty) and ("implied_move_pct" in df.columns)
    ncols = 10 + (2 if has_opt else 0) + (1 if has_earn else 0) + (2 if has_halal else 0)
    opt_head = "<th>Implied</th><th>Vol</th>" if has_opt else ""
    earn_head = "<th>Earnings</th>" if has_earn else ""
    halal_head = "<th>Debt%</th><th>Cash%</th>" if has_halal else ""

    if df.empty:
        rows_html = f'<tr><td colspan="{ncols}">No signals — no tickers returned usable data.</td></tr>'
    else:
        cells = []
        for _, r in df.iterrows():
            lean_cls = {"Bullish": "bull", "Bearish": "bear", "Neutral": "neut"}[r["lean"]]
            # Score heat: 0..100 -> hue green(140) at high, red(8) at low.
            hue = 8 + (r["score"] / 100) * 132
            fired = bool(r.get("squeeze_fired", False))
            sq_cls = ' class="fired"' if fired else ""
            verdict = r.get("vol_verdict")
            opt_cells = (
                f'\n  <td class="num">{_implied_cell(r["implied_move_pct"])}</td>'
                f'\n  <td class="vd-{verdict}">{_verdict_cell(verdict)}</td>' if has_opt else ""
            )
            earn_txt = _earnings_cell(r["earnings_in_days"], horizon) if has_earn else ""
            earn_cls = ' class="warn-cell"' if earn_txt.startswith("⚠") else ' class="num"'
            earn_cell = f'\n  <td{earn_cls}>{earn_txt}</td>' if has_earn else ""
            halal_cells = (
                f'\n  <td class="num">{_pct(r["debt_ratio"])}</td>'
                f'\n  <td class="num">{_pct(r["cash_ratio"])}</td>' if has_halal else ""
            )
            cells.append(f"""<tr>
  <td class="num">{int(r['rank'])}</td>
  <td class="tkr">{r['ticker']}</td>
  <td class="num">{r['price']:,.2f}</td>
  <td class="num"><span class="score" style="background:hsl({hue:.0f} 70% 42%)">{r['score']:.0f}</span></td>
  <td{sq_cls}>{_squeeze_cell(r)}</td>
  <td class="num">±{r['em_pct']:.1f}%</td>{opt_cells}
  <td class="num down">{r['down_1sigma']:,.2f}</td>
  <td class="num up">{r['up_1sigma']:,.2f}</td>
  <td class="lean {lean_cls}">{r['lean']}</td>
  <td class="num">{r['hv_annual']:.0f}</td>{earn_cell}{halal_cells}
</tr>""")
        rows_html = "\n".join(cells)

    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Halal Spread Scanner</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #0d1117; color: #e6edf3;
    font: 15px/1.5 -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif; }}
  .wrap {{ max-width: 1100px; margin: 0 auto; padding: 28px 18px 60px; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 4px; }}
  .meta {{ color: #8b949e; font-size: .85rem; margin-bottom: 18px; }}
  .lede {{ color: #c9d1d9; background: #161b22; border: 1px solid #30363d;
    border-radius: 10px; padding: 12px 16px; margin-bottom: 20px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: .92rem; }}
  thead th {{ position: sticky; top: 0; background: #161b22; text-align: left;
    padding: 9px 10px; border-bottom: 2px solid #30363d; font-size: .78rem;
    text-transform: uppercase; letter-spacing: .03em; color: #8b949e; }}
  tbody td {{ padding: 9px 10px; border-bottom: 1px solid #21262d; }}
  tbody tr:hover {{ background: #161b22; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .tkr {{ font-weight: 700; }}
  .score {{ display: inline-block; min-width: 34px; text-align: center;
    padding: 2px 8px; border-radius: 6px; font-weight: 700; color: #fff; }}
  .down {{ color: #f0816f; }} .up {{ color: #5fd07a; }}
  td.fired {{ font-weight: 700; color: #ffb454; }}
  td.warn-cell {{ text-align: right; color: #f0c992; font-weight: 600; }}
  td[class^="vd-"] {{ font-weight: 600; }}
  .vd-cheap {{ color: #5fd07a; }} .vd-rich {{ color: #f0816f; }} .vd-fair {{ color: #8b949e; }}
  .lean {{ font-weight: 600; }}
  .bull {{ color: #5fd07a; }} .bear {{ color: #f0816f; }} .neut {{ color: #8b949e; }}
  .legend {{ color: #8b949e; font-size: .82rem; margin-top: 18px; }}
  .legend code {{ background: #161b22; padding: 1px 6px; border-radius: 4px; }}
  .warn {{ margin-top: 22px; padding: 12px 16px; border-radius: 10px;
    background: #2d1d12; border: 1px solid #5a3a1a; color: #f0c992; font-size: .85rem; }}
  h2 {{ font-size: 1.1rem; margin: 30px 0 12px; }}
  .cols {{ display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }}
  @media (max-width: 720px) {{ .cols {{ grid-template-columns: 1fr; }} }}
  .card {{ background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 14px 16px; }}
  .card h3 {{ margin: 0 0 8px; font-size: .95rem; }}
  .card p {{ margin: 0; font-size: .88rem; color: #c9d1d9; }}
  .card ul {{ margin: 0; padding-left: 18px; }}
  .card li {{ margin: 5px 0; font-size: .88rem; color: #c9d1d9; }}
  .formula {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: .82rem; background: #0d1117; border: 1px solid #30363d;
    border-radius: 8px; padding: 11px 13px; color: #9fb4d0; white-space: pre-wrap;
    margin: 12px 0 0; }}
  ol.steps {{ counter-reset: step; list-style: none; padding: 0; margin: 0; }}
  ol.steps li {{ position: relative; padding: 11px 13px 11px 46px; margin: 9px 0;
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    font-size: .9rem; color: #c9d1d9; }}
  ol.steps li::before {{ counter-increment: step; content: counter(step);
    position: absolute; left: 12px; top: 10px; width: 24px; height: 24px;
    background: #1f6feb; color: #fff; border-radius: 50%; text-align: center;
    line-height: 24px; font-size: .82rem; font-weight: 700; }}
  a {{ color: #58a6ff; }}
</style></head>
<body><div class="wrap">
  <h1>📈 Halal Spread Scanner</h1>
  <div class="meta">Updated <b><span id="updated" data-utc="{now_iso}">{now_utc}</span></b> · horizon <b>{horizon} trading days (~2 weeks)</b> · {scanned} halal-screened tickers</div>
  <div class="lede">
    Ranked by <b>Setup Score</b> — how coiled each name is now. High score = volatility
    compressed, a move loading. The <b>±</b> column and 1σ targets show how far price
    could travel <b>up or down</b> over the horizon (~68% within ±1σ). A <b>both-ways</b>
    (straddle-style) setup tool — direction is a weak hint only.
  </div>
  <table>
    <thead><tr>
      <th>#</th><th>Ticker</th><th>Price</th><th>Score</th><th>Squeeze</th>
      <th>±{horizon}d</th>{opt_head}<th>Down 1σ</th><th>Up 1σ</th><th>Lean</th><th>HV%</th>{earn_head}{halal_head}
    </tr></thead>
    <tbody>
{rows_html}
    </tbody>
  </table>

  <h2>How it works</h2>
  <div class="cols">
    <div class="card">
      <h3>The idea — volatility cycles</h3>
      <p>Volatility breathes: it contracts, then expands. When a stock <b>coils</b>
      — its trading range squeezes tight and recent volatility drops to the low end
      of its own range — an outsized move usually follows within days. The catch is
      the <i>direction</i> isn't knowable in advance. So this tool ranks <b>how
      coiled</b> each name is and shows how far it could travel <b>both ways</b>.</p>
    </div>
    <div class="card">
      <h3>Where the numbers come from</h3>
      <p>Each weekday after the US close a GitHub Action pulls ~1 year of daily
      prices for every halal-screened ticker, recomputes the indicators (Bollinger
      Bands, Keltner Channels, TTM squeeze, ATR, historical volatility) with no
      look-ahead, and redeploys this page. Nothing to run by hand — just refresh.</p>
    </div>
  </div>

  <h2>What each column means</h2>
  <div class="cols">
    <div class="card"><ul>
      <li><b>Score (0–100)</b> — how coiled the name is right now; higher = a move is more imminent. The chip shades green→red with the score.</li>
      <li><b>Squeeze</b> — <b>🔒 Nd</b> = Bollinger Bands inside the Keltner Channels (the “TTM squeeze”) and building for N days. <b>🔥 ▲/▼</b> = the squeeze just <i>released</i> (the entry trigger) and which way it broke.</li>
      <li><b>±{horizon}d</b> — the 1-sigma <i>historical</i> expected move over the horizon, as a % of price.</li>
      <li><b>Implied / Vol</b> — the option-<i>implied</i> move and whether it's <span class="vd-cheap">cheap</span> / fair / <span class="vd-rich">rich</span> vs. history. Cheap = market underpricing the move (favours buying a straddle); rich = overpricing it. Priced for the top names only.</li>
    </ul></div>
    <div class="card"><ul>
      <li><b>Down 1σ / Up 1σ</b> — price targets one standard deviation either side. ~68% of moves land inside ±1σ, ~95% inside ±2σ.</li>
      <li><b>Earnings</b> — calendar days to the next report. <b>⚠</b> means it lands inside the horizon: a big expected move there is normal, not edge.</li>
      <li><b>Lean</b> — a faint directional hint from squeeze momentum. A tiebreaker only — do <b>not</b> trade on it alone.</li>
      <li><b>HV%</b> — annualised historical volatility (how jumpy the name has been).</li>
      <li><b>Debt% / Cash%</b> — the Shariah financial-ratio check: interest-bearing debt and cash as a share of market cap. Both must stay under ~33%. Every name shown has passed.</li>
    </ul></div>
  </div>
  <div class="formula">Score = compression (low Bollinger-bandwidth %ile, ≤35)
      + room to expand (low historical-vol %ile, ≤20)
      + active TTM squeeze, longer = more (≤45)

Expected move (1σ) = price × daily σ × √(horizon days)

Halal screen = permissible industry
      AND interest-bearing debt / market cap < 33%
      AND cash & equivalents / market cap   < 33%   (AAOIFI / S&P Islamic style)</div>

  <h2>How to use these signals</h2>
  <ol class="steps">
    <li><b>Start at the top.</b> The highest scores are the most coiled — where a move is most likely to fire soon. Treat the list as a watchlist, not buy orders.</li>
    <li><b>Trade the move, not a guess at direction.</b> This is a <b>both-ways</b> setup. It pairs naturally with non-directional option plays (a straddle / strangle), or with waiting for the breakout and trading whichever way it resolves.</li>
    <li><b>Use the ± band as your expected move.</b> Compare it to what options are pricing: if a straddle costs less than the 1σ band implies, the expected move may be “cheap”; if more, it’s “rich”. The Down / Up targets are your reference levels.</li>
    <li><b>Wait for the squeeze to <i>release</i>.</b> 🔒 with rising days means energy is building — not yet a trigger. The signal is the <i>fire</i>: when the bands expand back out. Many traders enter on the release, in the direction it breaks.</li>
    <li><b>Check the calendar first.</b> A big expected move <i>into earnings</i> is normal, not edge. Know the catalyst (earnings, Fed, product event) before assuming the squeeze tells the whole story.</li>
    <li><b>Manage risk.</b> The band is a probability, not a promise — roughly 1 move in 3 breaks outside ±1σ. Size positions so a 2σ move against you is survivable.</li>
    <li><b>Re-verify halal compliance.</b> The universe is fetched from Shariah ETFs and re-checked on industry + debt/cash ratios — but the 5%-income purification rule isn’t automated. Confirm each name and purify incidental impure income (Zoya / Musaffa).</li>
  </ol>

  <div class="legend">
    🔬 <a href="backtest.html">Does the score work? — backtest</a>
    · Raw data: <a href="signals.csv">signals.csv</a> · <a href="signals.json">signals.json</a>
    · auto-refreshed each weekday after the US close.
  </div>
  <div class="warn">
    ⚠️ Educational tool, <b>not financial advice</b>, and <b>not a fatwa</b>. The universe is
    pulled from Shariah-compliant ETFs and re-screened on industry + debt/cash ratios
    (an <i>approximation</i> of AAOIFI / S&P Islamic methodology). It does not automate the
    5%-income purification rule, and ratios change quarterly — re-verify each name (Zoya /
    Musaffa) before trading.
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
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit'
  }}) + ' (' + tz + ')';
  el.title = el.getAttribute('data-utc');
}})();
</script>
</body></html>"""
