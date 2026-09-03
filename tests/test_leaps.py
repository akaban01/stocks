"""Long-dated (≈13-month) spread engine.

Every test runs against the Black-Scholes-priced synthetic chains in
``conftest.py``, so the credits, breakevens and probabilities asserted here are
the ones real quotes would produce — no stubs, no network.
"""

import datetime as dt
import json
import math

import pytest

from conftest import make_row, make_view
from spread_scanner import leaps, options


def block(**kw):
    """The long-dated block for one ticker, with the view built from `kw`."""
    bias = kw.pop("bias", "neutral")
    strength = kw.pop("bias_strength", "none")
    budget = kw.pop("risk_budget", 2500.0)
    row = kw.pop("row", None) or make_row()
    return leaps.long_spreads(row, make_view(**kw), budget, bias, strength)


def plan_for(key, **kw):
    b = block(**kw)
    assert b is not None
    match = [c for c in b["candidates"] if c["key"] == key]
    assert match, f"{key} was not built; got {[c['key'] for c in b['candidates']]}"
    return match[0]


# ------------------------------------------------------------ expiry selection

def _out(days):
    """A listed expiry `days` calendar days from today."""
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def test_long_expiry_takes_the_listed_date_nearest_thirteen_months():
    # A realistic ladder: monthlies up front, then January-cycle LEAPS.
    chain = [_out(d) for d in (44, 135, 288, 505)]
    picked = options._long_expiry(chain, target_days=395, lo=270, hi=550)
    assert picked == (_out(288), 288), "288 is nearer to 395 than 505 is"


def test_long_expiry_ignores_dates_outside_the_window():
    """The nearest date to 13 months is not good enough on its own: a 200-day
    contract is not a 13-month contract, however close it is to the rest."""
    assert options._long_expiry([_out(44), _out(200)], target_days=395) is None   # too near
    assert options._long_expiry([_out(600), _out(900)], target_days=395) is None  # too far


def test_long_expiry_is_none_when_nothing_is_far_enough_out():
    """Better to say "no long-dated chain" than to price a 13-month plan on a
    four-month contract."""
    assert options._long_expiry([_out(44), _out(106)], target_days=395) is None


def test_no_long_chain_yields_no_block():
    assert block(long_dte=None) is None


def test_block_reports_the_real_days_not_the_target():
    b = block(long_dte=480)
    assert b["dte"] == 480
    assert b["target_days"] == leaps.LONG_TARGET_DAYS
    assert any("480 days out" in w for w in b["warnings"]), \
        "an expiry 85 days off the target has to say so"


def test_on_target_expiry_does_not_warn_about_the_date():
    b = block(long_dte=400)
    assert not any("nearest listed expiry" in w for w in b["warnings"])


# ---------------------------------------------------------- strike placement

def test_debit_vertical_strikes_go_by_moneyness_not_sigma():
    """One sigma over 13 months is 40%+ of spot on a volatile name. Placing the
    short leg there would build a spread so wide it is a synthetic long."""
    spot, iv, dte = 200.0, 45.0, 409
    one_sigma = spot * (iv / 100) * math.sqrt(dte / 365)
    assert one_sigma > spot * 0.35, "fixture must be volatile enough for this to matter"

    plan = plan_for("leaps_bull_call", spot=spot, iv=iv, long_dte=dte)
    long_k = plan["legs"][0]["strike"]
    short_k = plan["legs"][1]["strike"]
    assert long_k == pytest.approx(spot, abs=3)
    assert short_k == pytest.approx(spot * (1 + leaps.VERTICAL_WIDTH), abs=3)
    assert short_k - long_k < one_sigma


def test_credit_vertical_short_strike_sits_about_twenty_percent_out():
    plan = plan_for("leaps_bull_put", spot=200.0)
    short_k = plan["legs"][0]["strike"]
    long_k = plan["legs"][1]["strike"]
    assert short_k == pytest.approx(200.0 * (1 - leaps.CREDIT_OTM), abs=3)
    assert long_k < short_k, "the protective wing goes below the short put"


def test_bear_structures_mirror_the_bull_ones():
    bull = plan_for("leaps_bull_call", spot=200.0)
    bear = plan_for("leaps_bear_put", spot=200.0)
    assert bull["legs"][1]["strike"] > bull["legs"][0]["strike"]
    assert bear["legs"][1]["strike"] < bear["legs"][0]["strike"]
    assert bull["profit_zone"] == "above" and bear["profit_zone"] == "below"


