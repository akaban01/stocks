"""The strategy engine: does the right instruction come out of each regime?"""

import math

import pytest

from spread_scanner import strategy
from conftest import build_chain, make_row, make_view


def rec(view_kw=None, row_kw=None, **kw):
    return strategy.recommend(make_row(**(row_kw or {})), make_view(**(view_kw or {})), **kw)


# ------------------------------------------------------- the headline rule

def test_low_iv_buys_premium():
    r = rec({"iv": 18, "hv": 30, "iv_rank": 8})
    assert r.premium_state == "cheap"
    assert r.action == "BUY_PREMIUM"
    assert r.plan["vega"] == "long"
    assert r.plan["net"] > 0                      # you pay a debit to be long premium
    assert any("Selling premium" in a["name"] for a in r.avoid)


def test_high_iv_sells_premium():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    assert r.premium_state == "rich"
    assert r.action == "SELL_PREMIUM"
    assert r.plan["vega"] == "short"
    assert r.plan["net"] < 0                      # you collect a credit
    assert any("straddle" in a["name"].lower() for a in r.avoid)


def test_fair_iv_and_a_quiet_chart_stands_aside():
    r = rec({"iv": 31, "hv": 30, "iv_rank": 45}, {"score": 25.0, "squeeze_on": False})
    assert r.action == "STAND_ASIDE"
    assert r.confidence == 0.0
    assert "fairly priced" in r.plan["thesis"]


def test_fair_iv_but_coiled_buys_the_move_not_the_vol():
    r = rec({"iv": 31, "hv": 30, "iv_rank": 45}, {"score": 72.0, "squeeze_on": True})
    assert r.action == "BUY_PREMIUM"
    assert any("squeeze" in a["reason"].lower() for a in r.avoid)


# ------------------------------------------------------------- direction

def test_released_squeeze_promotes_a_directional_structure():
    up = rec({"iv": 18, "hv": 30, "iv_rank": 8},
             {"squeeze_on": False, "squeeze_fired": True, "fired_dir": "up"})
    assert up.bias == "bullish" and up.bias_strength == "strong"
    assert up.plan["key"] == "bull_call_spread"

    down = rec({"iv": 58, "hv": 28, "iv_rank": 88},
               {"squeeze_on": False, "squeeze_fired": True, "fired_dir": "down"})
    assert down.bias == "bearish"
    assert down.plan["key"] == "bear_call_spread"   # short premium, bearish side


def test_a_weak_lean_alone_does_not_pick_a_side():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88}, {"lean": "Bullish", "score": 30.0})
    assert r.bias_strength == "weak"
    assert r.plan["bias"] == "neutral"              # stays a condor, not a one-sided spread


def test_backwardation_at_fair_iv_runs_a_calendar():
    r = rec({"iv": 31, "hv": 30, "iv_rank": 45, "term_slope": -0.08}, {"score": 40.0})
    assert r.action == "NEUTRAL_INCOME"
    assert r.plan["key"] == "calendar_spread"
    assert r.plan["legs"][0]["expiry"] != r.plan["legs"][1]["expiry"]


# ------------------------------------------------------------- guardrails

def test_illiquid_chain_stands_aside_and_says_why():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88, "liquidity": "poor"})
    assert r.action == "STAND_ASIDE"
    assert "illiquid" in r.plan["thesis"]
    assert "24%" in r.plan["thesis"]                # the reason is the actual spread


def test_thin_chain_drops_the_condor_for_a_single_credit_spread():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88, "liquidity": "fair"})
    assert r.action == "SELL_PREMIUM"
    assert len(r.plan["legs"]) == 2
    assert any("Iron condor" == a["name"] for a in r.avoid)


