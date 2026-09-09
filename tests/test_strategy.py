"""The strategy engine: does the right instruction come out of each regime?"""

import datetime as dt
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
    short = [leg for leg in legs if leg["action"] == "sell"]
    width = max(abs(s["strike"] - leg["strike"])
                for s in short for leg in legs
                if leg["action"] == "buy" and leg["right"] == s["right"])
    credit = -plan["net"]
    assert credit > 0
    assert plan["max_profit"] == pytest.approx(credit, abs=0.01)
    assert plan["max_loss"] == pytest.approx(width * 100 - credit, abs=0.01)
    assert plan["credit_to_width"] == pytest.approx(credit / (width * 100), abs=0.001)


def test_debit_spread_arithmetic_is_internally_consistent():
    plan = rec({"iv": 18, "hv": 30, "iv_rank": 8},
               {"squeeze_on": False, "squeeze_fired": True, "fired_dir": "up"}).plan
    long_leg = [leg for leg in plan["legs"] if leg["action"] == "buy"][0]
    short_leg = [leg for leg in plan["legs"] if leg["action"] == "sell"][0]
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
    by = {(leg["action"], leg["right"]): leg["strike"] for leg in plan["legs"]}
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
        assert strategy.strike_step(chain["call"]) == pytest.approx(step)
    assert strategy.strike_step({}) == 1.0            # empty chain -> safe default


def test_wing_strike_is_floored_and_capped_in_strike_increments():
    # Short strike a long way from spot: the wing is capped at 6 increments.
    assert strategy.wing_strike(200.0, 100.0, 5.0, below=True) == 100.0 - 30.0
    # Short strike very close to spot: the wing is floored at 2 increments.
    assert strategy.wing_strike(200.0, 199.0, 5.0, below=True) == 199.0 - 10.0
    assert strategy.wing_strike(200.0, 220.0, 5.0, below=False) > 220.0


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
    """N(d₂) under a driftless price: E[S_T] = spot, so the *median* sits a
    little below spot and P(finish above spot) is a little under half."""
    # A band centred on the median (spot·e^{-σ²/2}) holds the textbook ~68%.
    med = 100 * math.exp(-0.2 ** 2 / 2)
    band = [med * math.exp(-0.2), med * math.exp(0.2)]
    assert strategy.pop_estimate(100.0, band, "inside", 0.2) == pytest.approx(0.683, abs=0.01)
    assert strategy.pop_estimate(100.0, band, "outside", 0.2) == pytest.approx(0.317, abs=0.01)
    # Finishing above spot itself is N(-σ/2), not one half — the Itô correction.
    assert strategy.pop_estimate(100.0, [100.0], "above", 0.2) == pytest.approx(
        0.5 * (1 + math.erf(-0.1 / math.sqrt(2))), abs=0.005)


def test_pop_is_not_inflated_by_dropping_the_ito_correction():
    """Regression. The helper once put the *median* at spot, which overstates the
    chance of finishing above any strike — harmless over a month, badly wrong
    over a year. The bias is directional, not uniformly optimistic: it inflated
    the structures that profit on the upside and deflated the ones that profit on
    the downside."""
    spot, strike = 200.0, 155.0

    def naive(sig):                      # what the helper used to return
        return 1 - 0.5 * (1 + math.erf((math.log(strike / spot) / sig) / math.sqrt(2)))

    month, year = 0.115, 0.429           # total sigma at ~24 and ~409 days, iv ~45%
    for sigma in (month, year):
        assert strategy.pop_estimate(spot, [strike], "above", sigma) < naive(sigma), \
            "the corrected model must never read higher than the naive one"

    # The error it removes is a rounding detail at a month and material at a year.
    short_gap = naive(month) - strategy.pop_estimate(spot, [strike], "above", month)
    long_gap = naive(year) - strategy.pop_estimate(spot, [strike], "above", year)
    assert short_gap < 0.03
    assert 0.06 < long_gap < 0.10
    assert long_gap > short_gap * 3


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
        assert payload["plan"]["risk_form"]["note"]
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


# --------------------------------------------- sizing against the risk budget

