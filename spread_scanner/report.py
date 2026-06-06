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
  <div class="legend">
    <b>Score</b> = compression (low Bollinger bandwidth %ile) + room to expand (low HV %ile)
    + active TTM squeeze. <b>🔒</b> = Bollinger Bands inside Keltner Channels.
    Data: <a href="signals.csv">signals.csv</a> · <a href="signals.json">signals.json</a>.
  </div>
  <div class="warn">
    ⚠️ Educational tool, <b>not financial advice</b>, and <b>not a fatwa</b>. The watchlist
    is an industry-exclusion (Shariah) screen only — full compliance depends on financial
    ratios that change quarterly; re-verify each name (Zoya / Musaffa) before trading.
  </div>
</div></body></html>"""
