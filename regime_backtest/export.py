"""The regime backtest as one JSON payload, for the dashboard's Regime tab.

``python -m regime_backtest.run --json public/data/regime.json`` writes it. The
weekly workflow (.github/workflows/regime.yml) does that and commits the file;
the page renders it and computes nothing a reader could mistake for a result.

Everything here is derived from functions report.md already uses — the same
metrics, the same verdict headlines, the same event tables — so the two can only
disagree if one of them is stale.

What is deliberately NOT in the payload: any credit-spread value. The equity
curves and statistics are results; the spread series behind the credit rules is
licensed data (ICE, when HY OAS is in use) and stays out of anything published.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd

from . import metrics, report
from . import run as R

# Bump when a field the page reads changes meaning or moves. The page checks it
# and says so rather than rendering a payload it was not written for.
REGIME_SCHEMA = 1


def _clean(x):
    """JSON-safe: NaN/inf become null, numpy scalars become Python ones,
    timestamps become ISO dates."""
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, pd.Timestamp):
        return x.date().isoformat()
    if hasattr(x, "item"):                      # numpy scalar
        x = x.item()
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else round(x, 6)
    return x


def _sig(v: float, digits: int = 5) -> float | None:
    return None if v is None or not math.isfinite(v) else float(f"{v:.{digits}g}")


def _ckey(cost: float) -> str:
    """5.0 -> "5": the cost as the page's switch names it."""
    return f"{cost:g}"


def weekly_equity(by_cost: dict[str, dict[str, pd.DataFrame]]) -> dict:
    """Growth of $1 per rule and cost, sampled at each week's last trading day
    (and the final day). Every frame in the common window shares one index, so
    one date axis serves them all. The tables carry the exact daily statistics;
    the curves are for looking at — a weekly close can sit a little above the
    worst daily close of its week."""
    first = next(iter(next(iter(by_cost.values())).values()))
    idx = first.index
    take = pd.Series(range(len(idx)), index=idx).groupby(idx.to_period("W")).max().to_numpy()
    equity = {}
    for c, frames in by_cost.items():
        equity[c] = {}
        for name, f in frames.items():
            assert f.index.equals(idx), f"{name} is not on the common window's index"
            eq = (1.0 + f["ret"]).cumprod().to_numpy()
            equity[c][name] = [_sig(v, 4) for v in eq[take]]
    return {"dates": [d.date().isoformat() for d in idx[take]], "equity": equity}


def _metrics_row(name: str, m: dict) -> dict:
    keep = ("start", "end", "years", "cagr", "vol", "sharpe", "sortino", "max_dd", "calmar",
            "worst_12m", "pct_invested", "switches", "avg_hold_days")
    return {"rule": name, **{k: m.get(k) for k in keep}}


def _grid(g: pd.DataFrame) -> list[list[float | None]]:
    return [[_sig(float(v), 4) for v in row] for row in g.to_numpy(dtype=float)]


def _fwd_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for r in df.to_dict("records"):
        rec = r.get("days_to_recover")
        rows.append({
            "date": r["date"], "below_prior_peak": r["below_prior_peak"],
            **{k: r[k] for k in ("1m", "3m", "6m", "12m")},
            "max_dd_before_recovery": r["max_dd_before_recovery"],
            "days_to_recover": None if rec is None or pd.isna(rec) else int(rec),
        })
    return rows


