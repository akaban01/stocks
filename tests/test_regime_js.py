"""The Market regime tab's builders (public/assets/regime.js), run under node.

Same arrangement as test_render_js.py: the page renders by concatenating strings
into innerHTML, so every payload string goes through these builders as an HTML
injection and must come out inert. The drawdown curve is the one number the page
derives itself, so it is checked against values worked out by hand.

Skipped where node is missing, like the other JavaScript tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REGIME_JS = ROOT / "public" / "assets" / "regime.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed; the builders are JavaScript")

EVIL = '"><img src=x onerror=alert(1)>'
ESCAPED = "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"


def call(expr: str, payload=None):
    script = f"""
      const g = require({json.dumps(str(REGIME_JS))});
      const d = {json.dumps(payload)};
      process.stdout.write(JSON.stringify({expr}));
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def assert_inert(html: str):
    assert "<img" not in html, html
    assert html.count("onerror") == html.count(ESCAPED), html


def _metrics(rule=EVIL):
    return {"rule": rule, "start": EVIL, "end": EVIL, "cagr": 0.1, "vol": 0.2, "sharpe": 0.5,
            "sortino": 0.7, "max_dd": -0.5, "calmar": 0.2, "worst_12m": -0.4, "pct_invested": 1,
            "switches": 3, "avg_hold_days": 100, "bh_sharpe": 0.4, "bh_cagr": 0.09, "bh_max_dd": -0.55}


def test_every_builder_escapes_the_payload():
    d = {
        "rows": [_metrics()],
        "order": {EVIL: 1},
        "crisis": {"windows": [EVIL], "rows": [{"rule": EVIL, "cells": [{"ret": -0.1, "max_dd": -0.2}]}]},
        "panel": {"title": EVIL, "start": EVIL, "end": EVIL, "bh_sharpe": 0.5, "grid": [[0.4, 0.6]]},
        "events": [{"date": EVIL, "below_prior_peak": -0.1, "1m": 0.01, "3m": -0.02, "6m": None,
                    "12m": 0.1, "max_dd_before_recovery": -0.3, "days_to_recover": None}],
        "pt": [{"date": EVIL, "days_to_peak": 3, "peak_date": EVIL, "days_to_trough": 9,
                "trough_date": EVIL, "peak_to_trough": -0.2, "window_complete": False}],
        "verdicts": {"verdicts": [{"rule": EVIL, "headline": EVIL, "note": EVIL, "lines": [EVIL]}]},
        "summary": {"costs": [0, 5], "summary": {"0": [_metrics()], "5": [_metrics()]}},
    }
    html = call("""[
      g.summaryTable(d.rows, d.order),
      g.longestTable(d.rows, d.order),
      g.crisisTable(d.crisis),
      g.heatmap(d.panel, [d.panel.title], [d.panel.title, d.panel.title], [0, 0]),
      g.eventTable(d.events),
      g.peakTroughTable(d.pt),
      g.bottomLine(d.verdicts, {}),
      g.verdictDetails(d.verdicts),
      g.costTable(d.summary),
      g.tipHtml(d.panel.title, [{name: d.panel.title, color: "#fff", value: 1}], String),
      g.lineChart([d.panel.title, "2020-01-03"], [{name: "x", values: [1, 2], color: "#fff"}],
                  {log: true, endLabels: true, label: d.panel.title}).svg
    ].join("")""", d)
    assert_inert(html)


def test_drawdowns_count_the_starting_capital_as_a_peak():
    # Falls 20% from the start, recovers past it, then falls 25% from the new high.
    dd = call("g.drawdowns([0.8, 1.0, 1.2, 0.9, null, 1.2])")
    assert dd[4] is None                                  # a gap stays a gap
    assert [dd[i] for i in (0, 1, 2, 3, 5)] == pytest.approx([-0.2, 0.0, 0.0, -0.25, 0.0])


