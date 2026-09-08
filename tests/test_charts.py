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


def test_each_series_carries_its_calendar_month_record(tmp_path):
    data = {"NVDA": _synth(n=1300, seed=7, start="2019-01-02")}
    payload = json.loads(charts.write_charts(data, tmp_path).read_text(encoding="utf-8"))
    seas = payload["series"][0]["seasonality"]
    assert [m["month"] for m in seas["months"]] == list(range(1, 13))
    assert seas["years"]["start"] < seas["years"]["end"]
    assert seas["best_month"] in range(1, 13)
    assert seas["worst_month"] in range(1, 13)


def test_seasonality_is_also_pooled_across_tickers(tmp_path):
    data = {"A": _synth(n=1300, seed=8, start="2019-01-02"),
            "B": _synth(n=1300, seed=9, start="2019-01-02")}
    payload = json.loads(charts.write_charts(data, tmp_path).read_text(encoding="utf-8"))
    pooled = payload["seasonality"]
    assert pooled["tickers"] == 2
    jan = pooled["months"][0]
    assert jan["n"] == jan["years"] * 2          # both names, every year


def test_seasonality_is_null_when_there_is_too_little_history(tmp_path):
    payload = json.loads(
        charts.write_charts({"X": _synth(n=20, seed=1)}, tmp_path).read_text(encoding="utf-8"))
    assert payload["series"][0]["seasonality"] is None
    assert payload["seasonality"] is None


def test_cards_are_trimmed_to_the_display_window_but_months_use_it_all(tmp_path):
    data = {"X": _synth(n=2600, seed=11, start="2016-01-04")}          # ~10 years
    payload = json.loads(charts.write_charts(
        data, tmp_path, period_label="10y", display_years=5).read_text(encoding="utf-8"))

    assert payload["period"] == "5y"           # what the cards show
    assert payload["history_period"] == "10y"  # what was downloaded
    s = payload["series"][0]
    assert s["bars"] < 1400                    # five years of sessions, not ten
    full = data["X"]["Close"]
    cutoff = full.index[-1] - pd.DateOffset(years=5)
    assert pd.Timestamp(s["start"]) > cutoff
    assert s["end"] == full.index[-1].strftime("%Y-%m-%d")
    assert s["low"] > round(float(full.min()), 2)   # the decade's low is outside it
    assert s["high"] == pytest.approx(round(float(full[full.index > cutoff].max()), 2))

    # The month tables still see the whole download.
    assert s["seasonality"]["years"]["start"] == 2016
    assert payload["seasonality"]["years"]["start"] == 2016


def test_a_stale_series_cannot_stretch_the_reported_window(tmp_path):
    # Two bars a decade apart: nothing lands inside the display window, so the
    # card falls back to drawing what it has. The window still describes 5y.
    idx = pd.DatetimeIndex(["2016-01-04", "2025-12-19"])
    data = {"STALE": pd.DataFrame({"Close": [10.0, 20.0]}, index=idx),
            "FRESH": _synth(n=1300, seed=13, start="2020-12-01")}
    payload = json.loads(charts.write_charts(
        data, tmp_path, period_label="10y", display_years=5).read_text(encoding="utf-8"))

    stale = [s for s in payload["series"] if s["ticker"] == "STALE"][0]
    assert stale["bars"] == 2 and stale["start"] == "2016-01-04"   # the card kept its bars
    assert payload["window"]["start"] > "2020-01-01"               # the label did not follow


def test_display_years_of_zero_charts_the_whole_download(tmp_path):
    data = {"X": _synth(n=2600, seed=12, start="2016-01-04")}
    payload = json.loads(charts.write_charts(
        data, tmp_path, period_label="10y", display_years=None).read_text(encoding="utf-8"))
    assert payload["period"] == "10y"
    assert payload["series"][0]["bars"] == 2600


def test_window_spans_every_ticker(tmp_path):
    data = {"OLD": _synth(seed=5, start="2020-01-02"), "NEW": _synth(seed=6, start="2023-01-02")}
    payload = json.loads(charts.write_charts(data, tmp_path).read_text(encoding="utf-8"))
    assert payload["window"]["start"].startswith("2020")
    assert payload["window"]["end"] > payload["window"]["start"]
