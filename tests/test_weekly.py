import numpy as np
import pandas as pd
import pytest

from spread_scanner import weekly as wk


def _frame(start, end, daily=0.0, seed=0, spread=0.01):
    """A business-day OHLC frame compounding at a fixed daily rate.

    The high and low sit a fixed fraction either side of the close, so a weekly
    high is always the largest close of the week times ``1 + spread`` — which
    makes every touch test below checkable by hand.
    """
    idx = pd.bdate_range(start=start, end=end)
    close = pd.Series(100.0 * np.cumprod(np.full(len(idx), 1.0 + daily)), index=idx)
    return pd.DataFrame({"Open": close, "High": close * (1 + spread),
                         "Low": close * (1 - spread), "Close": close,
                         "Volume": 1_000_000}, index=idx)


def _bars(*args, **kwargs):
    return wk.weekly_bars(_frame(*args, **kwargs))


# ---- one week is one ISO week ---------------------------------------------

def test_a_week_is_indexed_by_its_monday():
    # 2025-09-01 is a Monday; the week runs to Friday the 5th.
    bars = _bars("2025-09-01", "2025-09-19")
    assert [d.strftime("%Y-%m-%d") for d in bars.index] == ["2025-09-01", "2025-09-08", "2025-09-15"]


def test_a_week_takes_the_high_the_low_and_the_last_close():
    bars = _bars("2025-09-01", "2025-09-12", daily=0.01)
    first = bars.iloc[0]
    daily = _frame("2025-09-01", "2025-09-05", daily=0.01)
    assert first["close"] == pytest.approx(daily["Close"].iloc[-1])
    assert first["high"] == pytest.approx(daily["High"].max())
    assert first["low"] == pytest.approx(daily["Low"].min())


def test_a_run_starting_midweek_still_makes_that_week():
    # Wednesday the 3rd: a partial first week, kept, because a high and a close
    # are real prices however many sessions produced them.
    bars = _bars("2025-09-03", "2025-09-19")
    assert bars.index[0].strftime("%Y-%m-%d") == "2025-09-01"
    assert len(bars) == 3


def test_iso_week_names_the_week_its_monday_opens():
    assert wk.iso_week(pd.Timestamp("2025-09-08")) == "2025-W37"
    # 2021-01-01 is a Friday inside ISO week 2020-W53.
    assert wk.iso_week(pd.Timestamp("2020-12-28")) == "2020-W53"


# ---- the week in progress is dropped --------------------------------------

def test_the_week_in_progress_is_dropped_until_its_last_session():
    # Thursday the 18th: Friday is still due, so that week is not a week yet.
    through_thu = _bars("2025-08-01", "2025-09-18")
    assert through_thu.index[-1].strftime("%Y-%m-%d") == "2025-09-08"
    through_fri = _bars("2025-08-01", "2025-09-19")
    assert through_fri.index[-1].strftime("%Y-%m-%d") == "2025-09-15"


def test_a_week_ending_on_a_weekend_still_counts():
    # Friday the 19th is the last session; Saturday is not another one.
    bars = _bars("2025-08-01", "2025-09-20")
    assert bars.index[-1].strftime("%Y-%m-%d") == "2025-09-15"


# ---- degenerate frames -----------------------------------------------------

def test_a_frame_with_no_close_makes_no_bars():
    assert wk.weekly_bars(pd.DataFrame({"Open": [1.0, 2.0]})).empty
    assert wk.weekly_bars(None).empty


def test_one_bar_is_not_a_series():
    assert wk.weekly_bars(_frame("2025-09-01", "2025-09-01")).empty


def test_a_close_only_frame_falls_back_to_the_close():
    frame = _frame("2025-09-01", "2025-09-19")[["Close"]]
    bars = wk.weekly_bars(frame)
    assert (bars["high"] == bars["close"]).all()
    assert (bars["low"] == bars["close"]).all()


def test_a_tz_aware_index_is_handled():
    frame = _frame("2025-09-01", "2025-09-19")
    frame.index = frame.index.tz_localize("America/New_York")
    bars = wk.weekly_bars(frame)
    assert [d.strftime("%Y-%m-%d") for d in bars.index] == ["2025-09-01", "2025-09-08", "2025-09-15"]


# ---- the shared axis -------------------------------------------------------

def _payload(frames, **kwargs):
    return wk.build_weekly(frames, period_label="10y", **kwargs)


def test_the_axis_is_contiguous_even_where_a_name_has_a_hole():
    frame = _frame("2023-01-02", "2025-09-19")
    holed = frame[(frame.index < "2024-03-01") | (frame.index > "2024-04-01")]
    payload = _payload({"AAA": frame, "BBB": holed})

    starts = pd.to_datetime(payload["starts"])
    assert (starts.to_series().diff().dropna() == pd.Timedelta(days=7)).all()

    # The hole is null in BBB and present in AAA, at the same positions.
    aaa = next(s for s in payload["series"] if s["ticker"] == "AAA")
    bbb = next(s for s in payload["series"] if s["ticker"] == "BBB")
    gap = [i for i, d in enumerate(payload["starts"]) if "2024-03-04" <= d <= "2024-03-25"]
    assert gap, "expected the hole to land on the axis"
    assert all(bbb["close"][i] is None for i in gap)
    assert all(aaa["close"][i] is not None for i in gap)