def test_chart_hover_maps_positions_to_weeks_within_bounds():
    out = call("""(function () {
      var c = g.lineChart(["a", "b", "c", "d", "e"], [{name: "x", values: [1, 2, 3, 2, 1], color: "#fff"}],
                          {width: 400, height: 200, padRight: 20});
      return [c.indexAt(-50), c.indexAt(c.xAt(2)), c.indexAt(10000), c.svg.indexOf("rg-cross") > 0];
    })()""")
    assert out == [0, 2, 4, True]


def test_end_labels_are_pushed_apart_and_kept_in_the_plot():
    ys = call("g.spreadLabels([{y: 10}, {y: 11}, {y: 12}, {y: 100}], 13, 0, 105).map(function (e) { return e.y; })")
    assert all(b - a >= 13 - 1e-9 for a, b in zip(ys, ys[1:]))
    assert ys[0] >= 0 and ys[-1] <= 105


def test_drawdown_axis_ticks_are_whole_steps_from_zero():
    assert call("g.linearTicks(-0.552)") == [0, -0.1, -0.2, -0.3, -0.4, -0.5]
    assert call("g.linearTicks(-0.8)") == [0, -0.2, -0.4, -0.6, -0.8]


def test_only_a_beats_headline_is_shown_as_good():
    assert call('g.verdictTone("Beats buy-and-hold on a risk-adjusted basis after costs")') == "good"
    assert call('g.verdictTone("Higher Sharpe than buy-and-hold after costs, but within noise")') == "bad"
    assert call('g.verdictTone("Does not beat buy-and-hold")') == "bad"


def test_a_payload_from_another_schema_is_refused():
    assert call("g.schemaOk({regime_schema: g.SCHEMA})") is True
    assert call("g.schemaOk({regime_schema: g.SCHEMA + 1})") is False
    assert call("g.schemaOk(null)") is False


def test_the_page_wires_the_tab_and_loads_its_scripts_in_order():
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    core = (ROOT / "public" / "assets" / "app" / "core.js").read_text(encoding="utf-8")
    assert 'data-tab="regime"' in html and 'id="regime-body"' in html
    order = [html.index(f'src="assets/{s}"') for s in
             ("render.js", "regime.js", "app/core.js", "app/regime-tab.js", "app/boot.js")]
    assert order == sorted(order), "regime.js needs render.js, and the tab needs both before boot.js"
    assert '"regime"' in core and 'App.renderRegime()' in core


def test_heatmap_panels_share_one_colour_scale():
    # Same gap from each panel's own buy-and-hold Sharpe -> same shade, whichever panel.
    out = call("""(function () {
      var a = {grid: [[0.30, 0.25]], bh_sharpe: 0.25}, b = {grid: [[0.90, 0.70]], bh_sharpe: 0.80};
      var span = g.heatSpan([a, b]);
      return [span, g.heatStyle(0.30, 0.25, span), g.heatStyle(0.85, 0.80, span),
              g.heatStyle(0.25, 0.25, span)];
    })()""")
    span, first_half, second_half, equal = out
    assert span == pytest.approx(0.10)                  # the widest gap of either panel
    assert first_half == second_half
    assert equal == "background:rgb(56,56,53)"          # neutral grey at buy-and-hold


def test_heatmap_poles_are_not_any_rules_line_colour():
    lines = call("g.COLORS.concat([g.BENCH_COLOR])")
    poles = call("[g.heatStyle(1, 0, 1), g.heatStyle(-1, 0, 1)]")

    def rgb(h):
        return f"rgb({int(h[1:3], 16)},{int(h[3:5], 16)},{int(h[5:7], 16)})"
    assert not {f"background:{rgb(h)}" for h in lines} & set(poles)


def test_log_ticks_keep_decades_below_one_when_thinned():
    assert call("g.niceLogTicks(0.08, 30)") == [0.1, 1, 10]
    assert call("g.niceLogTicks(0.977, 20.1)") == [1, 2, 5, 10, 20]
