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
    assert strategy.net_cost([call], slip=0) == -297.0
    assert strategy.net_cost([shares, call], slip=0) == -297.0
    assert strategy.net_cost([shares, call]) == strategy.net_cost([call])


def test_net_cost_prices_between_mid_and_natural():
    """A plan's numbers are only as good as the fill it assumes. Mid is the best
    case; the natural price (buy the ask, sell the bid) the worst."""
    buy = strategy.Leg("buy", "put", 90.0, "2026-01-16", 1, 1.00, 0.90, 1.10, 40.0, 100, "")
    sell = strategy.Leg("sell", "put", 100.0, "2026-01-16", 1, 3.00, 2.80, 3.20, 40.0, 100, "")
    assert strategy.net_cost([sell, buy], slip=0) == -200.0
    assert strategy.net_cost([sell, buy], slip=1) == -170.0          # 280 bid − 110 ask
    planned = strategy.net_cost([sell, buy])
    assert -200.0 < planned < -170.0
    assert planned == pytest.approx(-190.0, abs=0.01)                # a third of the way


def test_a_leg_priced_off_the_last_trade_is_flagged():
    q = strategy.Quote(strike=100.0, right="call", bid=None, ask=None, mid=2.5, last=2.5,
                       iv=30.0, open_interest=10, volume=0, mid_source="last")
    leg = strategy.make_leg("sell", q, "2026-01-16")
    assert leg.mid_source == "last"
    assert strategy.stale_legs([leg]) == [leg]
    assert strategy.net_cost([leg]) == -250.0          # no spread to cross — the mid


def test_net_cost_returns_none_when_a_traded_leg_has_no_mid():
    call = strategy.Leg("sell", "call", 230.0, "2026-01-16", 1, None, None, None, None, None, "")
    assert strategy.net_cost([call]) is None


def test_covered_call_is_priced_as_the_credit_it_collects():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    plan = _alt(r, "covered_call")
    call = [leg for leg in plan["legs"] if leg["right"] == "call"][0]
    shares = [leg for leg in plan["legs"] if leg["right"] == "share"][0]
    spot = 200.0

    credit = -plan["net"]
    # The planned fill sits between the bid and the mid for a sale.
    assert call["bid"] * 100 - 0.01 <= credit <= call["mid"] * 100 + 0.01
    assert -2000 < plan["net"] < 0, "a covered call collects a credit, not a fortune"
    assert shares["action"] == "own" and shares["qty"] == 100
    # The whole position: called away at the strike, plus the premium.
    assert plan["max_profit"] == pytest.approx((call["strike"] - spot) * 100 + credit, abs=0.01)
    # And the real risk is the stock going to zero, less the premium.
    assert plan["max_loss"] == pytest.approx(spot * 100 - credit, abs=0.01)
    assert plan["breakevens"] == [pytest.approx(spot - credit / 100, abs=0.01)]
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


def test_pop_reads_each_breakeven_at_its_own_strike_vol():
    """A put skew prices more vol at the downside breakeven than at the money,
    so an iron condor reaches that tail more often than a flat sigma says."""
    flat = strategy.pop_estimate(100.0, [90.0, 110.0], "inside", 0.08)
    skewed = strategy.pop_estimate(100.0, [90.0, 110.0], "inside", 0.08,
                                   sigma_at=lambda k: 0.12 if k < 100 else 0.08)
    assert skewed < flat
    # No IV at the strike -> the ATM sigma, unchanged.
    assert strategy.pop_estimate(100.0, [90.0, 110.0], "inside", 0.08,
                                 sigma_at=lambda k: None) == flat


def test_every_quoted_pop_says_it_assumes_holding_to_expiry():
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88})
    for plan in [r.plan, *r.alternatives]:
        if plan["pop"] is not None:
            assert "held to expiry" in plan["pop_basis"]
        # The planned fill always sits between the best case and the worst:
        # a larger number is always worse for you, debit or credit.
        if plan["net"] is not None and plan["net_natural"] is not None:
            assert plan["net_mid"] - 0.01 <= plan["net"] <= plan["net_natural"] + 0.01


# ------------------------------------------------ long vol needs evidence


def _implied(n, ret, key="coiled_cheap"):
    return {"implied": {"ok": True, "buckets": {key: {"label": "Coiled and cheap", "n": n,
                                                        "avg_straddle_return_pct": ret}}}}


def test_long_vol_evidence_prefers_the_implied_test_once_it_has_matured():
    lb = {"independent": {"long_band": {"edge_pts": 8.0, "ci95_pts": [3.0, 13.0]}}}
    # Implied says no, long band says yes: the implied test wins.
    ev = strategy.long_vol_evidence({**lb, **_implied(40, -12.0)})
    assert ev["supported"] is False and ev["source"] == "implied"
    assert strategy.long_vol_evidence(_implied(40, 5.0))["supported"] is True
    # Too few readings: fall back to the long band.
    ev = strategy.long_vol_evidence({**lb, **_implied(10, -50.0)})
    assert ev["supported"] is True and ev["source"] == "long_band"


