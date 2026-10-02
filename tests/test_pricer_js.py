"""The Spread pricer's maths (public/assets/pricer.js), run under node.

The tab prices real orders from quotes the reader pastes, so the numbers are
checked against independent references: the Black-Scholes in conftest.py that
the scanner's own fixtures are priced with, textbook values, put-call parity,
and payoffs worked out by hand for all four vertical spreads.

Skipped where node is missing, like the other JavaScript tests.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import bs_price

ROOT = Path(__file__).resolve().parents[1]
PRICER_JS = ROOT / "public" / "assets" / "pricer.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed; the pricer is JavaScript")


def call(expr: str, payload=None):
    script = f"""
      const p = require({json.dumps(str(PRICER_JS))});
      const d = {json.dumps(payload)};
      process.stdout.write(JSON.stringify({expr}));
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# --- the model ---------------------------------------------------------------------

@pytest.mark.parametrize("right", ["call", "put"])
@pytest.mark.parametrize("strike", [80, 95, 100, 110, 130])
def test_black_scholes_matches_the_python_fixture_pricer(right, strike):
    # conftest.bs_price: zero rates, IV in percent, days to expiry over 365.
    want = bs_price(100.0, strike, 35.0, 45, right)
    got = call(f'p.bs("{right}", 100, {strike}, 45 / 365, 0, 0, 0.35)')
    assert got == pytest.approx(want, abs=1e-5)


def test_black_scholes_textbook_values_and_parity():
    c, p = call('[p.bs("call", 100, 100, 1, 0.05, 0, 0.2), p.bs("put", 100, 100, 1, 0.05, 0, 0.2)]')
    assert c == pytest.approx(10.4506, abs=1e-4) and p == pytest.approx(5.5735, abs=1e-4)
    # Put-call parity with a dividend yield: C − P = S·e^{−qT} − K·e^{−rT}.
    c, p = call('[p.bs("call", 50, 55, 0.5, 0.03, 0.02, 0.3), p.bs("put", 50, 55, 0.5, 0.03, 0.02, 0.3)]')
    assert c - p == pytest.approx(50 * math.exp(-0.01) - 55 * math.exp(-0.015), abs=1e-9)


def test_implied_vol_recovers_the_input_and_refuses_impossible_prices():
    vols = call("""[0.12, 0.35, 0.9].map(function (s) {
      return p.impliedVol("put", p.bs("put", 100, 90, 30 / 365, 0.04, 0, s), 100, 90, 30 / 365, 0.04, 0);
    })""")
    assert vols == pytest.approx([0.12, 0.35, 0.9], abs=1e-5)
    # A deep in-the-money put quoted below intrinsic: no volatility produces it.
    assert call('p.impliedVol("put", 1.5, 100, 110, 30 / 365, 0.04, 0)') is None
    assert call('p.impliedVol("call", 0, 100, 110, 30 / 365, 0.04, 0)') is None


# --- the chain ---------------------------------------------------------------------

def test_parse_chain_reads_a_broker_paste():
    out = call("p.parseChain(d)", "Strike\tBid\tAsk\tIV\n105\t$5.85\t$6.10\t31.5%\n"
                                  "95, 1.70, 1.85\n100 3.30 3.45 0.29\nfoo\n90, 0.9, 0.8\n95, 1, 2\n")
    assert [r["strike"] for r in out["rows"]] == [95, 100, 105]          # sorted
    by = {r["strike"]: r for r in out["rows"]}
    assert by[105]["iv"] == pytest.approx(0.315) and by[105]["bid"] == 5.85   # "%" and "$" read
    assert by[100]["iv"] == pytest.approx(0.29)                          # a decimal stays a decimal
    assert by[95]["iv"] is None
    errs = " ".join(out["errors"])
    assert "line 5" in errs and "line 6" in errs and "not a two-sided market" in errs
    assert "line 7" in errs and "already given" in errs
    assert "line 1" not in errs                                         # the header, skipped quietly


def test_a_quote_no_volatility_fits_is_shown_but_never_priced():
    out = call("""(function () {
      var ctx = {spot: 102, days: 30, rate: 0.04, div: 0, right: "put"};
      var rows = p.prepare(p.parseChain("95, 1.70, 1.85\\n100, 3.30, 3.45\\n110, 1, 2").rows, ctx);
      return [rows.map(function (r) { return r.usable; }), p.makeSpread(rows, "put", 110, 100),
              p.rankPairs(rows, ctx, 0.3, "credit", 10).map(function (x) { return [x.shortK, x.longK]; })];
    })()""")
    usable, spread, ranked = out
    assert usable == [True, True, False]
    assert spread is None
    assert all(110 not in pair for pair in ranked)


