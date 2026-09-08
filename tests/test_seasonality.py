import warnings

import numpy as np
import pandas as pd
import pytest

from spread_scanner import seasonality as se


def _closes(start, end, daily=0.0):
    """A business-day close series compounding at a fixed daily rate."""
    idx = pd.bdate_range(start=start, end=end)
    return pd.Series(100.0 * np.cumprod(np.full(len(idx), 1.0 + daily)), index=idx)


def _seasonal(years=8, up_month=4, down_month=9, seed=0):
    """``years`` whole calendar years of closes, ending 2025, where one month is
    reliably strong and another reliably weak. The run starts in the December
    before so that January is a whole month too."""
    idx = pd.bdate_range(start=f"{2025 - years}-12-01", end="2025-12-31")
    rng = np.random.RandomState(seed)
    step = rng.normal(0.0, 0.001, len(idx))
    step += np.where(idx.month == up_month, 0.004, 0.0)
    step += np.where(idx.month == down_month, -0.004, 0.0)
    return pd.Series(100.0 * np.cumprod(1.0 + step), index=idx)


# ---- whole months only ----------------------------------------------------

def test_partial_first_and_last_months_are_excluded():
    # Starts mid-March, ends mid-October: neither March nor October is whole.
    r = se.monthly_returns(_closes("2024-03-14", "2024-10-15", daily=0.001))
    assert [str(p) for p in r.index] == ["2024-04", "2024-05", "2024-06",
                                         "2024-07", "2024-08", "2024-09"]


def test_a_month_ending_on_a_weekend_still_counts():
    # August 2025 ends on a Sunday; the last trading day is Friday the 29th.
    r = se.monthly_returns(_closes("2025-01-01", "2025-08-29", daily=0.001))
    assert str(r.index[-1]) == "2025-08"


def test_the_month_in_progress_is_dropped_until_its_last_session():
    # January 2026 ends on Friday the 30th. Through the 27th there are three
    # sessions still to come, so January is not yet a January.
    through_27 = se.monthly_returns(_closes("2025-06-02", "2026-01-27", daily=0.001))
    assert str(through_27.index[-1]) == "2025-12"
    through_30 = se.monthly_returns(_closes("2025-06-02", "2026-01-30", daily=0.001))
    assert str(through_30.index[-1]) == "2026-01"


def test_a_gap_in_the_history_is_not_charged_to_the_next_month():
    closes = _closes("2024-01-01", "2024-12-31", daily=0.001)
    closes = closes[(closes.index.month != 5) & (closes.index.month != 6)]
    months = [str(p) for p in se.monthly_returns(closes).index]
    assert "2024-05" not in months and "2024-06" not in months
    assert "2024-07" not in months            # its predecessor is missing too
    assert "2024-04" in months and "2024-08" in months


def test_returns_are_percent_moves_of_the_month():
    # January 2024 is measured from the last close of December to the last of
    # January — not from the first January bar.
    closes = _closes("2023-12-01", "2024-02-29", daily=0.01)
    dec, jan = closes["2023-12"].iloc[-1], closes["2024-01"].iloc[-1]
    r = se.monthly_returns(closes)
    assert r.loc[pd.Period("2024-01", "M")] == pytest.approx((jan / dec - 1) * 100, abs=0.01)
    assert r.loc[pd.Period("2024-01", "M")] > 20          # +1% a day for a month


# ---- the month table ------------------------------------------------------

def test_a_gap_is_dropped_rather_than_padded_flat():
    # The regression this guards: pandas pads NaN in pct_change by default, which
    # would invent a flat month for the hole and charge two months' move to the
    # month after it. Both must be absent, and the survivors unchanged.
    full = _closes("2024-01-01", "2024-12-31", daily=0.001)
    holed = full[full.index.month != 5]
    a, b = se.monthly_returns(full), se.monthly_returns(holed)
    assert [str(p) for p in b.index] == [m for m in map(str, a.index)
                                         if m not in ("2024-05", "2024-06")]
    assert b.loc[pd.Period("2024-04", "M")] == pytest.approx(a.loc[pd.Period("2024-04", "M")])
    assert b.loc[pd.Period("2024-07", "M")] == pytest.approx(a.loc[pd.Period("2024-07", "M")])