def test_long_vol_evidence_needs_the_interval_to_clear_zero():
    bt = {"independent": {"long_band": {"edge_pts": 1.0, "ci95_pts": [-5.0, 6.0]}}}
    assert strategy.long_vol_evidence(bt)["supported"] is False
    assert strategy.long_vol_evidence(None) == {
        "supported": False, "source": "none",
        "text": "No backtest is available to show that buying premium on this setup pays."}
    old = strategy.long_vol_evidence({"ok": True, "verdict": {"holds": True}})
    assert old["supported"] is False and "predates" in old["text"]


NO = {"supported": False, "source": "long_band", "text": "no edge"}


def test_cheap_premium_without_evidence_offers_no_straddle():
    r = rec({"iv": 18, "hv": 30, "iv_rank": 8}, long_vol=NO)
    keys = {r.plan["key"], *(a["key"] for a in r.alternatives)}
    assert not keys & {"long_straddle", "long_strangle"}
    assert any("no edge" in a["reason"] for a in r.avoid)


def test_cheap_premium_with_evidence_still_buys_the_straddle():
    r = rec({"iv": 18, "hv": 30, "iv_rank": 8}, long_vol={**NO, "supported": True})
    keys = {r.plan["key"], *(a["key"] for a in r.alternatives)}
    assert keys & {"long_straddle", "long_strangle"}


# ------------------------------------------------------------ portfolio cap


def _trade(conf, risk, n):
    return {"action": "SELL_PREMIUM", "confidence": conf,
            "plan": {"sizing": {"risk_per_spread": risk, "contracts": n,
                                "total_risk": risk * n, "note": "orig"}}}


def test_portfolio_cap_funds_the_most_confident_trades_first():
    recs = {"A": _trade(0.9, 400, 2), "B": _trade(0.5, 300, 3), "C": _trade(0.7, 500, 1),
            "W": {"action": "STAND_ASIDE", "plan": {"sizing": {"contracts": 0}}}}
    out = strategy.apply_portfolio_cap(recs, 1500)
    # A (800) then C (500) fit; B gets what is left: 200 -> 0 contracts.
    assert recs["A"]["plan"]["sizing"]["contracts"] == 2
    assert recs["C"]["plan"]["sizing"]["contracts"] == 1
    b = recs["B"]["plan"]["sizing"]
    assert b["contracts"] == 0 and b["portfolio_capped"] and "cap" in b["note"]
    assert out == {"cap": 1500, "used": 1300.0, "capped": ["B"]}


def test_portfolio_cap_trims_rather_than_drops_when_part_fits():
    recs = {"A": _trade(0.9, 400, 2), "B": _trade(0.5, 300, 3)}
    strategy.apply_portfolio_cap(recs, 1500)
    assert recs["B"]["plan"]["sizing"]["contracts"] == 2
    assert recs["B"]["plan"]["sizing"]["total_risk"] == 600


def test_no_cap_changes_nothing():
    recs = {"A": _trade(0.9, 400, 5)}
    assert strategy.apply_portfolio_cap(recs, 0)["capped"] == []
    assert recs["A"]["plan"]["sizing"]["contracts"] == 5


# ------------------------------------------------- direction needs evidence


def _dir_bt(**proven):
    return {"direction": {"text": "t", "reads": {k: {"proven": v} for k, v in proven.items()}}}


def test_an_unproven_lean_is_treated_as_no_direction():
    row = make_row(lean="Bullish")
    ev = strategy.direction_evidence(_dir_bt(lean_bullish=False))
    assert strategy.effective_bias(row, ev)[:2] == ("neutral", "none")
    assert "not traded on" in strategy.effective_bias(row, ev)[2]
    ev = strategy.direction_evidence(_dir_bt(lean_bullish=True))
    assert strategy.effective_bias(row, ev) == ("bullish", "weak", "")
    # Ungated library callers keep the raw read.
    assert strategy.effective_bias(row, None)[:2] == ("bullish", "weak")


def test_no_backtest_proves_no_direction():
    ev = strategy.direction_evidence(None)
    assert ev["source"] == "none" and ev["proven"] == {}
    assert strategy.effective_bias(make_row(lean="Bearish"), ev)[:2] == ("neutral", "none")


def test_a_rich_name_with_an_unproven_lean_gets_a_non_directional_structure():
    ev = strategy.direction_evidence(_dir_bt(lean_bullish=False, lean_bearish=False))
    r = rec({"iv": 58, "hv": 28, "iv_rank": 88}, {"lean": "Bullish"}, direction=ev)
    assert r.bias == "neutral"
    assert r.plan["key"] in ("iron_condor", "bull_put_spread", "bear_call_spread")
    assert any("not traded on" in w for w in r.why)


# ------------------------------------------------- selling premium needs evidence


def _rich_bt(n, ret):
    return {"implied": {"ok": True, "buckets": {"rich": {"n": n, "avg_straddle_return_pct": ret}}}}


def test_short_vol_evidence_is_three_valued():
    assert strategy.short_vol_evidence(_rich_bt(40, -12.0))["supported"] is True
    assert strategy.short_vol_evidence(_rich_bt(40, 8.0))["supported"] is False
    assert strategy.short_vol_evidence(_rich_bt(10, -50.0))["supported"] is None   # too few
    assert strategy.short_vol_evidence(None)["supported"] is None


