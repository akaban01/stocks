"""Run the whole regime backtest and write the report.

    python -m regime_backtest.run                 # cached data, 5 bp per side
    python -m regime_backtest.run --refresh       # refetch every source
    python -m regime_backtest.run --cost-bp 10
    python -m regime_backtest.run --json public/data/regime.json   # + the dashboard payload

Writes regime_backtest/output/{report.md, summary.csv, equity.png,
drawdowns.png, credit_heatmap.png}. Exits non-zero, with the reason, if a data
source fails beyond the allowed fallbacks.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from . import engine, events, metrics, report
from .data import DATA_DIR, DataError, Inputs, load_all

OUT_DIR = Path(__file__).resolve().parent / "output"
REPORT_COSTS = (0.0, 5.0, 20.0)

BH, SMA200, M10, CROSS = "Buy & hold", "SMA200 (daily)", "10-month SMA (monthly)", "Golden cross 50/200"
CREDIT, LEVEL, COMBO = "Credit velocity", "HY OAS level ≤ 500 bp", "10-month SMA + credit velocity"
CREDIT_SOURCE_LABEL = {"baa": "BAA10Y", "hy": "HY OAS"}
BAA_VERDICT_NOTE = ("Tested on BAA10Y, an investment-grade spread, because the full HY OAS history "
                    "isn't available. This verdict does not cover the high-yield version of the rule.")


def credit_label(base: str, kind: str) -> str:
    """A credit rule's display name carries the spread it was tested on, so a
    BAA10Y result can never be read as the high-yield rule's."""
    return f"{base} ({CREDIT_SOURCE_LABEL[kind]})"

# The credit grid. HY: exit / re-entry on the 22-day change in basis points.
# BAA10Y: exit on the z-score of that change; the brief gives no re-entry levels
# for z, so the grid mirrors HY's (re-entry at, and a little above, "no change").
HY_EXITS, HY_REENTRIES = [75, 100, 125, 150, 200], [0, 25, 50]
Z_EXITS, Z_REENTRIES = [1.5, 2.0, 2.5, 3.0], [0.0, 0.5, 1.0]
# The headline credit rule uses an interior setting of each grid, fixed before
# looking at any result — not the sweep's winner. (For HY it is the centre cell;
# the z grid has four exits, so 2.0 is the second, the one the event study uses.)
# The sweep and the out-of-sample split are where parameter choice is tested.
HY_DEFAULT, Z_DEFAULT = (125, 25), (2.0, 0.5)
HY_LEVEL_BP = 500
CAPITULATION_HY_BP, CAPITULATION_Z = 125, 2.0
MATERIAL = 0.03      # Sharpe; a smaller difference is reported as "about equal"
SPIKE_TOL = 0.05     # Sharpe; a neighbour within this of the best is "the same region"


CAVEATS = [
    "One market (US large caps), one ~30-year sample with a handful of bear markets. A rule that "
    "sidestepped 2008 once has one data point, not a track record.",
    "The 200-day and 10-month rules are not out-of-sample here: they were popularised after the "
    "decades they are tested on, and their parameters were chosen with that history known.",
    "Trades are assumed filled at the signal day's close. Costs are a flat per-side charge; taxes on "
    "switching in a taxable account are ignored and would hurt the switching rules.",
    "T-bill cash uses DTB3's discount rate on a 360-day basis, slightly understating the investment yield.",
    "VIX and VIX3M come from FRED and are lagged a day like every FRED series, so the capitulation "
    "screen uses the prior day's VIX against the current SPY close.",
]


@dataclass
class Study:
    inputs: Inputs
    cost_bp: float
    signals: dict[str, pd.Series]
    frames: dict[float, dict[str, pd.DataFrame]]          # cost -> name -> frame
    grid_frames: dict[tuple[float, float], pd.DataFrame]  # (exit, reentry) -> frame at cost_bp
    kind: str
    credit: str                                           # display name of the credit-velocity rule
    combo: str                                            # display name of the combined rule
    notes: list[str] = field(default_factory=list)


