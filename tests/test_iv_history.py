"""The implied-vol log and the test against it."""

from __future__ import annotations

import numpy as np
import pandas as pd

from spread_scanner import iv_history


class _View:
    def __init__(self, iv, spot=100.0):
        self.spot, self.expiry, self.days_to_expiry = spot, "2026-10-17", 21
        self.iv_annual, self.implied_move_pct, self.hist_move_pct = iv, iv * 0.2, 4.0
        self.hv_annual, self.premium_score, self.premium_state = 25.0, 30.0, "cheap"


def test_rows_skip_implausible_iv_and_undated_names():
    rows = iv_history.rows_from_views(
        {"A": "2026-09-01", "B": "2026-09-01"},
        {"A": _View(30.0), "B": _View(1.0), "C": _View(30.0)},
        scores={"A": 70.0}, min_iv=5.0)
    assert [r["ticker"] for r in rows] == ["A"]
    assert rows[0]["score"] == 70.0


def test_append_replaces_a_rerun_of_the_same_date(tmp_path):
    path = tmp_path / "iv.csv"
    views = {"A": _View(30.0), "B": _View(40.0)}
    dates = {"A": "2026-09-01", "B": "2026-09-01"}
    iv_history.append(path, iv_history.rows_from_views(dates, views))
    iv_history.append(path, iv_history.rows_from_views(dates, {"A": _View(35.0)}))
    iv_history.append(path, iv_history.rows_from_views({"A": "2026-09-02"}, {"A": _View(31.0)}))
    df = iv_history.load(path)
    assert len(df) == 3
    a1 = df[(df["ticker"] == "A") & (df["date"] == "2026-09-01")]
    assert a1["iv_annual"].tolist() == [35.0]


def test_load_missing_file_is_empty(tmp_path):
    df = iv_history.load(tmp_path / "nope.csv")
    assert df.empty and list(df.columns) == iv_history.COLUMNS


def _prices(n=80, step=0.0):
    idx = pd.bdate_range("2026-01-01", periods=n)
    return pd.DataFrame({"Close": 100 * np.exp(np.arange(n) * step)}, index=idx)


def test_matured_outcomes_start_at_the_logged_close_and_skip_unmatured():
    px = _prices(step=0.01)
    dates = [d.date().isoformat() for d in px.index]
    hist = pd.DataFrame([
        {"date": dates[0], "ticker": "A", "implied_move_pct": 5.0, "score": 70, "premium_state": "cheap"},
        {"date": dates[-3], "ticker": "A", "implied_move_pct": 5.0, "score": 70, "premium_state": "cheap"},
    ])
    m = iv_history.matured_outcomes(hist, {"A": px}, horizon=10)
    assert len(m) == 1                                   # the late row has not matured
    expected = (np.exp(0.10) - 1) * 100
    assert abs(m["realized_move_pct"].iloc[0] - expected) < 1e-6
    assert bool(m["beat_implied"].iloc[0]) is True
    cost = iv_history.STRADDLE_FACTOR * 5.0
    assert abs(m["straddle_return"].iloc[0] - (expected - cost) / cost) < 1e-9


def test_implied_backtest_waits_for_enough_matured_rows():
    assert iv_history.implied_backtest(pd.DataFrame(columns=iv_history.COLUMNS), {}, 10)["ok"] is False
    px = _prices(n=200)
    dates = [d.date().isoformat() for d in px.index]
    hist = pd.DataFrame([{"date": d, "ticker": "A", "implied_move_pct": 3.0, "score": 65,
                          "premium_state": "cheap"} for d in dates[:20]])
    out = iv_history.implied_backtest(hist, {"A": px}, 10)
    assert out["ok"] is False and out["matured"] == 20
    hist = pd.DataFrame([{"date": d, "ticker": "A", "implied_move_pct": 3.0, "score": 65,
                          "premium_state": "cheap"} for d in dates[:150]])
    out = iv_history.implied_backtest(hist, {"A": px}, 10)
    assert out["ok"] is True
    # A flat stock never beats a positive implied move; the straddle loses it all.
    assert out["buckets"]["all"]["beat_implied_pct"] == 0.0
    assert out["buckets"]["coiled_cheap"]["avg_straddle_return_pct"] == -100.0


def test_series_by_ticker_is_oldest_first_and_capped():
    hist = pd.DataFrame([{"date": f"2026-01-{d:02d}", "ticker": "A", "iv_annual": float(d)}
                         for d in (3, 1, 2)] + [{"date": "2026-01-01", "ticker": "B",
                                                 "iv_annual": None}])
    assert iv_history.series_by_ticker(hist) == {"A": [1.0, 2.0, 3.0]}
    assert iv_history.series_by_ticker(hist, last=2) == {"A": [2.0, 3.0]}
    assert iv_history.series_by_ticker(pd.DataFrame(columns=iv_history.COLUMNS)) == {}