def _condor_plan(max_loss):
    """A defined-risk plan carrying just the fields size_position reads."""
    p = strategy.Plan(key="iron_condor", name="Iron Condor", action="SELL_PREMIUM",
                      bias="neutral", thesis="", playbook="", vega="short",
                      theta="positive", risk="defined")
    p.max_loss = max_loss
    p.net = -abs(max_loss) / 4
    return p


def test_sizing_takes_a_trade_that_fits_the_budget():
    """The README documents this exact case, so it is asserted rather than
    described: $792 of risk against a $1,000 budget is one contract."""
    out = strategy.size_position(_condor_plan(792.0), 1000.0)
    assert out["contracts"] == 1 and out["over_budget"] is False
    assert out["risk_per_spread"] == 792.0 and out["total_risk"] == 792.0


def test_sizing_refuses_a_trade_dearer_than_the_budget():
    out = strategy.size_position(_condor_plan(1350.0), 1000.0)
    assert out["contracts"] == 0 and out["over_budget"] is True
    assert "$1,350" in out["note"] and "$1,000" in out["note"]


def test_raising_the_budget_can_turn_a_refusal_into_a_position():
    """The whole point of the budget being configurable. GOOGL's long strangle
    on the 2026-09-03 scan risked $584: refused at $500, one contract at
    $1,000. Nothing about the trade changes — only what you will risk on it."""
    plan = _condor_plan(584.0)
    assert strategy.size_position(plan, 500.0)["contracts"] == 0
    assert strategy.size_position(plan, 1000.0)["contracts"] == 1


def test_sizing_never_suggests_more_than_the_cap():
    """A cheap spread against a large budget is still bounded."""
    out = strategy.size_position(_condor_plan(1.0), 1_000_000.0)
    assert out["contracts"] == strategy.MAX_CONTRACTS


# ------------------------------------------- the structures nothing asserted on
#
# `_covered_call` and `_calendar` both *executed* under the old suite — they are
# built on every rich name and every backwardated one — but nothing looked at
# the numbers they produced. A covered call reporting a $2,000,326 credit on a
# $200 stock passed CI for as long as it existed.

def _alt(rec_obj, key):
    return [a for a in rec_obj.alternatives if a["key"] == key][0]


def test_net_cost_ignores_shares_you_already_own():
    """`action="own"` is stock in the account, not a leg of this order. Priced
    as a sale it subtracted 100 shares at spot from the net."""
    call = strategy.Leg("sell", "call", 230.0, "2026-01-16", 1, 2.97, 2.9, 3.0, 45.0, 100, "")
    shares = strategy.Leg("own", "share", None, None, 100, 200.0, None, None, None, None, "")
    assert strategy.net_cost([call]) == -297.0
    assert strategy.net_cost([shares, call]) == -297.0


def test_net_cost_returns_none_when_a_traded_leg_has_no_mid():
    call = strategy.Leg("sell", "call", 230.0, "2026-01-16", 1, None, None, None, None, None, "")
    assert strategy.net_cost([call]) is None


def test_covered_call_is_priced_as_the_credit_it_collects():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    plan = _alt(r, "covered_call")
    call = [leg for leg in plan["legs"] if leg["right"] == "call"][0]
    shares = [leg for leg in plan["legs"] if leg["right"] == "share"][0]
    spot = 200.0

    credit = round(call["mid"] * 100, 2)
    assert plan["net"] == pytest.approx(-credit, abs=0.01)
    assert -2000 < plan["net"] < 0, "a covered call collects a credit, not a fortune"
    assert shares["action"] == "own" and shares["qty"] == 100
    # The whole position: called away at the strike, plus the premium.
    assert plan["max_profit"] == pytest.approx((call["strike"] - spot) * 100 + credit, abs=0.01)
    # And the real risk is the stock going to zero, less the premium.
    assert plan["max_loss"] == pytest.approx(spot * 100 - credit, abs=0.01)
    assert plan["breakevens"] == [pytest.approx(spot - call["mid"], abs=0.01)]
    assert plan["risk_form"]["tier"] == "covered"


def test_the_covered_call_order_text_quotes_the_real_credit():
    """The card prints this string verbatim."""
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    plan = _alt(r, "covered_call")
    assert plan["net"] < 0
    assert f"{-plan['net']:,.2f}" in strategy._order_text(
        strategy.Plan(**{**plan, "legs": [strategy.Leg(**leg) for leg in plan["legs"]]}))


