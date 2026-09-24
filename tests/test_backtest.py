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
    bars = payload["bars"]
    assert bars["train"] + bars["test"] + bars["embargoed"] == bars["total"]
    assert bars["embargoed"] > 0
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


# --------------------------------------------------- the statistics that claim


def test_non_overlapping_keeps_one_bar_per_horizon_per_ticker():
    data = {"A": _synth(0), "B": _synth(1)}
    recs, _ = backtest.run_backtest(data, PARAMS)
    indep = backtest.non_overlapping(recs, 10)
    assert 0 < len(indep) <= len(recs) // 10 + 2
    for _, g in indep.groupby("ticker"):
        assert (g["pos"].diff().dropna() == 10).all()


def test_run_backtest_reports_an_independent_interval():
    data = {t: _synth(i) for i, t in enumerate("ABCD")}
    _, stats = backtest.run_backtest(data, PARAMS)
    ind = stats["independent"]
    assert ind["n"] < stats["n"]
    own = ind["own_band"]
    if own["edge_pts"] is not None:
        assert own["lo_pts"] <= own["edge_pts"] <= own["hi_pts"]


def test_bootstrap_edge_resamples_dates_and_brackets_the_point():
    rng = np.random.RandomState(0)
    n = 400
    recs = pd.DataFrame({
        "date": np.repeat(np.arange(100), 4),
        "broke_band": rng.rand(n) < 0.4,
    })
    hi = pd.Series(np.tile([True, True, False, False], 100))
    # Make the high bucket break far more often: a clear, positive edge.
    recs.loc[hi.values, "broke_band"] = rng.rand(hi.sum()) < 0.8
    out = backtest.bootstrap_edge(recs, hi, ~hi, "broke_band", reps=500)
    assert out["dates"] == 100
    assert out["lo_pts"] > 0
    assert out["lo_pts"] <= out["edge_pts"] <= out["hi_pts"]
    # Deterministic: the published interval must not wobble between runs.
    assert backtest.bootstrap_edge(recs, hi, ~hi, "broke_band", reps=500) == out


def test_verdict_needs_the_interval_to_clear_zero():
    data = {"A": _synth(0), "B": _synth(1)}
    _, stats = backtest.run_backtest(data, PARAMS)
    stats["independent"]["own_band"] = {"edge_pts": 12.0, "lo_pts": -3.0, "hi_pts": 25.0, "dates": 40}
    payload = backtest.backtest_payload(stats, PARAMS, n_tickers=2, years=5)
    assert payload["verdict"]["holds"] is False
    stats["independent"]["own_band"] = {"edge_pts": 12.0, "lo_pts": 2.0, "hi_pts": 25.0, "dates": 40}
    payload = backtest.backtest_payload(stats, PARAMS, n_tickers=2, years=5)
    assert payload["verdict"]["holds"] is True
    assert payload["verdict"]["long_band_text"]
    json.dumps(payload)


def test_calibration_row_shows_the_train_weights_that_produced_it():
    data = {"A": _synth(0), "B": _synth(1), "C": _synth(2)}
    recs, _ = backtest.run_backtest(data, PARAMS)
    c = backtest.calibrate_weights(recs)
    payload = backtest.calibration_payload(c, years=5, universe=3)
    assert payload["separation"]["calibrated"]["weights"] == c["train_weights"]
    assert payload["weights"] == c["weights"]
    assert payload["separation_basis"] == "quintile"


def test_calibration_embargo_keeps_training_outcomes_out_of_the_test_split():
    data = {"A": _synth(0), "B": _synth(1)}
    recs, _ = backtest.run_backtest(data, PARAMS)
    c = backtest.calibrate_weights(recs, embargo=10)
    dates = np.sort(recs["date"].unique())
    gap = ((dates > c["cutoff"]) & (dates < c["test_start"])).sum()
    assert gap == 10


def test_calibration_holds_only_when_strictly_better():
    c = {"lift": {}, "weights": {}, "train_weights": {}, "cutoff": 0, "test_start": None,
         "n": 3, "n_train": 2, "n_test": 1,
         "sep_heuristic": (40.0, 30.0, 10.0), "sep_calibrated": (39.8, 30.0, 9.8)}
    assert backtest.calibration_payload(c, years=5, universe=1)["verdict"]["holds"] is False
    c["sep_calibrated"] = (41.0, 30.0, 11.0)
    assert backtest.calibration_payload(c, years=5, universe=1)["verdict"]["holds"] is True