def credit_metric(inputs: Inputs, idx: pd.DatetimeIndex) -> pd.Series:
    aligned = engine.align_fred(inputs.credit.series, idx, lag=1)
    if inputs.credit.kind == "hy":
        return engine.credit_velocity_bp(aligned)
    return engine.credit_velocity_z(aligned)


def grid_axes(kind: str) -> tuple[list[float], list[float], tuple[float, float], str]:
    if kind == "hy":
        return HY_EXITS, HY_REENTRIES, HY_DEFAULT, "bp"
    return Z_EXITS, Z_REENTRIES, Z_DEFAULT, "z"


def build(inputs: Inputs, cost_bp: float) -> Study:
    close = inputs.spy
    idx = close.index
    spy_ret = close.pct_change()
    cash = engine.cash_returns(inputs.tbill, idx)
    kind = inputs.credit.kind
    vel = credit_metric(inputs, idx)
    exits, reentries, default, _ = grid_axes(kind)
    credit, combo = credit_label(CREDIT, kind), credit_label(COMBO, kind)

    sig = {
        BH: pd.Series(1.0, index=idx),
        SMA200: engine.sma_signal(close, 200),
        M10: engine.monthly_sma_signal(close),
        CROSS: engine.cross_signal(close, 50, 200),
        credit: engine.hysteresis(vel, *default),
    }
    notes = []
    if kind == "hy" and inputs.credit.series.index.min() <= pd.Timestamp("1998-12-31"):
        hy_bp = engine.align_fred(inputs.credit.series, idx, lag=1) * 100.0
        sig[LEVEL] = (hy_bp <= HY_LEVEL_BP).astype(float).where(hy_bp.notna())
    else:
        notes.append(f"Strategy 6 ({LEVEL}) was not run: it needs the full ICE BofA HY OAS "
                     "history, and the credit source in use is not that.")
    sig[combo] = engine.combine_all_in(sig[M10], sig[credit])

    costs = sorted(set(REPORT_COSTS) | {cost_bp})
    frames = {c: {n: engine.run(s, spy_ret, cash, c) for n, s in sig.items()} for c in costs}
    grid_frames = {(e, r): engine.run(engine.hysteresis(vel, e, r), spy_ret, cash, cost_bp)
                   for e in exits for r in reentries}
    return Study(inputs, cost_bp, sig, frames, grid_frames, kind, credit, combo, notes)


def slice_frames(frames: dict[str, pd.DataFrame], start, end) -> dict[str, pd.DataFrame]:
    return {n: f.loc[start:end] for n, f in frames.items()}


def common_window(frames: dict[str, pd.DataFrame]) -> tuple[pd.Timestamp, pd.Timestamp]:
    return max(f.index[0] for f in frames.values()), min(f.index[-1] for f in frames.values())


# --- sections ------------------------------------------------------------------------

def sweep_grid(study: Study, start=None, end=None) -> pd.DataFrame:
    exits, reentries, _, unit = grid_axes(study.kind)
    g = pd.DataFrame(index=[f"exit {e}{unit}" for e in exits],
                     columns=[f"re-enter <{r}{unit}" for r in reentries], dtype=float)
    for (e, r), f in study.grid_frames.items():
        sub = f.loc[start:end]
        g.loc[f"exit {e}{unit}", f"re-enter <{r}{unit}"] = metrics.sharpe(sub["ret"], sub["cash"])
    return g


