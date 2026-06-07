import numpy as np
import pandas as pd

from spread_scanner import backtest

PARAMS = dict(horizon_days=10, bb_length=20, bb_mult=2.0, kc_length=20, kc_mult=1.5,
              atr_length=14, vol_lookback=20, percentile_lookback=120)


def test_consecutive_true_counts_and_resets():
    s = pd.Series([True, True, False, True, True, True])
    assert list(backtest._consecutive_true(s)) == [1, 2, 0, 1, 2, 3]


def _synth(seed, n=400):
    rng = np.random.RandomState(seed)
    px = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    close = pd.Series(px)
    return pd.DataFrame({"Open": close, "High": close * 1.005,
                         "Low": close * 0.995, "Close": close, "Volume": 1e6})


def test_run_backtest_produces_stats():
    data = {"A": _synth(0), "B": _synth(1)}
    recs, stats = backtest.run_backtest(data, PARAMS)
    assert not recs.empty
    assert stats["n"] > 0
    assert {"high", "mid", "low", "sq_on", "sq_off"}.issubset(stats)
    assert 0 <= stats["coverage"] <= 100
    # expansion and within_band columns are present and sane
    assert (recs["expansion"] >= 0).all()
    assert recs["within_band"].dtype == bool


def test_no_lookahead_short_series_empty():
    # Not enough bars for percentile_lookback + horizon -> no records.
    short = {"A": _synth(0, n=60)}
    recs, stats = backtest.run_backtest(short, PARAMS)
    assert recs.empty
    assert stats == {}