def test_every_series_is_as_long_as_the_axis():
    short = _frame("2024-01-01", "2025-09-19")
    payload = _payload({"AAA": _frame("2023-01-02", "2025-09-19"), "BBB": short})
    n = len(payload["weeks"])
    assert n == len(payload["starts"])
    for s in payload["series"]:
        assert len(s["close"]) == n and len(s["high"]) == n and len(s["low"]) == n


def test_a_late_lister_reports_its_own_first_and_last_week():
    payload = _payload({"AAA": _frame("2023-01-02", "2025-09-19"),
                        "BBB": _frame("2024-06-03", "2025-09-19")})
    bbb = next(s for s in payload["series"] if s["ticker"] == "BBB")
    assert bbb["first"] == "2024-06-03"
    assert bbb["last"] == "2025-09-15"
    assert bbb["weeks"] < len(payload["weeks"])
    # The weeks before it listed are null, not zero.
    assert bbb["close"][0] is None


def test_a_name_with_too_little_history_is_left_out():
    payload = _payload({"AAA": _frame("2023-01-02", "2025-09-19"),
                        "NEW": _frame("2025-08-01", "2025-09-19")})
    assert [s["ticker"] for s in payload["series"]] == ["AAA"]
    assert payload["count"] == 1


def test_no_usable_names_still_makes_a_payload():
    payload = _payload({"NEW": _frame("2025-08-01", "2025-09-19")})
    assert payload["series"] == [] and payload["weeks"] == [] and payload["count"] == 0
    assert payload["reference"]["result"]


def test_years_trims_from_the_newest_end():
    payload = _payload({"AAA": _frame("2015-01-05", "2025-09-19")}, years=3)
    assert payload["starts"][0] >= "2022-09-01"
    assert payload["starts"][-1] == "2025-09-15"
    # The trim lands on a Monday, so the axis starts on a whole week.
    assert pd.Timestamp(payload["starts"][0]).weekday() == 0


def test_the_payload_carries_the_rules_it_is_read_by():
    payload = _payload({"AAA": _frame("2023-01-02", "2025-09-19")})
    for key in ("entry", "window", "result", "touch", "incomplete", "ranking", "prices"):
        assert payload["reference"][key].strip()
    # Both floors ship with the data, so the page gates on the backend's numbers
    # rather than on a copy of them.
    assert payload["min_weeks"] == wk.MIN_WEEKS
    assert payload["min_years"] == wk.MIN_YEARS
    assert payload["period"] == "10y"


# ---- what the frontend actually walks --------------------------------------

def test_a_window_read_by_position_spans_the_weeks_it_claims_to():
    """The axis is what makes "eight weeks later" mean eight weeks.

    This is the arithmetic the Repeat test does in the browser, asserted here on
    the payload it does it to.
    """
    payload = _payload({"AAA": _frame("2023-01-02", "2025-09-19")})
    starts = payload["starts"]
    i = starts.index("2024-09-02")
    assert starts[i + 8] == "2024-10-28"
    assert payload["weeks"][i] == "2024-W36"


def test_prices_survive_the_round_trip_to_two_decimals():
    payload = _payload({"AAA": _frame("2023-01-02", "2025-09-19", daily=0.001)})
    series = payload["series"][0]
    for i, close in enumerate(series["close"]):
        if close is None:
            continue
        assert series["low"][i] <= close <= series["high"][i]
        assert round(close, 2) == close


# ---- writing it out --------------------------------------------------------

def test_write_weekly_round_trips_and_stays_compact(tmp_path):
    import json

    frames = {"AAA": _frame("2015-01-05", "2025-09-19"),
              "BBB": _frame("2015-01-05", "2025-09-19", daily=0.0005, seed=1)}
    path = wk.write_weekly(frames, tmp_path, period_label="10y")
    assert path == tmp_path / "data" / "weekly.json"

    text = path.read_text(encoding="utf-8")
    payload = json.loads(text)
    assert payload["count"] == 2
    assert len(payload["series"][0]["close"]) == len(payload["weeks"])

    # Compact: the numbers are the file, and an indented line each is four times
    # the bytes. One line of JSON plus the trailing newline.
    assert text.count("\n") == 1
    assert ": " not in text.split('"reference"')[0]


def test_the_week_floor_is_measured_on_the_window_that_ships():
    """A name is judged on what it puts in the payload, not on what was fetched.

    Applied only to the download, a `years` trim let a name whose history ended
    years ago through with a handful of weeks in it — on a payload that still
    declares `min_weeks: 26`, which the page quotes back to the reader.
    """
    frames = {"AAA": _frame("2015-01-05", "2025-09-19"),
              "STOPPED": _frame("2015-01-05", "2023-11-30")}

    # Untrimmed, STOPPED has a decade of weeks and belongs in the payload.
    assert [s["ticker"] for s in _payload(frames)["series"]] == ["AAA", "STOPPED"]

    # Inside a two-year window it has about eleven, which is not a history.
    trimmed = _payload(frames, years=2)
    assert [s["ticker"] for s in trimmed["series"]] == ["AAA"]
    assert trimmed["count"] == 1
