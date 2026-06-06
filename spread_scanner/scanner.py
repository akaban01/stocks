"""Turn raw OHLCV into a ranked short-term breakout watchlist.

The thesis: when realized volatility compresses (Bollinger Bands choke
inside the Keltner Channels, bandwidth drops to the low end of its range)
price is storing energy. It tends to release that energy with an outsized
move over the following days — but the *direction* is not knowable in
advance. So for each name we report:

  * a Setup Score (how coiled it is right now), and
  * an expected-move band: how far it could travel UP **and** DOWN over the
    next ~2 weeks, derived from realized volatility.

That band is the "spread both ways" the scanner is built around.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from . import indicators as ind


@dataclass
class Signal:
    ticker: str
    price: float
    score: float            # 0..100, higher = more coiled / move more imminent
    squeeze_on: bool
    squeeze_days: int       # consecutive days the squeeze has been on
    bandwidth_pctile: float # 0..1, where today's BB width sits in its history
    hv_annual: float        # annualized historical volatility (%)
    hv_pctile: float        # 0..1, where today's HV sits in its history
    em_pct: float           # 1-sigma expected move over the horizon, % of price
    horizon_days: int
    down_1sigma: float      # price targets
    up_1sigma: float
    down_2sigma: float
    up_2sigma: float
    lean: str               # Bullish / Bearish / Neutral (weak directional hint)
    note: str = ""

    def as_row(self) -> dict:
        return asdict(self)


def _nz(x, default=0.5):
    """NaN-safe: return default when x is NaN/None."""
    return default if x is None or (isinstance(x, float) and math.isnan(x)) else x


def _setup_score(squeeze_on: bool, squeeze_days: int, bw_pctile, hv_pctile) -> float:
    """Blend three "coiled spring" signals into a 0..100 score.

    * compression  — low Bollinger bandwidth percentile (tight range)   max 35
    * vol room     — low historical-vol percentile (room to expand)     max 20
    * squeeze      — squeeze on, longer = more stored energy            max 45
    """
    compression = (1 - _nz(bw_pctile)) * 35
    vol_room = (1 - _nz(hv_pctile)) * 20
    squeeze = 0.0
    if squeeze_on:
        squeeze = 30 + min(squeeze_days, 15) / 15 * 15
    return round(min(compression + vol_room + squeeze, 100), 1)


def _lean(momentum: float, price: float, sma20: float) -> str:
    bullish = _nz(momentum, 0) > 0
    above = price > _nz(sma20, price)
    if bullish and above:
        return "Bullish"
    if (not bullish) and (not above):
        return "Bearish"
    return "Neutral"


def analyze(ticker: str, df: pd.DataFrame, p: dict) -> Signal | None:
    """Compute a Signal for one ticker, or None if there isn't enough data."""
    df = df.dropna(subset=["Open", "High", "Low", "Close"]).copy()
    min_bars = max(p["percentile_lookback"], p["bb_length"], p["vol_lookback"]) + 5
    if len(df) < p["bb_length"] + 5:
        return None

    close = df["Close"]
    price = float(close.iloc[-1])

    _, _, _, bandwidth = ind.bollinger_bands(close, p["bb_length"], p["bb_mult"])
    squeeze = ind.ttm_squeeze(df, p["bb_length"], p["bb_mult"], p["kc_length"], p["kc_mult"])
    momentum = ind.squeeze_momentum(df, p["kc_length"])
    hv = ind.historical_volatility(close, p["vol_lookback"])

    # rolling_percentile clamps the window to available history, so this still
    # returns a value when there's less than `percentile_lookback` of data.
    bw_pctile = ind.rolling_percentile(bandwidth, p["percentile_lookback"]).iloc[-1]
    hv_pctile = ind.rolling_percentile(hv, p["percentile_lookback"]).iloc[-1]

    squeeze_on = bool(squeeze.iloc[-1])
    squeeze_days = ind.trailing_true_count(squeeze)

    # Expected move: 1-sigma daily move scaled by sqrt(time) over the horizon.
    logret = np.log(close / close.shift(1))
    sigma_d = float(logret.tail(p["vol_lookback"]).std(ddof=0))
    horizon = p["horizon_days"]
    em = price * sigma_d * math.sqrt(horizon)
    em_pct = em / price * 100 if price else float("nan")

    sma20 = float(close.rolling(20).mean().iloc[-1]) if len(df) >= 20 else price

    note = "" if len(df) >= min_bars else "limited history"

    return Signal(
        ticker=ticker,
        price=round(price, 2),
        score=_setup_score(squeeze_on, squeeze_days, bw_pctile, hv_pctile),
        squeeze_on=squeeze_on,
        squeeze_days=squeeze_days,
        bandwidth_pctile=round(_nz(bw_pctile), 3),
        hv_annual=round(float(hv.iloc[-1]) * 100, 1),
        hv_pctile=round(_nz(hv_pctile), 3),
        em_pct=round(em_pct, 2),
        horizon_days=horizon,
        down_1sigma=round(price - em, 2),
        up_1sigma=round(price + em, 2),
        down_2sigma=round(price - 2 * em, 2),
        up_2sigma=round(price + 2 * em, 2),
        lean=_lean(float(momentum.iloc[-1]), price, sma20),
        note=note,
    )


def scan(data: dict[str, pd.DataFrame], p: dict) -> pd.DataFrame:
    """Analyze every ticker and return a DataFrame ranked by Setup Score."""
    rows = []
    for ticker, df in data.items():
        try:
            sig = analyze(ticker, df, p)
        except Exception as exc:  # one bad ticker shouldn't sink the scan
            print(f"  ! {ticker}: {exc}")
            continue
        if sig is not None:
            rows.append(sig.as_row())

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    out.insert(0, "rank", out.index + 1)
    return out