# ------------------------------------------------------------- spread maths

def test_debit_vertical_arithmetic_closes():
    plan = plan_for("leaps_bull_call", spot=200.0)
    debit, long_k, short_k = plan["net"], plan["legs"][0]["strike"], plan["legs"][1]["strike"]
    width = (short_k - long_k) * 100
    assert debit > 0, "a bull call spread is paid for, not collected"
    assert plan["max_loss"] == pytest.approx(debit)
    assert plan["max_profit"] == pytest.approx(width - debit)
    assert plan["breakevens"] == [pytest.approx(long_k + debit / 100, abs=0.01)]
    assert plan["max_profit"] + plan["max_loss"] == pytest.approx(width)


def test_credit_vertical_arithmetic_closes():
    plan = plan_for("leaps_bull_put", spot=200.0)
    credit = -plan["net"]
    short_k, long_k = plan["legs"][0]["strike"], plan["legs"][1]["strike"]
    width = (short_k - long_k) * 100
    assert credit > 0, "a bull put spread collects"
    assert plan["max_profit"] == pytest.approx(credit)
    assert plan["max_loss"] == pytest.approx(width - credit)
    assert plan["credit_to_width"] == pytest.approx(credit / width, abs=1e-3)
    assert plan["breakevens"] == [pytest.approx(short_k - credit / 100, abs=0.01)]


def test_probability_of_profit_uses_the_long_expiry_not_the_front_month():
    """A year of sigma is far wider than a month of it. Pricing POP off the
    front expiry would make every long-dated spread look like a lock."""
    plan = plan_for("leaps_bull_put", spot=200.0, iv=45.0, long_dte=409)
    view = make_view(spot=200.0, iv=45.0, long_dte=409)
    long_sigma = (view.long_iv / 100) * math.sqrt(409 / 365)
    front_sigma = (view.iv_annual / 100) * math.sqrt(view.days_to_expiry / 365)
    assert long_sigma > front_sigma * 3

    short_k = plan["legs"][0]["strike"]
    credit = -plan["net"]
    be = short_k - credit / 100
    d2 = (math.log(200.0 / be) - long_sigma * long_sigma / 2) / long_sigma
    expected = 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
    assert plan["pop"] == pytest.approx(expected, abs=0.01)


def test_sizing_runs_against_the_long_dated_budget():
    small = plan_for("leaps_bull_call", risk_budget=400.0)
    large = plan_for("leaps_bull_call", risk_budget=8000.0)
    assert small["sizing"]["over_budget"] is True
    assert small["sizing"]["contracts"] == 0
    assert large["sizing"]["contracts"] > small["sizing"]["contracts"]
    assert large["sizing"]["total_risk"] <= 8000.0


# ------------------------------------------------------- poor man's covered call

def test_pmcc_buys_the_long_dated_leg_deep_in_the_money():
    view = make_view(spot=200.0)
    plan = plan_for("poor_mans_covered_call", spot=200.0)
    long_leg = plan["legs"][0]
    assert long_leg["action"] == "buy" and long_leg["right"] == "call"
    assert long_leg["strike"] == pytest.approx(200.0 * (1 - leaps.ITM_DEPTH), abs=3)
    assert long_leg["expiry"] == view.long_expiry


def test_pmcc_sells_the_front_month_because_that_is_where_theta_lives():
    view = make_view(spot=200.0)
    plan = plan_for("poor_mans_covered_call", spot=200.0)
    short_leg = plan["legs"][1]
    assert short_leg["action"] == "sell"
    assert short_leg["expiry"] == view.expiry
    assert short_leg["expiry"] != view.long_expiry
    assert short_leg["strike"] > view.spot, "the short call goes above the money"


def test_pmcc_is_secured_by_the_long_call_not_by_shares():
    """There are no shares in a diagonal. Calling it "covered" the way a real
    covered call is would misdescribe what happens on assignment."""
    plan = plan_for("poor_mans_covered_call")
    assert plan["risk_form"]["tier"] == "option_covered"
    assert "long call" in plan["risk_form"]["note"]
    assert "shares you already own" not in plan["risk_form"]["note"]
    assert not any(l["right"] == "share" for l in plan["legs"])


