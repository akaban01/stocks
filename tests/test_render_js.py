"""The page's HTML builders, fed payloads that are nothing but injections.

The page (public/assets/app/) renders by concatenating strings into innerHTML, so every payload field
that reaches the page is one forgotten esc() away from being markup. Some of the
payload is text scraped from third-party pages. These tests run the real
`public/assets/render.js` under node with every string replaced by an HTML/
attribute injection and fail if any of it survives unescaped.

Skipped where node is missing, like the other JavaScript tests.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

RENDER_JS = Path(__file__).resolve().parents[1] / "public" / "assets" / "render.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed; the renderers are JavaScript")

EVIL = '"><img src=x onerror=alert(1)>'


def render(call: str, payload) -> str:
    script = f"""
      const r = require({json.dumps(str(RENDER_JS))});
      const d = {json.dumps(payload)};
      process.stdout.write(JSON.stringify({call}));
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


ESCAPED = "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"


def assert_inert(html: str):
    """No raw tag, and every copy of the payload is the fully escaped one — a
    copy that kept its quote would close the attribute it was written into."""
    assert "<img" not in html, html
    assert html.count("onerror") == html.count(ESCAPED), html


def _leg(**kw):
    leg = {"action": EVIL, "right": EVIL, "strike": 100, "expiry": EVIL, "qty": 1, "mid": 1.2,
           "bid": 1.1, "ask": 1.3, "iv": 30, "open_interest": 10, "label": EVIL,
           "mid_source": "last"}
    leg.update(kw)
    return leg


PLAN = {
    "name": EVIL, "thesis": EVIL, "playbook": EVIL, "expiry": EVIL, "dte": 21, "net": -120,
    "net_mid": -130, "net_natural": -100, "fill_basis": EVIL, "max_profit": 120, "max_loss": 380,
    "breakevens": [95.5], "pop": 0.6, "pop_basis": EVIL, "credit_to_width": 0.24, "risk": EVIL,
    "legs": [_leg(), _leg(expiry="2026-11-20")],
    "sizing": {"contracts": 1, "note": EVIL, "over_budget": False},
    "manage": {"profit_target": EVIL, "stop": EVIL, "time_stop": EVIL},
    "risk_form": {"tier": EVIL, "note": EVIL},
}


def test_the_order_table_escapes_every_field():
    assert_inert(render("r.legsTable(d)", PLAN))


def test_an_over_budget_size_escapes_its_note():
    assert_inert(render("r.sizeCell(d)", {"contracts": 0, "over_budget": True, "note": EVIL}))


def test_management_alternatives_and_risk_form_escape():
    assert_inert(render("r.manageBlock(d)", PLAN))
    assert_inert(render("r.altBlock([d])", {**PLAN, "legs": [_leg(right="call", action="sell")]}))
    assert_inert(render("r.riskFormNote(d)", PLAN))


def test_note_lists_escape_when_given_escaped_items():
    # noteList takes pre-escaped HTML by contract (some notes carry <b>); the
    # contract is that callers map esc over plain text first.
    assert_inert(render("r.noteList(d, [d, d].map(r.esc), d)", EVIL))


def test_validation_tables_escape_labels():
    bucket = {"label": EVIL, "bars": 10, "avg_abs_move_pct": 5, "expansion": 1.1,
              "broke_band_pct": 40, "broke_long_band_pct": 30}
    assert_inert(render("r.statsTable([d, d], d.label, d.label)", bucket))


def test_implied_section_escapes_every_label_and_note():
    group = {"label": EVIL, "n": 5, "beat_implied_pct": 40, "avg_straddle_return_pct": -10}
    imp = {"ok": True, "text": EVIL, "buckets": {k: group for k in
                                                 ("coiled", "calm", "cheap", "rich", "coiled_cheap")}}
    assert_inert(render("r.impliedSection(d)", imp))
    assert_inert(render("r.impliedSection(d)", {"ok": False, "note": EVIL, "logged_rows": 3,
                                                "first_date": EVIL}))


def test_a_last_trade_price_is_marked_in_the_order_table():
    html = render("r.legsTable(d)", PLAN)
    assert "last traded price" in html
    assert "mid $130.00, natural $100.00" in html


def test_direction_section_escapes_labels_and_text():
    read = {"label": EVIL, "n": 10, "hit_pct": 50, "base_pct": 49, "edge_pts": 1,
            "ci95_pts": [-3, 5], "proven": False}
    d = {"text": EVIL, "reads": {k: read for k in ("lean_bullish", "lean_bearish",
                                                   "fired_bullish", "fired_bearish")}}
    html = render("r.directionSection(d)", d)
    assert_inert(html)
    assert "not traded on" in html
