import numpy as np
import pandas as pd

from spread_scanner import indicators as ind


def _df(closes, highs=None, lows=None):
    c = pd.Series(list(closes), dtype=float)
    h = c if highs is None else pd.Series(list(highs), dtype=float)
    lo = c if lows is None else pd.Series(list(lows), dtype=float)
    return pd.DataFrame({"Open": c, "High": h, "Low": lo, "Close": c, "Volume": 1.0})


def test_atr_is_positive():
    df = _df(range(1, 60), highs=[x + 1 for x in range(1, 60)], lows=[x - 1 for x in range(1, 60)])
    a = ind.atr(df, 14).dropna()
    assert len(a) > 0 and (a > 0).all()


def test_bollinger_bands_ordered_and_bandwidth_nonneg():
    close = pd.Series(np.random.RandomState(0).normal(100, 1, 200))
    _, up, lo, bw = ind.bollinger_bands(close, 20, 2.0)
    assert (up.dropna() >= lo.dropna()).all()
    assert (bw.dropna() >= 0).all()


def test_rolling_percentile_handles_leading_nan():
    # Regression: 19 warmup NaNs + 105 bars and a 120 lookback must NOT be all-NaN.
    vals = list(np.random.RandomState(1).normal(0, 1, 105))
    s = pd.Series([np.nan] * 19 + vals)
    rp = ind.rolling_percentile(s, 120)
    assert rp.notna().any()
    assert 0.0 <= rp.dropna().iloc[-1] <= 1.0


def test_rolling_percentile_ranks_extremes():
    s = pd.Series(list(range(100)))           # strictly increasing
    rp = ind.rolling_percentile(s, 50)
    assert rp.iloc[-1] == 1.0                 # last value is the max in its window


def test_trailing_true_count():
    assert ind.trailing_true_count(pd.Series([True, True, False, True, True])) == 2
    assert ind.trailing_true_count(pd.Series([False, False])) == 0
    assert ind.trailing_true_count(pd.Series([True, True, True])) == 3


def test_ttm_squeeze_is_boolean():
    close = pd.Series(np.linspace(100, 101, 120))
    df = _df(close, highs=close + 0.3, lows=close - 0.3)
    sq = ind.ttm_squeeze(df)
    assert sq.dtype == bool