def spike_assessment(grid: pd.DataFrame) -> str:
    vals = grid.to_numpy(dtype=float)
    r, c = np.unravel_index(np.nanargmax(vals), vals.shape)
    best = vals[r, c]
    nbrs = [vals[r + dr, c + dc] for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if 0 <= r + dr < vals.shape[0] and 0 <= c + dc < vals.shape[1]]
    close = sum(abs(best - v) <= SPIKE_TOL for v in nbrs)
    where = f"{grid.index[r]}, {grid.columns[c]}"
    nb = ", ".join(f"{v:.2f}" for v in nbrs)
    if close == len(nbrs):
        verdict = "a stable region: every adjacent cell is within"
    elif close == 0:
        verdict = "an isolated spike: no adjacent cell is within"
    else:
        verdict = f"partly isolated: {close} of {len(nbrs)} adjacent cells are within"
    return (f"Best cell: {where} (Sharpe {best:.2f}); adjacent cells {nb}. "
            f"This is {verdict} {SPIKE_TOL:.2f} Sharpe of the best. "
            f"Range across the whole grid: {np.nanmin(vals):.2f} to {best:.2f}.")


def oos(study: Study) -> dict:
    """Pick credit parameters on the first half of the credit rule's history
    only, then report the second half with them frozen."""
    base = study.frames[study.cost_bp][study.credit]
    start, end = base.index[0], base.index[-1]
    mid = start + (end - start) / 2
    first = sweep_grid(study, start, mid)
    second = sweep_grid(study, mid + pd.Timedelta(days=1), end)
    r, c = np.unravel_index(np.nanargmax(first.to_numpy(dtype=float)), first.shape)
    exits, reentries, _, _ = grid_axes(study.kind)
    chosen = (exits[r], reentries[c])
    rank = int((second.to_numpy(dtype=float) > second.iloc[r, c]).sum()) + 1
    return {"start": start, "mid": mid, "end": end, "first": first, "second": second,
            "chosen": chosen, "label": (first.index[r], first.columns[c]),
            "rank_second": rank, "cells": first.size}


def _versus(a: pd.DataFrame, b: pd.DataFrame) -> tuple[str, float, float, float]:
    """Classify Sharpe(a) − Sharpe(b): 'better' (material and the bootstrap
    interval excludes zero), 'noise+' (material, interval includes zero),
    'same' (smaller than MATERIAL), 'noise-', 'worse'."""
    d, lo, hi = metrics.sharpe_diff_ci(a, b)
    if abs(d) < MATERIAL:
        tag = "same"
    elif d > 0:
        tag = "better" if lo > 0 else "noise+"
    else:
        tag = "worse" if hi < 0 else "noise-"
    return tag, d, lo, hi


_TAG_TEXT = {
    "better": "higher, and the 90% interval excludes zero",
    "noise+": "higher, but the 90% interval includes zero — not distinguishable from luck",
    "same": f"no meaningful difference (under {MATERIAL:.2f})",
    "noise-": "lower, though the 90% interval includes zero",
    "worse": "lower, and the 90% interval excludes zero",
}