def test_earnings_inside_the_expiry_warns_on_the_right_side():
    long_prem = rec({"iv": 18, "hv": 30, "iv_rank": 8}, {"earnings_in_days": 6.0})
    assert any("IV collapses" in w or "crush" in w for w in long_prem.warnings)

    short_prem = rec({"iv": 58, "hv": 28, "iv_rank": 88}, {"earnings_in_days": 6.0})
    assert any("gap" in w for w in short_prem.warnings)
    assert short_prem.plan["risk"] == "defined"     # never naked into a print


def test_naked_strangle_is_gated_behind_the_config_flag():
    off = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    assert all(a["key"] != "short_strangle" for a in off.alternatives)
    assert any("naked" in a["name"].lower() for a in off.avoid)

    on = rec({"iv": 58, "hv": 28, "iv_rank": 88}, allow_undefined_risk=True)
    assert any(a["key"] == "short_strangle" for a in on.alternatives)


def test_undefined_risk_alternative_is_labelled_undefined():
    on = rec({"iv": 58, "hv": 28, "iv_rank": 88}, allow_undefined_risk=True)
    naked = [a for a in on.alternatives if a["key"] == "short_strangle"][0]
    assert naked["risk"] == "undefined"
    assert naked["max_loss"] is None
    assert naked["sizing"]["contracts"] is None     # can't budget an open-ended loss


def test_no_option_view_reports_no_data_rather_than_guessing():
    r = strategy.recommend(make_row(), None)
    assert r.action == "NO_DATA"
    assert r.premium_state == "unknown"
    assert r.plan["legs"] == []
    assert r.confidence == 0.0


# ------------------------------------------------------- the numbers hold

def test_credit_spread_arithmetic_is_internally_consistent():
    plan = rec({"iv": 58, "hv": 28, "iv_rank": 88}).plan
    legs = plan["legs"]
    short = [l for l in legs if l["action"] == "sell"]
    width = max(abs(s["strike"] - l["strike"])
                for s in short for l in legs
                if l["action"] == "buy" and l["right"] == s["right"])
    credit = -plan["net"]
    assert credit > 0
    assert plan["max_profit"] == pytest.approx(credit, abs=0.01)
    assert plan["max_loss"] == pytest.approx(width * 100 - credit, abs=0.01)
    assert plan["credit_to_width"] == pytest.approx(credit / (width * 100), abs=0.001)


def test_debit_spread_arithmetic_is_internally_consistent():
    plan = rec({"iv": 18, "hv": 30, "iv_rank": 8},
               {"squeeze_on": False, "squeeze_fired": True, "fired_dir": "up"}).plan
    long_leg = [l for l in plan["legs"] if l["action"] == "buy"][0]
    short_leg = [l for l in plan["legs"] if l["action"] == "sell"][0]
    width = abs(short_leg["strike"] - long_leg["strike"])
    debit = plan["net"]
    assert 0 < debit < width * 100
    assert plan["max_loss"] == pytest.approx(debit, abs=0.01)
    assert plan["max_profit"] == pytest.approx(width * 100 - debit, abs=0.01)
    assert plan["breakevens"] == [pytest.approx(long_leg["strike"] + debit / 100, abs=0.01)]


def test_straddle_breakevens_straddle_the_strike():
    plan = rec({"iv": 18, "hv": 30, "iv_rank": 8}).plan
    strike = plan["legs"][0]["strike"]
    lo, hi = min(plan["breakevens"]), max(plan["breakevens"])
    assert lo < strike < hi
    assert plan["max_profit"] is None               # unlimited to the upside
    assert (hi - strike) == pytest.approx(strike - lo, abs=0.01)


def test_condor_wings_bracket_the_short_strikes():
    plan = rec({"iv": 58, "hv": 28, "iv_rank": 88}).plan
    by = {(l["action"], l["right"]): l["strike"] for l in plan["legs"]}
    assert by[("buy", "put")] < by[("sell", "put")]
    assert by[("sell", "call")] < by[("buy", "call")]
    assert by[("sell", "put")] < 200 < by[("sell", "call")]

    # Wings placed by width, not by another sigma: a 2σ wing would be far wider
    # than the short strike's own distance from spot and would swamp the credit.
    put_width = by[("sell", "put")] - by[("buy", "put")]
    assert put_width <= (200 - by[("sell", "put")])


