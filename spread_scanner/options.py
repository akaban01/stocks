"""Options layer — is the expected move cheap or rich vs. history?

For a both-ways (straddle) trader the edge isn't just "a move is coming" — it's
whether the *market* is pricing that move cheaper or richer than realized
volatility suggests. We read the at-the-money implied volatility from the option
chain, rescale it to the scanner's horizon, and compare it to the historical
(HV-based) expected move:

    implied move %  =  ATM IV  ×  √(horizon_days / 252)        (same units as em_pct)
    verdict         =  CHEAP   if implied < hist × (1 − margin)   (buy volatility)
                       RICH    if implied > hist × (1 + margin)   (sell volatility)
                       fair    otherwise

Network cost is one or two calls per ticker, so callers should run this only on
the most coiled names (the scanner passes the top N). Fails soft: any ticker
without a usable chain just gets None.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass

import yfinance as yf


@dataclass
class OptionView:
    ticker: str
    implied_move_pct: float   # implied move over the horizon, % of spot
    iv_annual: float          # ATM implied volatility, annualized (%)
    verdict: str              # "cheap" / "fair" / "rich"
    expiry: str               # expiry whose IV we sampled
    days_to_expiry: int


def _nearest_expiry(expiries: list[str], target_days: int) -> tuple[str, int] | None:
    """Pick the expiry closest to `target_days` calendar days out (future only)."""
    today = dt.date.today()
    dated = []
    for e in expiries:
        try:
            d = (dt.date.fromisoformat(e) - today).days
        except ValueError:
            continue
        if d >= 1:
            dated.append((e, d))
    if not dated:
        return None
    return min(dated, key=lambda ed: abs(ed[1] - target_days))


def _atm_iv(chain, spot: float) -> float | None:
    """Average call/put implied vol at the strike nearest spot. None if unusable."""
    ivs = []
    for leg in (chain.calls, chain.puts):
        if leg is None or leg.empty or "impliedVolatility" not in leg:
            continue
        row = leg.iloc[(leg["strike"] - spot).abs().argmin()]
        iv = float(row.get("impliedVolatility", float("nan")))
        if math.isfinite(iv) and iv > 0:
            ivs.append(iv)
    return sum(ivs) / len(ivs) if ivs else None


def implied_view(
    ticker: str,
    spot: float,
    hist_move_pct: float,
    horizon_days: int,
    margin: float = 0.15,
) -> OptionView | None:
    """Build an OptionView comparing implied vs. historical expected move."""
    try:
        tk = yf.Ticker(ticker)
        expiries = list(tk.options or [])
        pick = _nearest_expiry(expiries, target_days=round(horizon_days * 1.4))
        if not pick:
            return None
        expiry, dte = pick
        iv = _atm_iv(tk.option_chain(expiry), spot)
    except Exception:
        return None
    if iv is None:
        return None

    implied_move_pct = iv * math.sqrt(horizon_days / 252) * 100
    verdict = classify_verdict(implied_move_pct, hist_move_pct, margin)

    return OptionView(ticker, round(implied_move_pct, 2), round(iv * 100, 1),
                      verdict, expiry, dte)


def classify_verdict(implied_pct: float, hist_pct: float, margin: float) -> str:
    """cheap if implied < hist·(1−margin), rich if implied > hist·(1+margin)."""
    if hist_pct and implied_pct < hist_pct * (1 - margin):
        return "cheap"
    if hist_pct and implied_pct > hist_pct * (1 + margin):
        return "rich"
    return "fair"


def screen_options(
    rows: list[tuple[str, float, float]],
    horizon_days: int,
    margin: float = 0.15,
) -> dict[str, OptionView]:
    """`rows` = [(ticker, spot, hist_move_pct)]. Returns {ticker: OptionView}."""
    out: dict[str, OptionView] = {}
    for ticker, spot, hist in rows:
        view = implied_view(ticker, spot, hist, horizon_days, margin)
        if view is not None:
            out[ticker] = view
    return out
