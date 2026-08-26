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
                oi: int = 1500) -> dict:
    """{"call": {strike: Quote}, "put": {strike: Quote}} around `spot`."""
    sigma = iv / 100 * math.sqrt(max(dte, 1) / 365)
    base = round(spot / step) * step
    side: dict[str, dict[float, Quote]] = {"call": {}, "put": {}}
    for i in range(-14, 15):
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
              skew: float = 4.0, step: float = 5.0) -> OptionView:
    """A fully-populated OptionView with a priced two-expiry chain."""
    expiry = (dt.date.today() + dt.timedelta(days=dte)).isoformat()
    back = (dt.date.today() + dt.timedelta(days=dte + 33)).isoformat()
    ratio = round(iv / hv, 2)
    score = options.premium_score(iv_rank, ratio, term_slope)
    implied = iv * math.sqrt(horizon / 252)
    hist = hv * math.sqrt(horizon / 252)
    spread_pct = {"good": 2.5, "fair": 11.0, "poor": 24.0}[liquidity]
    oi = {"good": 2400, "fair": 260, "poor": 40}[liquidity]
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
        expiries=[{"date": expiry, "dte": dte}, {"date": back, "dte": dte + 33}],
        chain={expiry: build_chain(spot, iv, dte, step, skew, spread_pct / 200, oi),
               back: build_chain(spot, iv * (1 + term_slope), dte + 33, step, skew,
                                 spread_pct / 200, oi)},
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