def test_a_tz_aware_index_is_handled_without_warnings():
    closes = _closes("2024-01-01", "2024-06-28", daily=0.001)
    closes.index = closes.index.tz_localize("America/New_York")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        r = se.monthly_returns(closes)
    assert [str(p) for p in r.index] == ["2024-02", "2024-03", "2024-04", "2024-05", "2024-06"]


def test_summarize_finds_the_planted_best_and_worst_months():
    s = se.summarize(se.monthly_returns(_seasonal(up_month=4, down_month=9)))
    assert s["best_month"] == 4
    assert s["worst_month"] == 9
    assert s["months"][3]["win_rate_pct"] == 100.0    # April never fell
    assert s["months"][8]["avg_pct"] < 0
    assert s["years"]["count"] == 8


def test_every_month_is_present_even_with_no_data():
    rows = se.month_rows(se.monthly_returns(_closes("2024-01-01", "2024-05-31", daily=0.001)))
    assert [r["month"] for r in rows] == list(range(1, 13))
    assert [r["name"] for r in rows][:3] == ["Jan", "Feb", "Mar"]
    assert rows[10] == {"month": 11, "name": "Nov", "n": 0, "years": 0, "avg_pct": None,
                        "median_pct": None, "win_rate_pct": None,
                        "best_pct": None, "worst_pct": None}


def test_thin_months_are_reported_but_never_ranked():
    # Two years only: every month is below MIN_YEARS, so nothing is named.
    s = se.summarize(se.monthly_returns(_seasonal(years=2)))
    assert s["best_month"] is None and s["worst_month"] is None
    assert all(r["n"] == 2 for r in s["months"])
    assert s["months"][0]["avg_pct"] is not None      # still reported


def test_a_short_history_summarizes_to_nothing():
    assert se.summarize(se.monthly_returns(_closes("2024-01-05", "2024-02-10"))) is None
    assert se.summarize(pd.Series(dtype="float64")) is None
    assert se.monthly_returns(pd.Series(dtype="float64")).empty


# ---- pooling across tickers ----------------------------------------------

def test_pooled_counts_both_tickers_and_years():
    rets = {t: se.monthly_returns(_seasonal(seed=i)) for i, t in enumerate("ABC")}
    p = se.pooled(rets)
    assert p["tickers"] == 3
    assert p["best_month"] == 4 and p["worst_month"] == 9
    jan = p["months"][0]
    assert jan["n"] == 24 and jan["years"] == 8 and jan["tickers"] == 3
    assert jan["ticker_years"] == {"min": 8, "median": 8}
    assert p["min_years"] == se.MIN_YEARS


def test_pooled_ranking_does_not_lean_on_one_long_history():
    # One name with eight years, four with two. The pooled row still spans eight
    # calendar years, but the typical name has two — so nothing is ranked.
    rets = {"LONG": se.monthly_returns(_seasonal(years=8))}
    for i in range(4):
        rets[f"SHORT{i}"] = se.monthly_returns(_seasonal(years=2, seed=i + 1))
    p = se.pooled(rets)
    jan = p["months"][0]
    assert jan["years"] == 8                      # the span, across every name
    assert jan["ticker_years"] == {"min": 2, "median": 2}
    assert p["best_month"] is None and p["worst_month"] is None

    # Give the shorter names three years each and the ranking comes back.
    rets = {"LONG": se.monthly_returns(_seasonal(years=8))}
    for i in range(4):
        rets[f"SHORT{i}"] = se.monthly_returns(_seasonal(years=3, seed=i + 1))
    p = se.pooled(rets)
    assert p["months"][0]["ticker_years"]["median"] == 3
    assert p["best_month"] == 4 and p["worst_month"] == 9


def test_pooled_ignores_tickers_with_no_usable_history():
    rets = {"GOOD": se.monthly_returns(_seasonal()),
            "SHORT": se.monthly_returns(_closes("2024-01-05", "2024-02-10")),
            "BARE": pd.Series(dtype="float64")}
    p = se.pooled(rets)
    assert p["tickers"] == 1
    assert all(r["tickers"] <= 1 for r in p["months"])


def test_pooled_of_nothing_is_none():
    assert se.pooled({}) is None
    assert se.pooled({"X": pd.Series(dtype="float64")}) is None