def test_pmcc_has_no_probability_of_profit():
    """Its legs expire thirteen months apart, so a single-sigma probability
    would be quietly wrong rather than merely imprecise."""
    plan = plan_for("poor_mans_covered_call")
    assert plan["pop"] is None
    for other in ("leaps_bull_call", "leaps_bull_put"):
        assert plan_for(other)["pop"] is not None


def test_pmcc_max_profit_is_the_assigned_case_and_says_so():
    plan = plan_for("poor_mans_covered_call", spot=200.0)
    long_k, short_k = plan["legs"][0]["strike"], plan["legs"][1]["strike"]
    assert plan["max_profit"] == pytest.approx((short_k - long_k) * 100 - plan["net"])
    assert plan["max_loss"] == pytest.approx(plan["net"])
    warnings = block(spot=200.0)["warnings"]
    assert any("conservative case" in w and "rolling" in w.lower() for w in warnings)


def test_pmcc_is_skipped_when_the_long_leg_would_not_be_deep_enough():
    """With a coarse strike grid there may be no strike ~20% in the money that
    still sits meaningfully below spot; the structure is dropped, not faked."""
    b = block(spot=200.0, step=200.0)
    assert "poor_mans_covered_call" not in [c["key"] for c in b["candidates"]]


# -------------------------------------------------------------- the selection

@pytest.mark.parametrize("state,bias,expected", [
    ("cheap", "bullish", "leaps_bull_call"),
    ("fair", "bullish", "leaps_bull_call"),
    ("rich", "bullish", "leaps_bull_put"),
    ("cheap", "bearish", "leaps_bear_put"),
    ("fair", "bearish", "leaps_bear_put"),
    ("rich", "bearish", "leaps_bear_call"),
    ("cheap", "neutral", None),
    ("rich", "neutral", None),
])
def test_preferred_follows_direction_first_then_premium(state, bias, expected):
    assert leaps.preferred_key(state, bias) == expected


def test_a_neutral_name_gets_no_pick_and_the_summary_says_why():
    b = block(bias="neutral")
    assert b["preferred"] is None
    assert b["candidates"], "the candidates are still listed for reference"
    assert "no directional read" in b["summary"]


def test_a_directional_name_names_its_pick():
    b = block(iv=58, hv=28, iv_rank=88, bias="bullish", bias_strength="strong")
    assert b["premium_state"] == "rich"
    assert b["preferred"] == "leaps_bull_put"
    assert "LEAPS Bull Put Spread" in b["summary"]


@pytest.mark.parametrize("kw", [
    {"step": 200.0},                       # a grid too coarse for most structures
    {"liquidity": "poor", "long_liquidity": "poor"},
    {"spot": 12.0, "step": 1.0},           # a low-priced name
    {"iv": 15.0, "hv": 14.0, "iv_rank": 10.0},
])
@pytest.mark.parametrize("bias", ["bullish", "bearish"])
def test_a_pick_is_never_named_unless_it_was_actually_built(kw, bias):
    """`preferred` is a pointer into `candidates`. A dangling one would render
    as a missing row rather than an error, so it must never survive."""
    b = block(bias=bias, bias_strength="strong", **kw)
    if b is None:
        return
    keys = [c["key"] for c in b["candidates"]]
    if b["preferred"] is not None:
        assert b["preferred"] in keys
    else:
        assert leaps.preferred_key(b["premium_state"], bias) not in keys


# ------------------------------------------------------------------ warnings

def test_every_block_warns_about_the_earnings_inside_the_expiry():
    assert any("earnings report" in w and "fall inside this" in w
               for w in block()["warnings"])


def test_a_thin_long_chain_is_called_out_with_its_own_spread():
    b = block(liquidity="good", long_liquidity="poor")
    assert b["liquidity"] == "poor"
    assert any("long-dated chain is thin" in w for w in b["warnings"])
    # The front month being liquid must not mask the long chain being thin.
    assert not any("long-dated chain is thin" in w
                   for w in block(liquidity="good", long_liquidity="good")["warnings"])


def test_the_front_month_iv_rank_caveat_ships_with_every_block():
    assert any("front-month reading" in w for w in block(iv_rank=60)["warnings"])


def test_short_legs_carry_an_early_assignment_warning():
    assert any("assigned early" in w for w in block()["warnings"])


# --------------------------------------------------------------- the payload

