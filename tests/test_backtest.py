import json

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


def test_backtest_payload_shape():
    data = {"A": _synth(0), "B": _synth(1)}
    _, stats = backtest.run_backtest(data, PARAMS)
    payload = backtest.backtest_payload(stats, PARAMS, n_tickers=2, years=5)

    assert payload["ok"] is True
    assert payload["universe"] == 2 and payload["history_years"] == 5
    assert set(payload["buckets"]) == {"high", "mid", "low"}
    assert set(payload["squeeze"]) == {"on", "off"}
    for bucket in payload["buckets"].values():
        assert bucket["label"]
        assert isinstance(bucket["bars"], int)
    assert isinstance(payload["verdict"]["holds"], bool)
    assert payload["verdict"]["text"]
    json.dumps(payload)                       # must survive serialization


def test_backtest_payload_without_stats_says_so():
    payload = backtest.backtest_payload({}, PARAMS, n_tickers=0, years=5)
    assert payload["ok"] is False and payload["note"]
    json.dumps(payload)


def test_calibration_payload_shape():
    data = {"A": _synth(0), "B": _synth(1), "C": _synth(2)}
    recs, _ = backtest.run_backtest(data, PARAMS)
    payload = backtest.calibration_payload(
        backtest.calibrate_weights(recs), years=5, universe=3)

    assert payload["ok"] is True
    assert abs(sum(payload["weights"].values()) - 1.0) < 0.05
    assert set(payload["separation"]) == {"heuristic", "calibrated"}
    assert payload["bars"]["train"] + payload["bars"]["test"] == payload["bars"]["total"]
    assert isinstance(payload["verdict"]["holds"], bool)
    json.dumps(payload)


def test_calibration_payload_without_a_run_says_so():
    payload = backtest.calibration_payload({}, years=5, universe=0)
    assert payload["ok"] is False and payload["note"]
    json.dumps(payload)


def test_round_helper_nulls_non_finite():
    assert backtest._round(float("nan")) is None
    assert backtest._round(float("inf")) is None
    assert backtest._round(None) is None
    assert backtest._round(1.234, 2) == 1.23