def test_strike_step_reads_the_grid():
    for step in (1.0, 2.5, 5.0):
        chain = build_chain(spot=200.0, iv=40.0, dte=30, step=step)
        assert strategy._strike_step(chain["call"]) == pytest.approx(step)
    assert strategy._strike_step({}) == 1.0            # empty chain -> safe default


def test_wing_strike_is_floored_and_capped_in_strike_increments():
    # Short strike a long way from spot: the wing is capped at 6 increments.
    assert strategy._wing_strike(200.0, 100.0, 5.0, below=True) == 100.0 - 30.0
    # Short strike very close to spot: the wing is floored at 2 increments.
    assert strategy._wing_strike(200.0, 199.0, 5.0, below=True) == 199.0 - 10.0
    assert strategy._wing_strike(200.0, 220.0, 5.0, below=False) > 220.0


def test_sizing_respects_the_risk_budget():
    small = rec({"iv": 58, "hv": 28, "iv_rank": 88}, risk_budget=100.0).plan["sizing"]
    assert small["contracts"] == 0 and small["over_budget"] is True

    big = rec({"iv": 58, "hv": 28, "iv_rank": 88}, risk_budget=10_000.0).plan["sizing"]
    assert big["contracts"] >= 1
    assert big["total_risk"] <= 10_000.0


def test_probability_of_profit_is_a_probability_and_points_the_right_way():
    condor = rec({"iv": 58, "hv": 28, "iv_rank": 88}).plan
    straddle = rec({"iv": 18, "hv": 30, "iv_rank": 8}).plan
    for plan in (condor, straddle):
        assert 0.0 <= plan["pop"] <= 1.0
    # A 1σ condor wins most of the time; an ATM straddle needs a real move.
    assert condor["pop"] > 0.6
    assert straddle["pop"] < 0.5


def test_pop_helper_matches_the_normal_model():
    # ±1σ either side: ~68% inside, ~32% outside.
    assert strategy._pop(100.0, [100 * math.exp(-0.2), 100 * math.exp(0.2)],
                         "inside", 0.2) == pytest.approx(0.683, abs=0.01)
    assert strategy._pop(100.0, [100 * math.exp(-0.2), 100 * math.exp(0.2)],
                         "outside", 0.2) == pytest.approx(0.317, abs=0.01)
    assert strategy._pop(100.0, [100.0], "above", 0.2) == pytest.approx(0.5, abs=0.01)


# ------------------------------------------------------------ presentation

def test_every_recommendation_is_json_serializable_and_self_describing():
    import json
    for kw in ({"iv": 18, "hv": 30, "iv_rank": 8},
               {"iv": 58, "hv": 28, "iv_rank": 88},
               {"iv": 31, "hv": 30, "iv_rank": 45},
               {"iv": 58, "hv": 28, "iv_rank": 88, "liquidity": "poor"}):
        r = rec(kw)
        payload = json.loads(json.dumps(r.as_dict()))
        assert payload["headline"] and payload["detail"]
        assert payload["plan"]["compliance"]["note"]
        assert payload["why"]
        assert payload["plan"]["key"] in strategy.PLAYBOOK


def test_the_order_text_names_every_leg_and_the_net_price():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    for leg in r.plan["legs"]:
        assert f"{leg['strike']:g}" in r.detail
    assert "credit" in r.detail


def test_recommend_all_covers_every_row_even_unpriced_ones():
    rows = [make_row("AAA"), make_row("BBB"), make_row("CCC")]
    out = strategy.recommend_all(rows, {"AAA": make_view("AAA", iv=58, hv=28, iv_rank=88)})
    assert set(out) == {"AAA", "BBB", "CCC"}
    assert out["AAA"]["action"] == "SELL_PREMIUM"
    assert out["BBB"]["action"] == "NO_DATA"