def verdict_parts(name: str, st: dict[str, dict], frames: dict[str, pd.DataFrame],
                  frames20: dict[str, pd.DataFrame], note: str = "") -> tuple[str, str]:
    """(headline, body) for one rule. The headline is what "Bottom line" shows
    and the Verdicts section bolds; the body is the evidence under it."""
    m, bh = st[name], st[BH]
    lines, tags = [], {}
    for other in (BH, SMA200, M10):
        if other == name:
            continue
        tag, d, lo, hi = _versus(frames[name], frames[other])
        tags[other] = tag
        lines.append(f"Sharpe {m['sharpe']:.2f} vs {st[other]['sharpe']:.2f} for {other} "
                     f"(difference {d:+.3f}, 90% CI {lo:+.2f} to {hi:+.2f}): {_TAG_TEXT[tag]}.")
    lines.append(f"CAGR {report.pct(m['cagr'])} vs {report.pct(bh['cagr'])}; max drawdown "
                 f"{report.pct(m['max_dd'])} vs {report.pct(bh['max_dd'])}; Calmar "
                 f"{report.num(m['calmar'])} vs {report.num(bh['calmar'])} (buy-and-hold).")
    s20, b20 = metrics.summarize(frames20[name]), metrics.summarize(frames20[BH])
    lines.append(f"At 20 bp per side: Sharpe {s20['sharpe']:.2f} vs {b20['sharpe']:.2f} for buy-and-hold.")

    t = tags[BH]
    if t == "better":
        head = "Beats buy-and-hold on a risk-adjusted basis after costs"
    elif t == "noise+":
        head = "Higher Sharpe than buy-and-hold after costs, but within noise"
    elif t == "same":
        head = "Does not beat buy-and-hold on a risk-adjusted basis — the Sharpe ratios are about equal"
    else:
        head = "Does not beat buy-and-hold on a risk-adjusted basis after costs"
    filters = [o for o in (SMA200, M10) if o != name]
    ftags = [tags[o] for o in filters]
    fname = "the other 200-day filter" if len(filters) == 1 else "the 200-day filters"
    if all(x == "better" for x in ftags):
        head += f", and beats {fname}."
    elif all(x in ("better", "noise+") for x in ftags):
        head += f"; edges {fname} by an amount within noise."
    elif any(x in ("worse", "noise-") for x in ftags):
        head += f"; does not beat {fname}."
    else:
        head += f"; no meaningful difference from {fname}."
    if m["max_dd"] > bh["max_dd"] + 0.10:
        dd = (f"a shallower worst drawdown ({report.pct(m['max_dd'], 0)} vs "
              f"{report.pct(bh['max_dd'], 0)})")
        gap = bh["cagr"] - m["cagr"]
        if gap > 0:
            head += f" Its main effect is {dd}, paid for with {report.pct(gap)} a year of return."
        else:
            head += f" It also had {dd}, and returned {report.pct(-gap)} a year more."
    body = "\n".join(f"- {x}" for x in lines)
    if note:
        body = f"{note}\n\n{body}"
    return head, body


def verdict_for(name: str, st: dict[str, dict], frames: dict[str, pd.DataFrame],
                frames20: dict[str, pd.DataFrame], note: str = "") -> str:
    head, body = verdict_parts(name, st, frames, frames20, note)
    return f"**{head}**\n\n{body}"