def _calendar_rec():
    return rec({"iv": 31, "hv": 30, "iv_rank": 45, "term_slope": -0.08}, {"score": 40.0})


def test_calendar_is_a_debit_secured_by_the_long_leg():
    plan = _calendar_rec().plan
    assert plan["key"] == "calendar_spread"
    assert plan["net"] > 0, "a calendar is entered for a debit"
    assert plan["max_loss"] == pytest.approx(plan["net"], abs=0.01)
    assert plan["risk"] == "defined"
    # Not margin, and not shares: the short front call is covered by the long
    # back call at the same strike.
    assert plan["risk_form"]["tier"] == "option_covered"
    assert "not by margin" in plan["risk_form"]["note"]
    assert "long call" in plan["risk_form"]["note"]
    # The old copy was the credit-spread note, which says the opposite.
    assert "collect a premium" not in plan["risk_form"]["note"]


def test_calendar_quotes_no_probability_it_cannot_model():
    """POP is a terminal-price model. The calendar has two terminal dates, and
    the number it used to publish came from breakevens placed by hand at
    strike × (1 ± 0.6σ) — which the frontend printed exactly like a vertical's."""
    r = _calendar_rec()
    assert r.plan["pop"] is None
    assert r.plan["breakevens"] == []
    assert any("different dates" in w for w in r.warnings)


def test_calendar_legs_are_a_month_or_two_apart_not_a_year():
    """`exps[0]` took whichever expiry came first out of a dict that also holds
    the ≈13-month LEAPS chain — so a failed ~60-day fetch built a "calendar"
    against a leg a year out, silently."""
    plan = _calendar_rec().plan
    front, back = plan["legs"][0], plan["legs"][1]
    assert front["expiry"] < back["expiry"]
    assert front["strike"] == back["strike"]
    gap = (dt.date.fromisoformat(back["expiry"]) - dt.date.fromisoformat(front["expiry"])).days
    assert strategy.CALENDAR_MIN_GAP_DTE <= gap <= strategy.CALENDAR_MAX_GAP_DTE


def test_no_calendar_is_built_when_the_only_other_expiry_is_the_leaps_chain():
    v = make_view(iv=31, hv=30, iv_rank=45, term_slope=-0.08)
    back = [e for e in v.expiries if e["date"] not in (v.expiry, v.long_expiry)][0]
    del v.chain[back["date"]]
    v.expiries = [e for e in v.expiries if e["date"] != back["date"]]
    assert strategy._calendar(v, 0.1) is None


# --------------------------------------------------- a straddle needs one strike

def test_split_strikes_make_it_a_strangle_and_price_it_as_one():
    """When the nearest call and put strikes differ, both breakevens used to be
    computed off the call's — understating the lower one by the whole gap."""
    v = make_view(iv=18, hv=30, iv_rank=8)
    calls = v.chain[v.expiry]["call"]
    puts = v.chain[v.expiry]["put"]
    # Take the 200 put away so the nearest put is 195 while the nearest call is 200.
    puts.pop(200.0)
    plan = strategy._long_straddle(v, 0.1)

    assert plan.key == "long_strangle" and plan.name == "Long Strangle"
    debit = strategy.net_cost(plan.legs) / 100
    assert plan.breakevens == [pytest.approx(195 - debit, abs=0.01),
                               pytest.approx(200 + debit, abs=0.01)]
    assert calls[200.0].strike == 200.0      # the call leg is untouched


def test_a_straddle_with_one_strike_is_still_a_straddle():
    plan = strategy._long_straddle(make_view(iv=18, hv=30, iv_rank=8), 0.1)
    assert plan.key == "long_straddle"
    assert len({leg.strike for leg in plan.legs}) == 1


def test_alternatives_never_repeat_the_primary_structure():
    v = make_view(iv=18, hv=30, iv_rank=8)
    v.chain[v.expiry]["put"].pop(200.0)      # the straddle degrades to a strangle
    r = strategy.recommend(make_row(), v)
    keys = [r.plan["key"]] + [a["key"] for a in r.alternatives]
    assert len(keys) == len(set(keys))
