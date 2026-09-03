"""Shared fixtures: synthetic option chains so the strategy tests never touch
the network. Contracts are Black-Scholes priced, so credits, breakevens and
probabilities behave the way real ones do."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from spread_scanner import options
from spread_scanner.options import OptionView, Quote


def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(spot: float, strike: float, iv_pct: float, dte: int, right: str) -> float:
    """Black-Scholes with zero rates — enough for a fixture."""
    t = max(dte, 1) / 365
    sigma = iv_pct / 100 * math.sqrt(t)
    if sigma <= 0:
        return max(0.0, spot - strike if right == "call" else strike - spot)
    d1 = (math.log(spot / strike) + 0.5 * sigma * sigma) / sigma
    d2 = d1 - sigma
    if right == "call":
        return spot * _norm_cdf(d1) - strike * _norm_cdf(d2)
    return strike * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def build_chain(spot: float, iv: float, dte: int, step: float = 5.0,
                skew_pts: float = 4.0, spread_frac: float = 0.03,
                oi: int = 1500, span: int = 14) -> dict:
    """{"call": {strike: Quote}, "put": {strike: Quote}} around `spot`.

    `span` is how many strike increments to build either side — a long-dated
    chain needs a wider ladder than the front month."""
    sigma = iv / 100 * math.sqrt(max(dte, 1) / 365)
    base = round(spot / step) * step
    side: dict[str, dict[float, Quote]] = {"call": {}, "put": {}}
    for i in range(-span, span + 1):
        strike = round(base + i * step, 2)
        if strike <= 0:
            continue
        for right in ("call", "put"):
            # Downside puts carry the skew; upside calls trade slightly under ATM.
            tilt = skew_pts * max(0.0, (spot - strike) / spot) / max(sigma, 1e-6) * 0.35
            leg_iv = max(5.0, iv + (tilt if right == "put" else -tilt * 0.4))
            mid = round(max(0.02, bs_price(spot, strike, leg_iv, dte, right)), 2)
            side[right][strike] = Quote(
                strike=strike, right=right,
                bid=round(mid * (1 - spread_frac), 2), ask=round(mid * (1 + spread_frac), 2),
                mid=mid, last=mid, iv=round(leg_iv, 1),
                open_interest=oi, volume=oi // 4,
            )
    return side


def make_view(ticker: str = "TEST", spot: float = 200.0, iv: float = 45.0,
              hv: float = 30.0, iv_rank: float = 60.0, dte: int = 24,
              horizon: int = 10, liquidity: str = "good", term_slope: float = 0.03,
              skew: float = 4.0, step: float = 5.0, long_dte: int | None = 409,
              long_liquidity: str | None = None) -> OptionView:
    """A fully-populated OptionView with a priced chain.

    Three expiries: the front one the near-term engine trades, a ~2-month back
    month for the term-structure read, and (unless ``long_dte=None``) a
    ~13-month LEAPS expiry for the long-dated engine. The long chain is built on
    a wider strike ladder, because a year of sigma reaches far past the front
    month's strikes."""
    expiry = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    back = (dt.date.today() + dt.timedelta(days=dte + 33)).isoformat()
    ratio = round(iv / hv, 2)
    score = options.premium_score(iv_rank, ratio, term_slope)
    implied = iv * math.sqrt(horizon / 252)
    hist = hv * math.sqrt(horizon / 252)
    spread_pct = {"good": 2.5, "fair": 11.0, "poor": 24.0}[liquidity]
    oi = {"good": 2400, "fair": 260, "poor": 40}[liquidity]

    chain = {expiry: build_chain(spot, iv, dte, step, skew, spread_pct / 200, oi),
             back: build_chain(spot, iv * (1 + term_slope), dte + 33, step, skew,
                               spread_pct / 200, oi)}
    expiries = [{"date": expiry, "dte": dte}, {"date": back, "dte": dte + 33}]

    long_expiry = long_iv = long_spread = long_oi = None
    if long_dte:
        long_expiry = (dt.date.today() + dt.timedelta(days=long_dte)).isoformat()
        # LEAPS quote flatter in vol and wider in price than the front month.
        long_iv = round(iv * 0.9, 2)
        lliq = long_liquidity or liquidity
        long_spread = {"good": 5.0, "fair": 13.0, "poor": 26.0}[lliq]
        long_oi = {"good": 900, "fair": 180, "poor": 25}[lliq]
        chain[long_expiry] = build_chain(spot, long_iv, long_dte, step, skew,
                                         long_spread / 200, long_oi, span=30)
        expiries.append({"date": long_expiry, "dte": long_dte})

    return OptionView(
        ticker=ticker, spot=spot, iv_annual=iv, hv_annual=hv,
        implied_move_pct=round(implied, 2), hist_move_pct=round(hist, 2),
        iv_rank=iv_rank, iv_percentile=min(99.0, iv_rank + 6),
        vrp=round(iv - hv, 2), iv_hv_ratio=ratio,
        verdict=options.classify_verdict(implied, hist, 0.15),
        premium_score=score, premium_state=options.premium_state(score),
        term_slope=term_slope, term_structure=options.classify_term(term_slope),
        skew=skew, skew_label=options.classify_skew(skew),
        atm_spread_pct=spread_pct, atm_open_interest=oi,
        liquidity=options.classify_liquidity(spread_pct, oi),
        expiry=expiry, days_to_expiry=dte,
        expiries=expiries,
        long_expiry=long_expiry, long_dte=long_dte, long_iv=long_iv,
        long_spread_pct=long_spread, long_open_interest=long_oi,
        long_liquidity=options.classify_liquidity(long_spread, long_oi)
        if long_expiry else "unknown",
        chain=chain,
    )


def make_row(ticker: str = "TEST", **overrides) -> dict:
    """A scanner row shaped like scanner.Signal.as_row()."""
    row = dict(ticker=ticker, rank=1, price=200.0, score=70.0, squeeze_on=True,
               squeeze_days=9, squeeze_fired=False, fired_dir="", bandwidth_pctile=0.12,
               hv_annual=30.0, hv_pctile=0.25, em_pct=6.0, horizon_days=10,
               down_1sigma=188.0, up_1sigma=212.0, down_2sigma=176.0, up_2sigma=224.0,
               lean="Neutral", note="", earnings_in_days=45.0)
    row.update(overrides)
    return row


@pytest.fixture
def view():
    return make_view


@pytest.fixture
def row():
    return make_row
