"""Render scan results to Markdown, CSV and JSON in the public/ directory."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd


def _md_table(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No signals — no tickers returned usable data._\n"

    headers = [
        "#", "Ticker", "Price", "Score", "Squeeze",
        f"±{int(df['horizon_days'].iloc[0])}d", "Down (1σ)", "Up (1σ)", "Lean", "HV%",
    ]
    lines = ["| " + " | ".join(headers) + " |",
             "|" + "|".join(["---"] * len(headers)) + "|"]

    for _, r in df.iterrows():
        squeeze = f"🔒 {int(r['squeeze_days'])}d" if r["squeeze_on"] else "—"
        lean = {"Bullish": "▲ Bull", "Bearish": "▼ Bear", "Neutral": "• Neut"}[r["lean"]]
        lines.append(
            "| " + " | ".join([
                str(int(r["rank"])),
                str(r["ticker"]),
                f"{r['price']:,.2f}",
                f"**{r['score']:.0f}**",
                squeeze,
                f"{r['em_pct']:.1f}%",
                f"{r['down_1sigma']:,.2f}",
                f"{r['up_1sigma']:,.2f}",
                lean,
                f"{r['hv_annual']:.0f}",
            ]) + " |"
        )
    return "\n".join(lines) + "\n"


def write_reports(df: pd.DataFrame, outdir: str | Path, params: dict, top: int = 25) -> Path:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    horizon = params["horizon_days"]

    # Machine-readable copies (full set).
    if not df.empty:
        df.to_csv(outdir / "signals.csv", index=False)
        df.to_json(outdir / "signals.json", orient="records", indent=2)

    ranked = df.head(top) if not df.empty else df
    md = f"""# Short-Term Spread Scanner

_Last updated: **{now}** · horizon: **{horizon} trading days (~2 weeks)** · {len(df)} tickers scanned_

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
    (outdir / "index.html").write_text(_html_page(ranked, now, horizon, len(df)), encoding="utf-8")

    return report_path


def _html_page(df: pd.DataFrame, now: str, horizon: int, scanned: int) -> str:
    if df.empty:
        rows_html = '<tr><td colspan="10">No signals — no tickers returned usable data.</td></tr>'
    else:
        cells = []
        for _, r in df.iterrows():
            squeeze = f"🔒 {int(r['squeeze_days'])}d" if r["squeeze_on"] else "—"
            lean_cls = {"Bullish": "bull", "Bearish": "bear", "Neutral": "neut"}[r["lean"]]
            # Score heat: 0..100 -> hue green(140) at high, red(8) at low.
            hue = 8 + (r["score"] / 100) * 132
            cells.append(f"""<tr>
  <td class="num">{int(r['rank'])}</td>
  <td class="tkr">{r['ticker']}</td>
  <td class="num">{r['price']:,.2f}</td>
  <td class="num"><span class="score" style="background:hsl({hue:.0f} 70% 42%)">{r['score']:.0f}</span></td>
  <td>{squeeze}</td>
  <td class="num">±{r['em_pct']:.1f}%</td>
  <td class="num down">{r['down_1sigma']:,.2f}</td>
  <td class="num up">{r['up_1sigma']:,.2f}</td>
  <td class="lean {lean_cls}">{r['lean']}</td>
  <td class="num">{r['hv_annual']:.0f}</td>
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
  <div class="meta">Updated <b>{now}</b> · horizon <b>{horizon} trading days (~2 weeks)</b> · {scanned} halal-screened tickers</div>
  <div class="lede">
    Ranked by <b>Setup Score</b> — how coiled each name is now. High score = volatility
    compressed, a move loading. The <b>±</b> column and 1σ targets show how far price
    could travel <b>up or down</b> over the horizon (~68% within ±1σ). A <b>both-ways</b>
    (straddle-style) setup tool — direction is a weak hint only.
  </div>
  <table>
    <thead><tr>
      <th>#</th><th>Ticker</th><th>Price</th><th>Score</th><th>Squeeze</th>
      <th>±{horizon}d</th><th>Down 1σ</th><th>Up 1σ</th><th>Lean</th><th>HV%</th>
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
      <li><b>Squeeze 🔒</b> — Bollinger Bands have closed <i>inside</i> the Keltner Channels (the “TTM squeeze”). The number is how many days it has been building — longer = more stored energy.</li>
      <li><b>±{horizon}d</b> — the 1-sigma expected move over the horizon, as a % of price.</li>
    </ul></div>
    <div class="card"><ul>
      <li><b>Down 1σ / Up 1σ</b> — price targets one standard deviation either side. ~68% of moves land inside ±1σ, ~95% inside ±2σ.</li>
      <li><b>Lean</b> — a faint directional hint from squeeze momentum. A tiebreaker only — do <b>not</b> trade on it alone.</li>
      <li><b>HV%</b> — annualised historical volatility (how jumpy the name has been).</li>
    </ul></div>
  </div>
  <div class="formula">Score = compression (low Bollinger-bandwidth %ile, ≤35)
      + room to expand (low historical-vol %ile, ≤20)
      + active TTM squeeze, longer = more (≤45)

Expected move (1σ) = price × daily σ × √(horizon days)</div>

  <h2>How to use these signals</h2>
  <ol class="steps">
    <li><b>Start at the top.</b> The highest scores are the most coiled — where a move is most likely to fire soon. Treat the list as a watchlist, not buy orders.</li>
    <li><b>Trade the move, not a guess at direction.</b> This is a <b>both-ways</b> setup. It pairs naturally with non-directional option plays (a straddle / strangle), or with waiting for the breakout and trading whichever way it resolves.</li>
    <li><b>Use the ± band as your expected move.</b> Compare it to what options are pricing: if a straddle costs less than the 1σ band implies, the expected move may be “cheap”; if more, it’s “rich”. The Down / Up targets are your reference levels.</li>
    <li><b>Wait for the squeeze to <i>release</i>.</b> 🔒 with rising days means energy is building — not yet a trigger. The signal is the <i>fire</i>: when the bands expand back out. Many traders enter on the release, in the direction it breaks.</li>
    <li><b>Check the calendar first.</b> A big expected move <i>into earnings</i> is normal, not edge. Know the catalyst (earnings, Fed, product event) before assuming the squeeze tells the whole story.</li>
    <li><b>Manage risk.</b> The band is a probability, not a promise — roughly 1 move in 3 breaks outside ±1σ. Size positions so a 2σ move against you is survivable.</li>
    <li><b>Re-verify halal compliance.</b> The watchlist is an industry screen only; confirm each name’s financial ratios (Zoya / Musaffa) before trading, and purify incidental impure income.</li>
  </ol>

  <div class="legend">
    Raw data: <a href="signals.csv">signals.csv</a> · <a href="signals.json">signals.json</a>
    · auto-refreshed each weekday after the US close.
  </div>
  <div class="warn">
    ⚠️ Educational tool, <b>not financial advice</b>, and <b>not a fatwa</b>. The watchlist
    is an industry-exclusion (Shariah) screen only — full compliance depends on financial
    ratios that change quarterly; re-verify each name (Zoya / Musaffa) before trading.
  </div>
</div></body></html>"""