def build_payload(study: R.Study, generated_at: str, commit: str) -> dict:
    inp = study.inputs
    cost = study.cost_bp
    full = study.frames[cost]
    cstart, cend = R.common_window(full)
    names = list(full)
    costs = sorted(study.frames)
    exits, reentries, default, unit = R.grid_axes(study.kind)
    is_credit = {study.credit, study.combo}

    common = {c: R.slice_frames(study.frames[c], cstart, cend) for c in costs}
    st_common = {c: {n: metrics.summarize(f) for n, f in common[c].items()} for c in costs}

    verdicts = []
    for n in names:
        if n == R.BH:
            continue
        note = R.BAA_VERDICT_NOTE if study.kind == "baa" and n in is_credit else ""
        head, body = R.verdict_parts(n, st_common[cost], common[cost], common[20.0], note)
        lines = [ln[2:] for ln in body.split("\n") if ln.startswith("- ")]
        verdicts.append({"rule": n, "headline": head, "note": note or None, "lines": lines})

    longest = []
    for n, f in full.items():
        bh = metrics.summarize(full[R.BH].loc[f.index[0]:f.index[-1]])
        longest.append({**_metrics_row(n, metrics.summarize(f)),
                        "bh_sharpe": bh["sharpe"], "bh_cagr": bh["cagr"], "bh_max_dd": bh["max_dd"]})

    crisis_rows = [{"rule": n, "cells": [metrics.window_stats(f, a, b)
                                         for _, a, b in metrics.CRISIS_WINDOWS]}
                   for n, f in full.items()]

    o = R.oos(study)
    grid_full = R.sweep_grid(study)
    bh_full = full[R.BH].loc[full[study.credit].index[0]:]

    def bh_sh(a, b):
        sub = bh_full.loc[a:b]
        return metrics.sharpe(sub["ret"], sub["cash"])

    second_start = o["mid"] + pd.Timedelta(days=1)
    chosen = study.grid_frames[o["chosen"]].loc[second_start:o["end"]]
    oos_rows = [_metrics_row(f"{study.credit}, chosen: {o['label'][0]}, {o['label'][1]}",
                             metrics.summarize(chosen))]
    for n in (R.BH, R.SMA200, R.M10, study.credit):
        lab = n if n != study.credit else f"{study.credit}, default (not chosen)"
        oos_rows.append(_metrics_row(lab, metrics.summarize(full[n].loc[second_start:o["end"]])))

    ev = R.event_data(study)
    uses = {R.BH: "SPY", R.SMA200: "SPY", R.M10: "SPY", R.CROSS: "SPY", R.LEVEL: "SPY, HY OAS",
            study.credit: f"SPY, {R.CREDIT_SOURCE_LABEL[study.kind]}",
            study.combo: f"SPY, {R.CREDIT_SOURCE_LABEL[study.kind]}"}
    cov = [("SPY (Yahoo, dividend-adjusted)", inp.spy), ("DTB3 (T-bill, cash)", inp.tbill),
           ("VIXCLS", inp.vix), ("VXVCLS (VIX 3-month)", inp.vix3m), ("T10Y2Y", inp.t10y2y),
           (inp.credit.source, inp.credit.series)]

    payload = {
        "regime_schema": REGIME_SCHEMA,
        "generated_at": generated_at,
        "commit": commit,
        "data_through": inp.spy.index[-1],
        "default_cost_bp": cost,
        "costs": costs,
        "credit": {
            "kind": study.kind,
            "label": R.CREDIT_SOURCE_LABEL[study.kind],
            "source": inp.credit.source,
            "log": inp.credit.log,
            "is_proxy": study.kind == "baa",
            "rule": (f"exit when the 22-day change in HY OAS > {default[0]} bp, re-enter when "
                     f"< {default[1]} bp" if study.kind == "hy" else
                     f"exit when the z-score of the 22-day change in BAA10Y > {default[0]}, re-enter "
                     f"when < {default[1]} (z over a trailing 756-day window)"),
        },
        "notes": study.notes,
        "coverage": [{"series": n, "first": s.index.min(), "last": s.index.max(), "rows": len(s)}
                     for n, s in cov],
        "rules": [{"name": n, "inputs": uses[n], "start": f.index[0], "end": f.index[-1],
                   "days": len(f), "credit": n in is_credit} for n, f in full.items()],
        "common_window": {"start": cstart, "end": cend},
        "verdicts": verdicts,
        "summary": {_ckey(c): [_metrics_row(n, st_common[c][n]) for n in names] for c in costs},
        "longest": longest,
        "crisis": {"windows": [w[0] for w in metrics.CRISIS_WINDOWS], "rows": crisis_rows},
        "sweep": {
            "unit": unit, "exits": exits, "reentries": reentries, "default": list(default),
            "row_labels": list(grid_full.index), "col_labels": list(grid_full.columns),
            "panels": [
                {"title": "Full history", "start": o["start"], "end": o["end"],
                 "bh_sharpe": bh_sh(None, None), "grid": _grid(grid_full)},
                {"title": "First half (selection)", "start": o["start"], "end": o["mid"],
                 "bh_sharpe": bh_sh(None, o["mid"]), "grid": _grid(o["first"])},
                {"title": "Second half (out of sample)", "start": second_start, "end": o["end"],
                 "bh_sharpe": bh_sh(second_start, None), "grid": _grid(o["second"])},
            ],
            "assessment": R.spike_assessment(grid_full),
        },
        "oos": {
            "first_start": o["start"], "mid": o["mid"], "second_start": second_start, "end": o["end"],
            "chosen": f"{o['label'][0]}, {o['label'][1]}",
            "first_sharpe": float(o["first"].to_numpy().max()), "first_bh_sharpe": bh_sh(None, o["mid"]),
            "rank_second": o["rank_second"], "cells": o["cells"],
            "second_bh_sharpe": bh_sh(second_start, None),
            "rows": oos_rows,
        },
        "events": {
            "forward_note": ev["forward_note"],
            "peak_trough_note": ev["peak_trough_note"],
            "capitulation": {"text": ev["capitulation"]["text"],
                             "rows": _fwd_rows(ev["capitulation"]["table"])},
            "resteepening": {"text": ev["resteepening"]["text"],
                             "rows": _fwd_rows(ev["resteepening"]["table"]),
                             "peak_trough": ev["resteepening"]["peak_trough"].to_dict("records")},
        },
        "series": weekly_equity({_ckey(c): common[c] for c in costs}),
        "glossary": [ln[2:] for ln in report.HOW_TO_READ.split("\n") if ln.startswith("- ")],
        "caveats": R.CAVEATS,
        "material_sharpe": R.MATERIAL,
    }
    return _clean(payload)