RICH = {"iv": 58, "hv": 28, "iv_rank": 88}


def test_selling_premium_is_withheld_once_it_is_shown_to_lose():
    r = rec(RICH, short_vol=strategy.short_vol_evidence(_rich_bt(40, 8.0)))
    assert r.action == "STAND_ASIDE"
    keys = {a["key"] for a in r.alternatives}
    assert not keys & {"iron_condor", "bull_put_spread", "bear_call_spread", "covered_call"}
    assert any("Selling premium" in a["name"] for a in r.avoid)


def test_untested_selling_goes_ahead_with_a_warning():
    ev = strategy.short_vol_evidence(None)
    r = rec(RICH, short_vol=ev)
    assert r.action == "SELL_PREMIUM"
    assert ev["text"] in r.warnings
    # Tested and paid: no warning.
    paid = rec(RICH, short_vol=strategy.short_vol_evidence(_rich_bt(40, -12.0)))
    assert paid.action == "SELL_PREMIUM" and not any("not been tested" in w for w in paid.warnings)


# ------------------------------------------- near-term directional spreads

def test_directional_spreads_list_all_four_verticals_with_no_size_when_nothing_is_picked():
    row, view = make_row(), make_view(iv=31, hv=30, iv_rank=45)
    r = strategy.recommend(row, view).as_dict()
    assert r["plan"]["key"] not in strategy.DIRECTIONAL_KEYS
    block = strategy.directional_spreads(row, view, r)
    assert [c["key"] for c in block["candidates"]] == strategy.DIRECTIONAL_KEYS
    assert block["preferred"] is None
    assert all(c["sizing"]["contracts"] is None for c in block["candidates"])
    assert "no proven directional read" in block["summary"]
    # One direction each: two bullish, two bearish, all on the near expiry.
    assert sorted(c["bias"] for c in block["candidates"]) == ["bearish", "bearish",
                                                               "bullish", "bullish"]
    assert {c["expiry"] for c in block["candidates"]} == {view.expiry}


def test_directional_spreads_pick_is_the_recommendations_own_plan_and_size():
    row = make_row(squeeze_on=False, squeeze_fired=True, fired_dir="up")
    view = make_view(iv=18, hv=30, iv_rank=8)
    r = strategy.recommend(row, view).as_dict()
    assert r["plan"]["key"] == "bull_call_spread"
    block = strategy.directional_spreads(row, view, r)
    assert block["preferred"] == "bull_call_spread"
    picked = next(c for c in block["candidates"] if c["key"] == "bull_call_spread")
    assert picked["sizing"] == r["plan"]["sizing"]
    assert picked["legs"] == r["plan"]["legs"]
    others = [c for c in block["candidates"] if c["key"] != "bull_call_spread"]
    assert others and all(c["sizing"]["contracts"] is None for c in others)


def test_directional_spreads_skip_unpriced_names_and_floor_iv():
    assert strategy.directional_spreads(make_row(), None) is None
    assert strategy.directional_spreads(make_row(), make_view(iv=0.5, hv=30)) is None
    rows = [make_row("AAA"), make_row("BBB")]
    out = strategy.directional_spreads_all(rows, {"AAA": make_view("AAA")})
    assert list(out) == ["AAA"]


def test_directional_spreads_warn_about_earnings_inside_the_expiry():
    block = strategy.directional_spreads(make_row(earnings_in_days=5.0), make_view(dte=24))
    assert any("earnings" in w for w in block["warnings"])


def test_illiquid_stand_aside_names_open_interest_when_the_market_is_tight():
    v = make_view(liquidity="good")
    v.atm_spread_pct, v.atm_open_interest, v.liquidity = 0.6, 10, "poor"
    r = strategy.recommend(make_row(), v)
    assert r.action == "STAND_ASIDE"
    assert "open interest" in r.plan["thesis"] and "bid/ask" not in r.plan["thesis"]


def test_illiquid_stand_aside_names_both_failures():
    v = make_view(liquidity="poor")
    v.atm_spread_pct, v.atm_open_interest = 24.0, 40
    thesis = strategy.recommend(make_row(), v).plan["thesis"]
    assert "bid/ask is ~24%" in thesis and "only 40 contracts" in thesis


def test_realized_basis_rank_at_the_floor_is_explained():
    v = make_view(iv=18, hv=30, iv_rank=0.0)
    why = " ".join(strategy.recommend(make_row(), v).why)
    assert "realized volatility" in why and "not 'cheap for this name'" in why
    v.iv_rank_basis = "implied"
    assert "not 'cheap for this name'" not in " ".join(strategy.recommend(make_row(), v).why)


def test_directional_spreads_flag_thin_credits():
    block = strategy.directional_spreads(make_row(), make_view())
    ctw = {c["key"]: c.get("credit_to_width") for c in block["candidates"]}
    assert ctw["bull_put_spread"] < strategy.MIN_CREDIT_TO_WIDTH
    assert any("Bull Put Spread takes only 9%" in w for w in block["warnings"])