def test_the_block_is_json_safe_and_self_describing():
    payload = json.loads(json.dumps(block(bias="bullish")))
    assert payload["summary"] and payload["warnings"]
    for c in payload["candidates"]:
        assert c["name"] and c["playbook"], "every candidate explains itself"
        assert c["risk_form"]["tier"] and c["risk_form"]["note"]
        assert c["manage"]["profit_target"] and c["manage"]["time_stop"]
        assert c["expiry"] and c["dte"]
        for leg in c["legs"]:
            assert leg["expiry"] and leg["label"]


def test_every_candidate_key_has_playbook_copy():
    for key in leaps.CANDIDATE_ORDER:
        assert leaps.PLAYBOOK.get(key), f"{key} has no explanation to ship"


def test_long_spreads_all_skips_names_with_no_view_or_no_chain():
    rows = [make_row("AAA"), make_row("BBB"), make_row("CCC")]
    views = {"AAA": make_view("AAA"), "BBB": make_view("BBB", long_dte=None)}
    out = leaps.long_spreads_all(rows, views, 2500.0,
                                 biases={"AAA": ("bullish", "strong")})
    assert list(out) == ["AAA"]
    assert out["AAA"]["bias"] == "bullish"
    assert out["AAA"]["preferred"] is not None


def test_no_candidate_prices_at_nan():
    for c in block()["candidates"]:
        for field in ("net", "max_profit", "max_loss", "pop"):
            v = c[field]
            assert v is None or math.isfinite(v), f"{c['key']}.{field} is not finite"


# ------------------------------------------------- structurally impossible quotes

def _repriced(marks):
    """A view whose long-dated chain carries deliberately broken marks.
    `marks` maps (right, strike) -> mid."""
    v = make_view(spot=200.0)
    side = v.chain[v.long_expiry]
    for (right, strike), mid in marks.items():
        for k in side[right]:
            if abs(k - strike) < 0.01:
                side[right][k].mid = mid
    return v


def _keys(view, bias="bullish"):
    b = leaps.long_spreads(make_row(), view, 2500.0, bias, "strong")
    return [] if b is None else [c["key"] for c in b["candidates"]]


def test_a_credit_spread_quoted_as_a_debit_is_dropped_not_published():
    """Crossed or stale marks can invert a spread's sign. Publishing it anyway
    left a row with no max profit, no max loss and no breakeven — which the table
    then rendered as an *uncapped* win on a structure that is capped and can
    lose. Dropping it is the only honest option."""
    assert "leaps_bull_put" in _keys(make_view(spot=200.0))
    broken = _repriced({("put", 160.0): 1.00, ("put", 145.0): 9.00})
    assert "leaps_bull_put" not in _keys(broken)


def test_a_debit_vertical_costing_more_than_its_width_is_dropped():
    """Paying more than the spread can ever pay out is not a trade, it is a bad
    quote: max profit would be negative."""
    assert "leaps_bull_call" in _keys(make_view(spot=200.0))
    broken = _repriced({("call", 200.0): 60.00, ("call", 230.0): 1.00})
    assert "leaps_bull_call" not in _keys(broken)


def test_a_diagonal_that_cannot_profit_when_assigned_is_dropped():
    expensive = _repriced({("call", 160.0): 90.00})
    assert "poor_mans_covered_call" not in _keys(expensive)


def test_no_published_candidate_is_ever_missing_its_risk_numbers():
    """The invariant behind the guards: every row in the table can be read."""
    for kw in ({}, {"spot": 31.5, "step": 2.5}, {"iv": 15.0, "hv": 14.0},
               {"spot": 1180.0, "step": 20.0}, {"long_dte": 500}):
        b = leaps.long_spreads(make_row(), make_view(**kw), 2500.0, "bullish", "strong")
        for c in (b or {}).get("candidates", []):
            if c["net"] is None:
                continue                      # a leg with no two-sided market
            assert c["max_loss"] is not None, f"{c['key']} published without a max loss"
            assert c["max_profit"] is not None and c["max_profit"] > 0, \
                f"{c['key']} published without a positive max profit"
            assert c["breakevens"], f"{c['key']} published without a breakeven"


# ------------------------------------------------------------- warning accuracy