# Provenance, not results: a rerun that changes only these has nothing new to publish.
VOLATILE = ("generated_at", "commit")

# How stale the published data may be before the workflow refuses to commit it.
# SPY's last close vs the day of the run: a Saturday run sees Friday (1 day), a
# manual run after a long weekend about 4. Each other series vs SPY's last close:
# FRED posts the H.15 series (DTB3, T10Y2Y, BAA10Y) a business day late, so 1–3
# days behind is normal and a week is a feed that stopped.
MAX_SPY_AGE_DAYS = 5
MAX_SERIES_LAG_DAYS = 7


def staleness(payload: dict, today: dt.date) -> list[str]:
    """Why this payload should not be published, or [] if it is fresh.

    A history that ends weeks ago, or a FRED series that stopped updating (and
    is then forward-filled by the alignment), produces a perfectly well-formed
    payload — so shape checks pass it. This is the check that does not."""
    problems = []
    through = dt.date.fromisoformat(payload["data_through"])
    age = (today - through).days
    if age > MAX_SPY_AGE_DAYS:
        problems.append(f"SPY ends {through}, {age} days before {today}")
    for c in payload["coverage"]:
        last = dt.date.fromisoformat(c["last"])
        lag = (through - last).days
        if lag > MAX_SERIES_LAG_DAYS:
            problems.append(f"{c['series']} stops at {last}, {lag} days before SPY's last close")
    return problems


def _same_results(a: dict, b: dict) -> bool:
    strip = lambda d: {k: v for k, v in d.items() if k not in VOLATILE}  # noqa: E731
    return strip(a) == strip(b)


def write_payload(study: R.Study, path: Path) -> tuple[Path, bool]:
    """Write the payload, unless the file already holds the same results.

    Returns (path, written). Leaving an unchanged file alone is what makes
    "commit only if it changed" true in the workflow: generated_at and commit
    differ on every run, and a commit that only moves a timestamp still
    triggers a production deploy."""
    now = dt.datetime.now(dt.UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = build_payload(study, now, report.git_commit())
    if path.exists():
        try:
            if _same_results(json.loads(path.read_text(encoding="utf-8")), payload):
                return path, False
        except (OSError, ValueError):
            pass                              # unreadable: overwrite it
    path.parent.mkdir(parents=True, exist_ok=True)
    # Compact: the equity curves are most of the file, and it is committed weekly.
    path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False),
                    encoding="utf-8")
    return path, True
