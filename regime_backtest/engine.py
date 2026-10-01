"""Timing, signals and the switch-between-SPY-and-cash engine.

The convention everywhere: a **signal** at date t is the position the rule wants
using only what was known at the close of t. The engine holds that position
during day t+1 — ``position = signal.shift(1)`` — and nowhere else is a shift
applied to returns. FRED series get one *extra* trading day of lag on top
(``align_fred``), because a value FRED dates d is published the following day.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252
MONTHLY_SMA = 10
VELOCITY_WINDOW = 22                 # trading days in the credit-spread change
Z_WINDOW = 3 * TRADING_DAYS          # rolling window for the BAA10Y z-score


# --- alignment -----------------------------------------------------------------

def align_fred(s: pd.Series, index: pd.DatetimeIndex, lag: int = 1) -> pd.Series:
    """Put a FRED series on the trading-day index, then lag it.

    Forward-fill only uses observations dated on or before each trading day,
    and the ``shift(lag)`` makes the value FRED dates d first usable at the
    close of the next trading day."""
    s = s.sort_index()
    on_days = s.reindex(s.index.union(index)).ffill().reindex(index)
    return on_days.shift(lag)


def cash_returns(tbill_pct: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    """Daily return of holding 3-month T-bills.

    DTB3 is an annualized discount-basis rate in percent. Day t's return accrues
    the rate known at the start of the day (the previous published value, i.e.
    lagged like every FRED series) over the calendar days since the last close,
    on the 360-day basis T-bills are quoted on — so a Monday earns the weekend."""
    rate = align_fred(tbill_pct, index, lag=1)
    days = pd.Series(index, index=index).diff().dt.days
    out = rate / 100.0 * days / 360.0
    return out.fillna(0.0)


# --- signals ---------------------------------------------------------------------

def _as_signal(cond: pd.Series, valid: pd.Series) -> pd.Series:
    return cond.astype(float).where(valid)


def sma_signal(close: pd.Series, window: int = 200) -> pd.Series:
    sma = close.rolling(window, min_periods=window).mean()
    return _as_signal(close > sma, sma.notna())


def cross_signal(close: pd.Series, fast: int = 50, slow: int = 200) -> pd.Series:
    f = close.rolling(fast, min_periods=fast).mean()
    s = close.rolling(slow, min_periods=slow).mean()
    return _as_signal(f > s, f.notna() & s.notna())


def month_end_closes(close: pd.Series) -> pd.Series:
    """The close on the last trading day of each month, indexed by that day.

    A true monthly series — one row per month — so ``rolling(10)`` on it means
    ten months. The final month is dropped when the data stops before its last
    business day: a mid-month close is not a month-end close."""
    me = close.groupby(close.index.to_period("M")).tail(1)
    last = me.index[-1]
    if last == close.index[-1] and last < last + pd.offsets.BMonthEnd(0):
        me = me.iloc[:-1]
    return me


def monthly_sma_signal(close: pd.Series, months: int = MONTHLY_SMA) -> pd.Series:
    """In when the month-end close is above the SMA of the last ``months``
    month-end closes (including this one). Computed monthly, then the monthly
    signal is forward-filled onto the daily index, so it is decided on the last
    trading day's close and held for all of the next month."""
    me = month_end_closes(close)
    sma = me.rolling(months, min_periods=months).mean()
    sig_m = _as_signal(me > sma, sma.notna())
    return sig_m.reindex(close.index).ffill()


def hysteresis(metric: pd.Series, exit_above: float, reenter_below: float) -> pd.Series:
    """1 (in) / 0 (out) / NaN while the metric is undefined.

    Starts in the market at the first defined value. Leaves when the metric
    rises above ``exit_above``; once out, returns only when it falls below
    ``reenter_below``. A NaN mid-series keeps the previous state."""
    vals = metric.to_numpy(dtype=float)
    out = np.full(len(vals), np.nan)
    state = np.nan
    for i, v in enumerate(vals):
        if np.isnan(v):
            out[i] = state
            continue
        if np.isnan(state):
            state = 1.0
        if state == 1.0 and v > exit_above:
            state = 0.0
        elif state == 0.0 and v < reenter_below:
            state = 1.0
        out[i] = state
    return pd.Series(out, index=metric.index)


def credit_velocity_bp(spread_pct_aligned: pd.Series) -> pd.Series:
    """22-trading-day change in the spread, in basis points."""
    return (spread_pct_aligned * 100.0).diff(VELOCITY_WINDOW)


def credit_velocity_z(spread_pct_aligned: pd.Series, window: int = Z_WINDOW) -> pd.Series:
    """z-score of the 22-day change against its own trailing ``window`` days
    (the window ends at t, so nothing after t enters)."""
    chg = spread_pct_aligned.diff(VELOCITY_WINDOW)
    mu = chg.rolling(window, min_periods=window).mean()
    sd = chg.rolling(window, min_periods=window).std()
    return (chg - mu) / sd


def combine_all_in(*signals: pd.Series) -> pd.Series:
    """In only when every signal says in; undefined while any is undefined."""
    df = pd.concat(signals, axis=1)
    return df.min(axis=1, skipna=False)


# --- engine ----------------------------------------------------------------------

def run(signal: pd.Series, asset_ret: pd.Series, cash_ret: pd.Series,
        cost_bp: float) -> pd.DataFrame:
    """Simulate a 100%-asset / 100%-cash switch.

    ``position[t] = signal[t-1]``. Each change of position costs ``cost_bp``
    once (one side), and so does the initial purchase when the first held
    position is "in" — buy-and-hold pays it too. Rows before the first defined
    position are dropped, so each rule's history starts where its signal does."""
    pos = signal.reindex(asset_ret.index).shift(1)
    first = pos.first_valid_index()
    if first is None:
        return pd.DataFrame(columns=["ret", "pos", "asset", "cash", "turnover"])
    pos = pos.loc[first:].ffill()
    a = asset_ret.loc[first:]
    c = cash_ret.loc[first:]
    gross = pos * a + (1.0 - pos) * c
    turnover = pos.diff().abs()
    turnover.iloc[0] = abs(pos.iloc[0])
    net = (1.0 + gross) * (1.0 - cost_bp / 1e4 * turnover) - 1.0
    return pd.DataFrame({"ret": net, "pos": pos, "asset": a, "cash": c, "turnover": turnover})
