import datetime as dt
import math

import pytest

from spread_scanner import options
from conftest import build_chain, make_view


# ------------------------------------------------------- cheap / fair / rich

def test_classify_verdict():
    assert options.classify_verdict(5.0, 10.0, 0.15) == "cheap"   # implied << hist
    assert options.classify_verdict(15.0, 10.0, 0.15) == "rich"   # implied >> hist
    assert options.classify_verdict(10.0, 10.0, 0.15) == "fair"
    assert options.classify_verdict(5.0, 0.0, 0.15) == "fair"     # no history -> fair


# ---------------------------------------------------------------- expiries

def test_nearest_expiry_picks_closest_future():
    today = dt.date.today()
    exps = [(today + dt.timedelta(days=d)).isoformat() for d in [1, 7, 14, 30]]
    expiry, days = options._nearest_expiry(exps, target_days=14)
    assert days == 14


def test_nearest_expiry_ignores_past():
    today = dt.date.today()
    exps = [(today - dt.timedelta(days=5)).isoformat(),
            (today + dt.timedelta(days=20)).isoformat()]
    expiry, days = options._nearest_expiry(exps, target_days=14)
    assert days == 20


def test_nearest_expiry_none_when_all_past():
    past = [(dt.date.today() - dt.timedelta(days=3)).isoformat()]
    assert options._nearest_expiry(past, 14) is None


def test_nearest_expiry_skips_unparseable_dates():
    good = (dt.date.today() + dt.timedelta(days=9)).isoformat()
    assert options._nearest_expiry(["not-a-date", good], 10) == (good, 9)


# ------------------------------------------------------- IV rank / percentile

def test_iv_rank_places_iv_inside_the_realized_range():
    hist = [float(v) for v in range(20, 61)]        # 20% .. 60% realized vol
    assert options.iv_rank(20.0, hist) == 0.0
    assert options.iv_rank(60.0, hist) == 100.0
    assert options.iv_rank(40.0, hist) == pytest.approx(50.0, abs=0.5)


def test_iv_rank_clamps_outside_the_range():
    hist = [float(v) for v in range(20, 61)]
    assert options.iv_rank(5.0, hist) == 0.0        # below every reading
    assert options.iv_rank(200.0, hist) == 100.0    # above every reading


def test_iv_rank_needs_enough_history():
    assert options.iv_rank(30.0, [30.0] * 5) is None
    assert options.iv_rank(30.0, None) is None
    assert options.iv_rank(30.0, [30.0] * 40) is None   # flat range -> undefined


def test_iv_percentile_counts_readings_below():
    hist = [float(v) for v in range(0, 100)]
    assert options.iv_percentile(50.0, hist) == pytest.approx(51.0, abs=0.5)
    assert options.iv_percentile(0.0, hist) == pytest.approx(1.0, abs=0.5)


def test_iv_rank_and_percentile_ignore_nans():
    hist = [float("nan")] * 5 + [float(v) for v in range(20, 61)]
    assert options.iv_rank(40.0, hist) == pytest.approx(50.0, abs=0.5)
    assert 0 < options.iv_percentile(40.0, hist) < 100


# --------------------------------------------------------- the premium score

def test_premium_score_rises_with_iv_rank_and_with_the_risk_premium():
    low = options.premium_score(10, 0.85, 0.03)
    mid = options.premium_score(50, 1.15, 0.03)
    high = options.premium_score(90, 1.60, 0.03)
    assert low < mid < high
    assert options.premium_state(low) == "cheap"
    assert options.premium_state(mid) == "fair"
    assert options.premium_state(high) == "rich"


def test_premium_score_is_bounded():
    assert 0 <= options.premium_score(0, 0.5, 0.5) <= 100
    assert 0 <= options.premium_score(100, 3.0, -0.5) <= 100


def test_premium_score_falls_back_to_neutral_without_data():
    # No rank, no ratio, no term structure — should land in the fair band, not
    # silently at one extreme.
    assert options.premium_state(options.premium_score(None, None, None)) == "fair"


def test_backwardation_makes_front_premium_look_richer():
    flat = options.premium_score(50, 1.15, 0.0)
    inverted = options.premium_score(50, 1.15, -0.10)
    steep = options.premium_score(50, 1.15, 0.15)
    assert steep < flat < inverted


# ---------------------------------------------------------- classifications

def test_classify_term():
    assert options.classify_term(-0.08) == "backwardation"
    assert options.classify_term(0.0) == "flat"
    assert options.classify_term(0.10) == "contango"
    assert options.classify_term(None) == "unknown"


def test_classify_skew():
    assert options.classify_skew(6.0) == "put_skew"
    assert options.classify_skew(0.5) == "balanced"
    assert options.classify_skew(-6.0) == "call_skew"
    assert options.classify_skew(None) == "unknown"


def test_classify_liquidity():
    assert options.classify_liquidity(2.0, 2000) == "good"
    assert options.classify_liquidity(10.0, 300) == "fair"
    assert options.classify_liquidity(30.0, 20) == "poor"
    assert options.classify_liquidity(None, None) == "unknown"
    assert options.classify_liquidity(2.0, 5) == "poor"      # tight but nobody home


# --------------------------------------------------------------- chain math

def test_quote_spread_pct():
    q = options.Quote(strike=100, right="call", bid=1.90, ask=2.10, mid=2.00,
                      last=2.0, iv=30.0, open_interest=10, volume=1)
    assert q.spread_pct == pytest.approx(10.0)
    bare = options.Quote(strike=100, right="call", bid=None, ask=None, mid=None,
                         last=None, iv=None, open_interest=None, volume=None)
    assert bare.spread_pct is None