def test_atm_vol_interpolates_at_the_underlying():
    rows = [{"strike": 95, "iv": 0.30}, {"strike": 105, "iv": 0.20}]
    assert call("p.atmVol(d, 102.5)", rows) == pytest.approx(0.225)
    assert call("p.atmVol(d, 50)", rows) == pytest.approx(0.30)          # off the edge: nearest strike


# --- the four verticals, by hand --------------------------------------------------

CTX = {"spot": 100, "days": 30, "rate": 0.0, "div": 0.0}


def _spread(right, short_k, long_k, quotes):
    """quotes: {strike: (bid, ask)}; returns the spread and its metrics at mid and natural."""
    rows = [{"strike": k, "bid": b, "ask": a, "mid": (b + a) / 2, "iv": 0.3, "usable": True}
            for k, (b, a) in sorted(quotes.items())]
    d = {"rows": rows, "ctx": {**CTX, "right": right}, "s": short_k, "l": long_k}
    return call("""(function () {
      var sp = p.makeSpread(d.rows, d.ctx.right, d.s, d.l);
      return {sp: sp, mid: p.metricsAt(sp, sp.mid, d.ctx, 0.3), nat: p.metricsAt(sp, sp.natural, d.ctx, 0.3)};
    })()""", d)


def test_put_credit_spread():
    out = _spread("put", 95, 90, {95: (1.70, 1.90), 90: (0.80, 0.90)})
    sp, m = out["sp"], out["mid"]
    assert sp["kind"] == "credit" and sp["width"] == 5
    assert sp["mid"] == pytest.approx(1.80 - 0.85) and sp["natural"] == pytest.approx(1.70 - 0.90)
    assert m["maxProfit"] == pytest.approx(0.95) and m["maxLoss"] == pytest.approx(4.05)
    assert m["breakeven"] == pytest.approx(95 - 0.95)
    assert m["creditToWidth"] == pytest.approx(0.95 / 5)


def test_call_credit_spread():
    m = _spread("call", 105, 110, {105: (2.00, 2.20), 110: (0.90, 1.00)})["mid"]
    assert m["breakeven"] == pytest.approx(105 + 1.15) and m["maxLoss"] == pytest.approx(5 - 1.15)


def test_call_debit_spread():
    out = _spread("call", 110, 100, {100: (4.00, 4.20), 110: (0.90, 1.00)})
    sp, m = out["sp"], out["mid"]
    assert sp["kind"] == "debit" and sp["natural"] == pytest.approx(4.20 - 0.90)
    assert m["maxLoss"] == pytest.approx(4.10 - 0.95) and m["maxProfit"] == pytest.approx(10 - 3.15)
    assert m["breakeven"] == pytest.approx(100 + 3.15)
    assert m["creditToWidth"] is None


def test_put_debit_spread():
    m = _spread("put", 90, 100, {100: (4.00, 4.20), 90: (0.80, 0.90)})["mid"]
    assert m["breakeven"] == pytest.approx(100 - (4.10 - 0.85))


@pytest.mark.parametrize("right,short_k,long_k", [("put", 95, 90), ("call", 105, 110),
                                                  ("call", 110, 100), ("put", 90, 100)])
def test_probabilities_are_consistent(right, short_k, long_k):
    quotes = {90: (0.8, 0.9), 95: (1.7, 1.9), 100: (4.0, 4.2), 105: (2.0, 2.2), 110: (0.9, 1.0)}
    m = _spread(right, short_k, long_k, quotes)["mid"]
    assert 0 <= m["pMaxLoss"] <= 1 - m["pMaxProfit"] + 1e-12            # the two tails cannot overlap
    assert m["pMaxProfit"] <= m["pop"] + 1e-12                          # max profit is a subset of profit
    assert m["pop"] <= 1 - m["pMaxLoss"] + 1e-12


# --- ladder, fair value, ranking ----------------------------------------------------