@pytest.mark.parametrize("dte,expected", [(280, 3), (365, 4), (409, 4), (500, 5)])
def test_the_earnings_count_follows_the_actual_expiry(dte, expected):
    """These expiries run from ~9 to ~18 months, so a hardcoded "about 4" was
    wrong at both ends."""
    b = block(long_dte=dte)
    warning = next(w for w in b["warnings"] if "earnings report" in w)
    assert f"About {expected} earnings report" in warning
    assert f"{dte}-day expiry" in warning


def test_a_thin_credit_is_called_out_the_way_the_near_term_engine_does():
    from spread_scanner import strategy
    b = block(spot=31.5, iv=18.0, step=2.5, bias="bullish")
    thin = [c for c in b["candidates"]
            if c["credit_to_width"] is not None
            and c["credit_to_width"] < leaps.MIN_CREDIT_TO_WIDTH]
    assert thin, "fixture must actually produce a thin credit for this to test anything"
    warning = next(w for w in b["warnings"] if "of its width" in w)
    assert "holds the capital for" in warning, "the year-long commitment is the point"
    assert leaps.MIN_CREDIT_TO_WIDTH == strategy.MIN_CREDIT_TO_WIDTH


def test_a_healthy_credit_is_not_flagged():
    assert not any("of its width" in w for w in block(spot=200.0, bias="bullish")["warnings"])


# ------------------------------------------------- chains the feed cannot price

def _unquoted(long_iv=0.2, **kw):
    """A view carrying the signature of a chain read outside market hours: a
    floor implied vol, and zero bid / zero ask / zero open interest on every
    contract. Taken from a real run — see the docstrings below."""
    v = make_view(**kw)
    v.long_iv = long_iv
    v.long_spread_pct, v.long_open_interest, v.long_liquidity = 0.0, 0, "poor"
    for side in v.chain[v.long_expiry].values():
        for q in side.values():
            q.bid, q.ask, q.iv, q.open_interest = 0.0, 0.0, 0.0, 0
    return v


def test_a_chain_with_no_volatility_read_publishes_nothing():
    """A live run outside US market hours returned ATM implied vols of 0.01% to
    0.8% on the long chain, with zero bid, ask and open interest throughout.
    The engine priced two spreads off it anyway: concrete debits and max profits
    taken from stale last-trades, and a probability of profit computed against a
    distribution 0.2% wide. None of it was transactable."""
    assert leaps.long_spreads(make_row(), _unquoted(), 2500.0, "bearish", "weak") is None


@pytest.mark.parametrize("iv", [None, 0.01, 0.1, 0.2, 0.78, 4.9])
def test_every_implausible_volatility_is_refused(iv):
    v = make_view()
    v.long_iv = iv
    assert leaps.long_spreads(make_row(), v, 2500.0, "bullish", "strong") is None


def test_a_real_volatility_read_is_still_accepted():
    """The guard must not swallow ordinary low-volatility names."""
    for iv in (leaps.MIN_USABLE_IV, 12.0, 28.0, 60.0):
        v = make_view()
        v.long_iv = iv
        b = leaps.long_spreads(make_row(), v, 2500.0, "bullish", "strong")
        assert b is not None and b["candidates"], f"refused a usable {iv}% chain"


def test_legs_without_a_two_sided_market_are_not_published():
    """Zero bid and zero ask means the mid fell back to the last traded price,
    which can be days old. A spread priced off two of those has a debit, a max
    profit and a breakeven that all look real and none of which you can fill."""
    v = _unquoted(long_iv=30.0)          # a believable IV, but no quotes
    b = leaps.long_spreads(make_row(), v, 2500.0, "bullish", "strong")
    assert b is not None, "the volatility read is fine here; only the quotes are missing"
    assert b["candidates"] == []
    # The diagonal's short leg lives in the front month, which is still quoted,
    # but its long leg is not — so it must go too.
    assert "poor_mans_covered_call" not in [c["key"] for c in b["candidates"]]


def test_one_unquoted_leg_is_enough_to_drop_a_candidate():
    v = make_view(spot=200.0)
    side = v.chain[v.long_expiry]["call"]
    for k in side:
        if abs(k - 230.0) < 0.01:        # the bull call's short leg only
            side[k].bid = side[k].ask = 0.0
    keys = [c["key"] for c in
            leaps.long_spreads(make_row(), v, 2500.0, "bullish", "strong")["candidates"]]
    assert "leaps_bull_call" not in keys
    assert "leaps_bull_put" in keys, "the put-side structures are untouched"