def test_atm_iv_averages_the_two_sides_at_the_nearest_strike():
    chain = build_chain(spot=202.0, iv=40.0, dte=30, step=5.0, skew_pts=0.0)
    iv = options._atm_iv(chain["call"], chain["put"], 202.0)
    assert iv == pytest.approx(40.0, abs=1.5)


def test_skew_samples_either_side_of_spot():
    chain = build_chain(spot=200.0, iv=40.0, dte=30, skew_pts=8.0)
    sigma = 40 / 100 * math.sqrt(30 / 365)
    skew = options._skew(chain["call"], chain["put"], 200.0, sigma)
    assert skew is not None and skew > 0          # puts bid over calls


def test_view_as_dict_drops_the_raw_chain():
    payload = make_view().as_dict()
    assert "chain" not in payload
    assert payload["iv_rank"] is not None and payload["expiry"]


def test_quotes_record_whether_the_mid_is_live_or_the_last_trade():
    import pandas as pd

    from spread_scanner import options
    leg = pd.DataFrame([
        {"strike": 100.0, "bid": 2.0, "ask": 2.2, "lastPrice": 2.1, "impliedVolatility": 0.3,
         "openInterest": 10, "volume": 1},
        {"strike": 105.0, "bid": 0.0, "ask": 0.0, "lastPrice": 0.9, "impliedVolatility": 0.3,
         "openInterest": 10, "volume": 0},
        {"strike": 110.0, "bid": 0.0, "ask": 0.0, "lastPrice": 0.0, "impliedVolatility": 0.3,
         "openInterest": 0, "volume": 0},
    ])
    q = options._quotes(leg, "call")
    assert (q[100.0].mid_source, q[100.0].mid) == ("quote", 2.1)
    assert (q[105.0].mid_source, q[105.0].mid) == ("last", 0.9)
    assert (q[110.0].mid_source, q[110.0].mid) == ("none", None)


def test_solve_iv_recovers_the_volatility_a_price_was_made_with():
    from spread_scanner import options
    for right in ("call", "put"):
        for k in (80.0, 100.0, 125.0):
            price = options.bs_price(100.0, k, 30 / 365, 0.35, right)
            assert abs(options.solve_iv(price, 100.0, k, 30 / 365, right) - 0.35) < 1e-6


def test_solve_iv_refuses_a_price_no_volatility_can_produce():
    from spread_scanner import options
    # A call quoted under its intrinsic value (spot 120, strike 100) is stale.
    assert options.solve_iv(15.0, 120.0, 100.0, 30 / 365, "call") is None
    assert options.solve_iv(0.0, 100.0, 100.0, 30 / 365, "call") is None


def test_live_quotes_get_their_own_iv_and_stale_ones_keep_yahoos():
    from spread_scanner import options
    live_mid = options.bs_price(100.0, 100.0, 30 / 365, 0.30, "call")
    live = options.Quote(strike=100.0, right="call", bid=live_mid - 0.05, ask=live_mid + 0.05,
                         mid=live_mid, last=None, iv=0.001, open_interest=10, volume=1)
    stale = options.Quote(strike=105.0, right="call", bid=None, ask=None, mid=1.2, last=1.2,
                          iv=44.0, open_interest=10, volume=0, mid_source="last")
    sides = {"call": {100.0: live, 105.0: stale}, "put": {}}
    assert options.solve_chain_ivs(sides, 100.0, 30) == 1
    assert (live.iv, live.iv_source) == (30.0, "mid")          # Yahoo's 0.001% floor replaced
    assert (stale.iv, stale.iv_source) == (44.0, "yahoo")


# ------------------------------------------------------ after-hours quotes

def test_quote_session_follows_new_york_hours_across_daylight_saving():
    utc = dt.timezone.utc
    # Summer (EDT, UTC-4): 19:39 UTC is 15:39 in New York, 20:47 is after the close.
    assert options.quote_session(dt.datetime(2026, 9, 24, 19, 39, tzinfo=utc)) == "regular"
    assert options.quote_session(dt.datetime(2026, 9, 24, 20, 47, tzinfo=utc)) == "closed"
    # Winter (EST, UTC-5): 20:47 UTC is 15:47, still open; 21:23 is not.
    assert options.quote_session(dt.datetime(2026, 12, 3, 20, 47, tzinfo=utc)) == "regular"
    assert options.quote_session(dt.datetime(2026, 12, 3, 21, 23, tzinfo=utc)) == "closed"
    # Before the open, and a Saturday.
    assert options.quote_session(dt.datetime(2026, 9, 24, 13, 0, tzinfo=utc)) == "closed"
    assert options.quote_session(dt.datetime(2026, 9, 26, 16, 0, tzinfo=utc)) == "closed"


def test_after_hours_liquidity_ignores_the_bid_ask():
    # PG on the 20:47 UTC scan: a 56% bid/ask, and plenty of open interest.
    assert options.classify_liquidity(56.0, 3000) == "poor"
    assert options.classify_liquidity(56.0, 3000, "closed") == "good"
    assert options.classify_liquidity(56.0, 300, "closed") == "fair"
    assert options.classify_liquidity(1.0, 8, "closed") == "poor"
    assert options.classify_liquidity(3.0, None, "closed") == "unknown"
