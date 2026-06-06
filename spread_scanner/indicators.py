"""Technical indicators used by the spread scanner.

Everything in here operates on a single ticker's OHLCV DataFrame
(columns: Open, High, Low, Close, Volume) and returns pandas Series
aligned to the input index. No look-ahead: every value at row *i* is
computed only from data up to and including row *i*.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(df: pd.DataFrame) -> pd.Series:
    """Wilder's True Range."""
    high, low, prev_close = df["High"], df["Low"], df["Close"].shift(1)
    return pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)


def atr(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing (EMA, alpha = 1/length)."""
    return true_range(df).ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def bollinger_bands(close: pd.Series, length: int = 20, mult: float = 2.0):
    """Return (mid, upper, lower, bandwidth). bandwidth = (upper-lower)/mid."""
    mid = close.rolling(length).mean()
    std = close.rolling(length).std(ddof=0)
    upper = mid + mult * std
    lower = mid - mult * std
    bandwidth = (upper - lower) / mid
    return mid, upper, lower, bandwidth


def keltner_channels(df: pd.DataFrame, length: int = 20, mult: float = 1.5):
    """Return (mid, upper, lower) using an EMA mid-line and ATR envelope."""
    mid = df["Close"].ewm(span=length, adjust=False, min_periods=length).mean()
    rng = atr(df, length)
    return mid, mid + mult * rng, mid - mult * rng


def ttm_squeeze(
    df: pd.DataFrame,
    bb_length: int = 20,
    bb_mult: float = 2.0,
    kc_length: int = 20,
    kc_mult: float = 1.5,
) -> pd.Series:
    """Boolean Series: True when Bollinger Bands sit *inside* the Keltner
    Channels (the 'squeeze is on' — volatility is coiled, a move is loading)."""
    _, bb_up, bb_lo, _ = bollinger_bands(df["Close"], bb_length, bb_mult)
    _, kc_up, kc_lo = keltner_channels(df, kc_length, kc_mult)
    return (bb_up < kc_up) & (bb_lo > kc_lo)


def _linreg_last(y: np.ndarray) -> float:
    """Value of an OLS line fit to y at its final point (LazyBear style)."""
    n = len(y)
    x = np.arange(n)
    x_mean, y_mean = x.mean(), y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom == 0:
        return float(y_mean)
    slope = ((x - x_mean) * (y - y_mean)).sum() / denom
    intercept = y_mean - slope * x_mean
    return float(intercept + slope * (n - 1))


def squeeze_momentum(df: pd.DataFrame, length: int = 20) -> pd.Series:
    """Squeeze-momentum oscillator. Sign hints direction once the squeeze
    fires; magnitude shows how hard it is being pushed."""
    highest = df["High"].rolling(length).max()
    lowest = df["Low"].rolling(length).min()
    sma = df["Close"].rolling(length).mean()
    baseline = ((highest + lowest) / 2 + sma) / 2
    delta = df["Close"] - baseline
    return delta.rolling(length).apply(_linreg_last, raw=True)


def historical_volatility(close: pd.Series, length: int = 20, annualize: bool = True) -> pd.Series:
    """Rolling stdev of daily log returns (annualized by default)."""
    logret = np.log(close / close.shift(1))
    vol = logret.rolling(length).std(ddof=0)
    return vol * np.sqrt(252) if annualize else vol


def rolling_percentile(series: pd.Series, lookback: int, min_periods: int = 20) -> pd.Series:
    """For each row, the percentile rank (0..1) of the current value within
    the trailing `lookback` window — fraction of window values <= current.

    Leading NaNs (indicator warmup) are dropped first, and the window is
    clamped to the available history so short series still produce a value.
    """
    s = series.dropna()
    if s.empty:
        return s
    window = min(lookback, len(s))
    mp = min(min_periods, window)
    return s.rolling(window, min_periods=mp).apply(lambda w: (w <= w[-1]).mean(), raw=True)


def trailing_true_count(flags: pd.Series) -> int:
    """How many consecutive True values sit at the tail of a boolean Series."""
    count = 0
    for v in reversed(flags.fillna(False).tolist()):
        if v:
            count += 1
        else:
            break
    return count