def write_report(study: Study, outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    inp = study.inputs
    cost = study.cost_bp
    full = study.frames[cost]
    cstart, cend = common_window(full)
    common = slice_frames(full, cstart, cend)
    common20 = slice_frames(study.frames[20.0], cstart, cend)
    st_long = {n: metrics.summarize(f) for n, f in full.items()}
    st_common = {n: metrics.summarize(f) for n, f in common.items()}
    exits, reentries, default, unit = grid_axes(study.kind)
    credit_desc = (f"exit when the 22-day change in HY OAS > {default[0]} bp, re-enter when < {default[1]} bp"
                   if study.kind == "hy" else
                   f"exit when the z-score of the 22-day change in BAA10Y > {default[0]}, "
                   f"re-enter when < {default[1]} (z over a trailing 756-day window)")

    # summary.csv: every strategy, every cost, both windows, plus the grid.
    rows = []
    for c, fr in study.frames.items():
        for label, frs in (("longest", fr), ("common", slice_frames(fr, cstart, cend))):
            for n, f in frs.items():
                rows.append({"strategy": n, "cost_bp": c, "window": label, **metrics.summarize(f)})
    for (e, r), f in study.grid_frames.items():
        rows.append({"strategy": f"{study.credit} exit {e}{unit} / re-enter {r}{unit}", "cost_bp": cost,
                     "window": "longest", **metrics.summarize(f)})
    pd.DataFrame(rows).to_csv(outdir / "summary.csv", index=False)

    # charts
    report.plot_equity(common, outdir / "equity.png",
                       f"Growth of $1, {cstart.date()} → {cend.date()}, {cost:g} bp per side")
    report.plot_drawdowns(common, outdir / "drawdowns.png",
                          f"Drawdowns, {cstart.date()} → {cend.date()}, {cost:g} bp per side")
    o = oos(study)
    grid_full = sweep_grid(study)
    bh_full = full[BH].loc[full[study.credit].index[0]:]
    bh_sh = lambda a, b: metrics.sharpe(bh_full.loc[a:b, "ret"], bh_full.loc[a:b, "cash"])  # noqa: E731
    report.plot_heatmaps(
        [(f"Full: {o['start'].date()} → {o['end'].date()}", grid_full, bh_sh(None, None)),
         (f"First half (selection): → {o['mid'].date()}", o["first"], bh_sh(None, o["mid"])),
         (f"Second half (out of sample): {o['mid'].date()} →", o["second"],
          bh_sh(o["mid"] + pd.Timedelta(days=1), None))],
        outdir / "credit_heatmap.png",
        xlabel=f"re-entry threshold ({unit})", ylabel=f"exit threshold ({unit})")

    # Each verdict is computed once; "Bottom line" and "Verdicts" both read it.
    verdicts = {}
    for n in full:
        if n == BH:
            continue
        note = BAA_VERDICT_NOTE if study.kind == "baa" and n in (study.credit, study.combo) else ""
        verdicts[n] = verdict_parts(n, st_common, common, common20, note)

    md: list[str] = []
    add = md.append
    add("# Market-regime rules vs buy-and-hold on SPY\n")
    add(f"Generated {report.utc_stamp()} UTC from commit {report.git_commit()}, by "
        f"`python -m regime_backtest.run`. Data through {inp.spy.index[-1].date()}. "
        f"Default cost {cost:g} bp per side on every switch (and on the initial purchase, "
        "buy-and-hold included). Every rule is 100% SPY or 100% 3-month T-bills; a signal from day "
        "t's close is traded at that close and applies from day t+1; FRED inputs carry one extra "
        "trading day of lag for publication. Sharpe and Sortino are on returns in excess of the "
        "T-bill rate. SPY prices are Yahoo's dividend-adjusted closes (total return).\n")

    add("## Bottom line\n")
    add(f"Credit spread used for the credit rules: **{inp.credit.source}**.\n")
    add("\n".join(f"- {n}: **{head}**" for n, (head, _) in verdicts.items()) + "\n")
    add("Details, assumptions and caveats follow below.\n")

    add("## How to read this\n")
    add(report.HOW_TO_READ + "\n")

    add("## Data sources and coverage\n")
    add(f"**Credit spread used: {inp.credit.source}.** Sources tried, in order:\n")
    add("\n".join(f"- {x}" for x in inp.credit.log) + "\n")
    if study.kind == "baa":
        add("Because this is BAA10Y and not HY OAS, the credit thresholds are z-scores of the 22-day "
            "change rather than basis points (BAA10Y moves far less than HY OAS, so the bp levels in "
            "the brief do not transfer). BAA10Y is an investment-grade spread; it is a weaker proxy "
            "for credit stress than high yield, and results for the credit rules should be read with "
            "that in mind.\n")
    for n in study.notes:
        add(f"- {n}")
    add("")
    cov = [["SPY (yfinance, auto_adjust=True)", inp.spy], ["DTB3 (T-bill, cash)", inp.tbill],
           ["VIXCLS", inp.vix], ["VXVCLS (VIX 3-month)", inp.vix3m], ["T10Y2Y", inp.t10y2y],
           [inp.credit.source, inp.credit.series]]
    add(report.md_table(["Series", "First", "Last", "Rows"],
                        [[n, s.index.min().date(), s.index.max().date(), f"{len(s):,}"] for n, s in cov]))
    add("")
    uses = {BH: "SPY", SMA200: "SPY", M10: "SPY", CROSS: "SPY",
            study.credit: f"SPY, {CREDIT_SOURCE_LABEL[study.kind]}",
            LEVEL: "SPY, HY OAS", study.combo: f"SPY, {CREDIT_SOURCE_LABEL[study.kind]}"}
    add("Each rule's history (first day a position is held → last day):\n")
    add(report.md_table(["Rule", "Inputs", "From", "To", "Trading days"],
                        [[n, uses[n], f.index[0].date(), f.index[-1].date(), f"{len(f):,}"]
                         for n, f in full.items()]))
    add(f"\nCommon window shared by all rules: **{cstart.date()} → {cend.date()}**.\n")
    add(f"Credit-velocity rule in the headline tables: {credit_desc}. This is an interior "
        "setting of the swept grid, fixed in advance — not the best cell of the sweep.\n")

    add(f"## Summary — common window, {cost:g} bp per side\n")
    add(report.summary_table(st_common) + "\n")
    add("![Growth of $1, log scale](equity.png)\n")
    add("![Drawdowns vs buy-and-hold](drawdowns.png)\n")
    add(f"## Summary — each rule over its longest history, {cost:g} bp per side\n")
    add("Not comparable across rows when start dates differ; buy-and-hold over each rule's own "
        "window is in the last columns.\n")
    rows = []
    for n, m in st_long.items():
        bh = metrics.summarize(full[BH].loc[full[n].index[0]:full[n].index[-1]])
        rows.append([n] + [f(m) for _, f in report.SUMMARY_COLS] +
                    [report.num(bh["sharpe"]), report.pct(bh["cagr"]), report.pct(bh["max_dd"])])
    add(report.md_table(["Strategy"] + [c for c, _ in report.SUMMARY_COLS] +
                        ["B&H Sharpe", "B&H CAGR", "B&H Max DD"], rows) + "\n")

    add("## Cost sensitivity — common window\n")
    hdr, rows = ["Strategy"], []
    for c in sorted(study.frames):
        hdr += [f"CAGR @{c:g}bp", f"Sharpe @{c:g}bp"]
    for n in full:
        row = [n]
        for c in sorted(study.frames):
            m = metrics.summarize(study.frames[c][n].loc[cstart:cend])
            row += [report.pct(m["cagr"]), report.num(m["sharpe"])]
        rows.append(row)
    add(report.md_table(hdr, rows) + "\n")

    add(f"## Crisis windows — return / max drawdown inside each window, {cost:g} bp per side\n")
    add("Each rule run over its longest history; n/a where its history does not cover the window.\n")
    rows = []
    for n, f in full.items():
        row = [n]
        for _, a, b in metrics.CRISIS_WINDOWS:
            w = metrics.window_stats(f, a, b)
            row.append(f"{report.pct(w['ret'])} / {report.pct(w['max_dd'])}")
        rows.append(row)
    add(report.md_table(["Strategy"] + [w[0] for w in metrics.CRISIS_WINDOWS], rows) + "\n")

    add(f"## Credit-velocity parameter sweep ({CREDIT_SOURCE_LABEL[study.kind]})\n")
    add(f"Sharpe at {cost:g} bp per side for every grid cell, over the credit rule's history "
        f"({o['start'].date()} → {o['end'].date()}).\n")
    add("![Credit parameter sweep: Sharpe by exit and re-entry threshold](credit_heatmap.png)\n")
    add(report.md_table([""] + list(grid_full.columns),
                        [[i] + [f"{v:.2f}" for v in grid_full.loc[i]] for i in grid_full.index]))
    add(f"\nBuy-and-hold Sharpe over the same span: {bh_sh(None, None):.2f}. "
        f"Cells beating it: {(grid_full.to_numpy() > bh_sh(None, None)).sum()} of {grid_full.size}.\n")
    add(spike_assessment(grid_full) + "\n")

    add(f"## Out-of-sample check: {study.credit}\n")
    first_bh, second_bh = bh_sh(None, o["mid"]), bh_sh(o["mid"] + pd.Timedelta(days=1), None)
    add(f"Parameters chosen by the highest Sharpe in the first half only "
        f"({o['start'].date()} → {o['mid'].date()}): **{o['label'][0]}, {o['label'][1]}** "
        f"(first-half Sharpe {o['first'].to_numpy().max():.2f} vs buy-and-hold {first_bh:.2f}). "
        f"Frozen and applied to the second half ({o['mid'].date()} → {o['end'].date()}):\n")
    s, e = o["mid"] + pd.Timedelta(days=1), o["end"]
    chosen = study.grid_frames[o["chosen"]].loc[s:e]
    rows = [[f"{study.credit}, chosen: {o['label'][0]}, {o['label'][1]}"] +
            [f(metrics.summarize(chosen)) for _, f in report.SUMMARY_COLS]]
    for n in (BH, SMA200, M10, study.credit):
        lab = n if n != study.credit else f"{study.credit}, default (not chosen)"
        rows.append([lab] + [f(metrics.summarize(full[n].loc[s:e])) for _, f in report.SUMMARY_COLS])
    add(report.md_table(["Strategy"] + [c for c, _ in report.SUMMARY_COLS], rows))
    add(f"\nIn the second half the chosen cell ranks {o['rank_second']} of {o['cells']} grid cells by "
        f"Sharpe; buy-and-hold's second-half Sharpe is {second_bh:.2f}. Second-half grid:\n")
    add(report.md_table([""] + list(o["second"].columns),
                        [[i] + [f"{v:.2f}" for v in o["second"].loc[i]] for i in o["second"].index]) + "\n")

    add("## Event studies\n")
    add(event_section(study) + "\n")

    add(f"## Verdicts — common window ({cstart.date()} → {cend.date()}), {cost:g} bp per side\n")
    add("\"The 200-day filters\" = the daily SMA200 rule and the monthly 10-month SMA (≈ 200 trading "
        "days). Confidence intervals are a paired circular block bootstrap (63-day blocks, 2,000 "
        f"resamples, fixed seed) of the Sharpe difference; a difference under {MATERIAL:.2f} is called "
        "\"no meaningful difference\". Verdicts are generated from the numbers "
        "above by fixed rules; nothing here was tuned.\n")
    for n, (head, body) in verdicts.items():
        add(f"### {n}\n")
        add(f"**{head}**\n\n{body}\n")

    add("## Caveats\n")
    add("\n".join(f"- {c}" for c in CAVEATS) + "\n")
    path = outdir / "report.md"
    path.write_text("\n".join(md), encoding="utf-8")
    return path


def event_data(study: Study) -> dict:
    """Both event studies as tables plus the sentences that explain them, so
    report.md and the dashboard's JSON describe them in the same words."""
    inp = study.inputs
    close = inp.spy
    idx = close.index
    sma200 = close.rolling(200, min_periods=200).mean()
    vix = engine.align_fred(inp.vix, idx)
    vix3m = engine.align_fred(inp.vix3m, idx)
    vel = credit_metric(inp, idx)
    thr = CAPITULATION_HY_BP if study.kind == "hy" else CAPITULATION_Z
    cond_txt = (f"HY OAS 22-day change > {thr} bp" if study.kind == "hy"
                else f"BAA10Y 22-day-change z-score > {thr}")
    caps = events.capitulation_dates(close, sma200, vix, vix3m, vel > thr)
    res = events.resteepening_dates(engine.align_fred(inp.t10y2y, idx))
    return {
        "capitulation": {
            "table": events.forward_table(close, caps),
            "text": (f"SPY close < SMA200, VIX > VIX3M, and {cond_txt}; first day of each cluster "
                     f"(clusters ≥ {events.CLUSTER_GAP} trading days apart). VIX3M starts "
                     f"{inp.vix3m.index.min().date()}, so nothing earlier can qualify."),
        },
        "resteepening": {
            "table": events.forward_table(close, res),
            "peak_trough": events.peak_trough(close, res),
            "text": (f"T10Y2Y turns positive after ≥ {events.MIN_INVERSION} consecutive trading days "
                     f"at or below zero (with at least one day inverted). SPY data starts "
                     f"{idx[0].date()}, so earlier re-steepenings are not covered."),
        },
        "forward_note": ("Forward returns are SPY total return from the signal day's close. Below peak "
                         "is how far SPY already was under its prior all-time high on the signal day; "
                         "max DD before recovery is the worst later close relative to the signal close, "
                         "until SPY regains that prior all-time high."),
        "peak_trough_note": (f"The trough is the lowest SPY close in the {events.PEAK_TROUGH_WINDOW} "
                             "trading days (~3 years) after the signal; the peak is the highest close "
                             "between the signal and that trough. A peak of 0 days means SPY never "
                             "closed above the signal day's level before the trough."),
    }


def event_section(study: Study) -> str:
    ev = event_data(study)
    cap, res = ev["capitulation"], ev["resteepening"]
    out = ["### Capitulation\n",
           f"{cap['text']} **{len(cap['table'])} events.** {ev['forward_note']}\n",
           _fwd_md(cap["table"]),
           "\n### Yield-curve re-steepening\n",
           f"{res['text']} **{len(res['table'])} events.**\n",
           _fwd_md(res["table"]),
           f"\nNext peak and trough. {ev['peak_trough_note']}\n"]
    rows = [[r.date, r.days_to_peak, r.peak_date, r.days_to_trough, r.trough_date,
             report.pct(r.peak_to_trough), "yes" if r.window_complete else "no — data ends first"]
            for r in res["peak_trough"].itertuples()]
    out.append(report.md_table(["Signal", "Days to peak", "Peak", "Days to trough", "Trough",
                                "Peak→trough", "Full 3y window"], rows) if rows else "_No events._")
    return "\n".join(out)


def _fwd_md(df: pd.DataFrame) -> str:
    if df.empty:
        return "_No events._"
    rows = []
    for r in df.to_dict("records"):
        rec = r["days_to_recover"]
        rec = "not yet" if rec is None or pd.isna(rec) else str(int(rec))
        rows.append([r["date"], report.pct(r["below_prior_peak"])] +
                    [report.pct(r[k]) for k in events.HORIZONS] +
                    [report.pct(r["max_dd_before_recovery"]), rec])
    return report.md_table(["Signal date", "Below peak", "1m", "3m", "6m", "12m",
                            "Max DD before recovery", "Trading days to prior peak"], rows)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Market-regime rules vs buy-and-hold on SPY")
    ap.add_argument("--refresh", action="store_true", help="refetch every source, ignore the cache")
    ap.add_argument("--cost-bp", type=float, default=5.0, help="cost per side per switch (default 5)")
    ap.add_argument("--outdir", type=Path, default=OUT_DIR)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--json", type=Path, default=None,
                    help="also write the dashboard payload here (e.g. public/data/regime.json)")
    args = ap.parse_args(argv)

    try:
        import matplotlib  # noqa: F401 — the charts need it; fail before any work
    except ImportError:
        print("STOPPED: matplotlib is not installed. Run: pip install -r requirements-backtest.txt",
              file=sys.stderr)
        return 2

    try:
        inputs = load_all(refresh=args.refresh, data_dir=args.data_dir)
    except DataError as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 2
    print(f"Credit source: {inputs.credit.source}")
    for line in inputs.credit.log:
        print(f"  {line}")
    study = build(inputs, args.cost_bp)
    path = write_report(study, args.outdir)
    print(f"Wrote {path} and charts/summary.csv in {args.outdir}")
    if args.json:
        from .export import write_payload  # imports this module; deferred to avoid a cycle
        path, written = write_payload(study, args.json)
        print(f"Wrote {path}" if written else f"{path} already holds these results; left unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