def _chain_ctx():
    return {"text": "85, 0.38, 0.44\n90, 0.80, 0.90\n95, 1.70, 1.85\n100, 3.30, 3.45\n105, 5.85, 6.10",
            "ctx": {"spot": 102.4, "days": 30, "rate": 0.04, "div": 0, "right": "put"}}


def test_expected_value_is_zero_at_fair_value_and_falls_down_the_ladder():
    out = call("""(function () {
      var rows = p.prepare(p.parseChain(d.text).rows, d.ctx);
      var res = [];
      [[95, 90], [90, 95]].forEach(function (pair) {        // a credit and a debit spread
        var sp = p.makeSpread(rows, "put", pair[0], pair[1]);
        var fair = p.fairValue(sp, d.ctx, 0.35);
        res.push({kind: sp.kind, evAtFair: p.metricsAt(sp, fair, d.ctx, 0.35).ev,
                  ladder: p.ladder(sp, d.ctx, 0.35).map(function (m) { return m.ev; })});
      });
      return res;
    })()""", _chain_ctx())
    assert {o["kind"] for o in out} == {"credit", "debit"}
    for o in out:
        assert o["evAtFair"] == pytest.approx(0, abs=1e-9)
        assert all(a > b for a, b in zip(o["ladder"], o["ladder"][1:]))  # every concession costs


def test_walk_away_names_where_the_edge_runs_out():
    out = call("""(function () {
      var rows = p.prepare(p.parseChain(d.text).rows, d.ctx);
      var sp = p.makeSpread(rows, "put", 95, 90);
      // Pick the volatility that puts fair value exactly halfway between mid and natural.
      var target = (sp.mid + sp.natural) / 2, lo = 0.01, hi = 2;
      for (var i = 0; i < 80; i++) {
        var m = (lo + hi) / 2;
        if (p.fairValue(sp, d.ctx, m) < target) lo = m; else hi = m;
      }
      var s = (lo + hi) / 2;
      return [p.walkAway(sp, d.ctx, s), p.walkAway(sp, d.ctx, 0.05).status, p.walkAway(sp, d.ctx, 1.5).status];
    })()""", _chain_ctx())
    half, low_vol, high_vol = out
    assert half["status"] == "edge-inside" and half["fraction"] == pytest.approx(0.5, abs=1e-6)
    assert low_vol == "edge-at-natural"           # a calm model: the credit is worth more than anyone pays
    assert high_vol == "no-edge-at-mid"           # a wild model: not even mid is enough


def test_ranking_lists_only_the_asked_kind_best_first():
    out = call("""(function () {
      var rows = p.prepare(p.parseChain(d.text).rows, d.ctx);
      return ["credit", "debit"].map(function (k) {
        return p.rankPairs(rows, d.ctx, 0.35, k, 50).map(function (x) {
          return [p.makeSpread(rows, "put", x.shortK, x.longK).kind, x.m.evPerRisk, x.m.valid];
        });
      });
    })()""", _chain_ctx())
    for kind, rows in zip(("credit", "debit"), out):
        assert rows and all(r[0] == kind and r[2] for r in rows)
        scores = [r[1] for r in rows]
        assert scores == sorted(scores, reverse=True)
    assert len(out[0]) == 10                      # five strikes: ten put credit pairs, all tradable


# --- page ---------------------------------------------------------------------------

def test_html_from_a_hostile_paste_is_inert():
    html = call("""(function () {
      var parsed = p.parseChain(d);
      var ctx = {spot: 100, days: 30, rate: 0, div: 0, right: "put"};
      return p.chainTable(p.prepare(parsed.rows, ctx)) + parsed.errors.join("");
    })()""", '<img src=x onerror=alert(1)>, 1, 2\n95, 1.7, 1.85\n"><script>, 1, 2')
    assert "<img" not in html and "<script" not in html


def test_the_page_wires_the_tab_and_loads_its_scripts_in_order():
    html = (ROOT / "public" / "index.html").read_text(encoding="utf-8")
    core = (ROOT / "public" / "assets" / "app" / "core.js").read_text(encoding="utf-8")
    assert 'data-tab="pricer"' in html and 'id="pr-out"' in html
    order = [html.index(f'src="assets/{s}"') for s in
             ("render.js", "pricer.js", "app/core.js", "app/pricer-tab.js", "app/boot.js")]
    assert order == sorted(order)
    assert '"pricer"' in core and "App.renderPricer()" in core
