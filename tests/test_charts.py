import json

import numpy as np
import pandas as pd
import pytest

from spread_scanner import charts


def _synth(n=500, seed=0, start="2022-01-03"):
    """~2 years of daily closes on a business-day index."""
    rng = np.random.RandomState(seed)
    px = [100.0]
    for _ in range(n - 1):
        px.append(px[-1] * (1 + rng.normal(0.0005, 0.01)))
    idx = pd.bdate_range(start=start, periods=n)
    close = pd.Series(px, index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close, "Volume": 1e6})


def test_write_charts_emits_a_series_per_ticker(tmp_path):
    data = {"NVDA": _synth(seed=1), "AAPL": _synth(seed=2)}
    path = charts.write_charts(data, tmp_path, period_label="5y")
    assert path == tmp_path / "data" / "charts.json"

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["count"] == 2
    assert payload["period"] == "5y"
    assert [s["ticker"] for s in payload["series"]] == ["AAPL", "NVDA"]   # sorted
    for s in payload["series"]:
        assert len(s["dates"]) == len(s["closes"])
        assert s["low"] <= s["last"] <= s["high"]
        assert s["bars"] == 500


def test_series_are_downsampled_but_keep_both_endpoints(tmp_path):
    data = {"X": _synth(n=1200, seed=3)}
    payload = json.loads(
        charts.write_charts(data, tmp_path, points=100).read_text(encoding="utf-8"))
    s = payload["series"][0]
    assert 90 <= len(s["closes"]) <= 105          # thinned, not exact
    assert s["bars"] == 1200                      # the true bar count is kept
    full = data["X"]["Close"]
    assert s["closes"][0] == pytest.approx(round(float(full.iloc[0]), 2))
    assert s["closes"][-1] == pytest.approx(round(float(full.iloc[-1]), 2))
    assert s["dates"] == sorted(s["dates"])


def test_short_series_is_not_downsampled(tmp_path):
    data = {"X": _synth(n=40, seed=4)}
    payload = json.loads(
        charts.write_charts(data, tmp_path, points=220).read_text(encoding="utf-8"))
    assert len(payload["series"][0]["closes"]) == 40


def test_change_pct_matches_known_move():
    idx = pd.bdate_range("2024-01-01", periods=400)
    close = pd.Series(np.linspace(100.0, 200.0, 400), index=idx)
    assert charts._change_pct(close, days=None) == 100.0  # full window doubled
    yoy = charts._change_pct(close, days=365)
    assert yoy is not None and yoy > 0                    # up over the trailing year


def test_skips_series_with_no_close(tmp_path):
    data = {"GOOD": _synth(seed=3), "BARE": pd.DataFrame({"Volume": [1, 2, 3]})}
    payload = json.loads(charts.write_charts(data, tmp_path).read_text(encoding="utf-8"))
    assert [s["ticker"] for s in payload["series"]] == ["GOOD"]


def test_empty_input_still_writes_a_valid_payload(tmp_path):
    payload = json.loads(charts.write_charts({}, tmp_path).read_text(encoding="utf-8"))
    assert payload["series"] == []
    assert payload["count"] == 0
    assert payload["window"] == {"start": None, "end": None}


def test_window_spans_every_ticker(tmp_path):
    data = {"OLD": _synth(seed=5, start="2020-01-02"), "NEW": _synth(seed=6, start="2023-01-02")}
    payload = json.loads(charts.write_charts(data, tmp_path).read_text(encoding="utf-8"))
    assert payload["window"]["start"].startswith("2020")
    assert payload["window"]["end"] > payload["window"]["start"]
