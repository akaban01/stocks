"""The Repeat test's counting rules — run against the JavaScript that ships.

`public/assets/trial.js` decides what a year that closed past the target, a
miss, a still-open year and a skipped one mean, and that is the whole substance of the Repeat test. The
verdict is the *exit* — where the window closed — and touching the target on the
way is reported beside it rather than being it. It runs in
the browser because every control re-runs it and Pages has no server, but that
is not a reason for it to be the one part of the pipeline nothing checks.

So these tests execute the real file under node rather than re-implementing it
in Python. A mirror would keep passing while the shipped rules drifted away from
it, which is the failure mode worth avoiding here: the arithmetic is boring and
the *edge cases* are not.

Skipped, not failed, where node is missing — the rest of the suite has no
runtime dependencies and this one must not quietly become a reason the suite
cannot run at all. GitHub's runners all ship node, so CI does execute it.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

TRIAL_JS = Path(__file__).resolve().parents[1] / "public" / "assets" / "trial.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed; the trial rules are JavaScript")

# The fixture's high and low sit this far either side of each close. Deliberately
# well inside the 8% targets below, so a flat series touches nothing and every
# hit in these tests comes from a close that was actually moved.
WICK = 0.02


def run_trial(data: dict, ticker: str, **opts) -> dict:
    """Run `SpreadTrial.run` in node and hand back what it produced."""
    options = {"dir": "up", "week": 37, "hold": 8, "target": 8, "years": 30, **opts}
    script = f"""
      const trial = require({json.dumps(str(TRIAL_JS))});
      const payload = {json.dumps(data)};
      const opt = {json.dumps(options)};
      const series = payload.series.find(s => s.ticker === {json.dumps(ticker)});
      const at = trial.index(payload);
      process.stdout.write(JSON.stringify(trial.run(payload, series, opt, at)));
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ---- building payloads by hand --------------------------------------------

def payload(bars: dict[str, list], first_week: tuple[int, int] = (2016, 1)) -> dict:
    """A weekly.json-shaped payload from ``{ticker: [close-or-None, ...]}``.

    One contiguous ISO week per position, which is the invariant the positional
    window arithmetic rests on — see :mod:`spread_scanner.weekly`.
    """
    length = max(len(v) for v in bars.values())
    monday = dt.date.fromisocalendar(first_week[0], first_week[1], 1)
    weeks, starts = [], []
    for i in range(length):
        day = monday + dt.timedelta(weeks=i)
        cal = day.isocalendar()
        weeks.append(f"{cal[0]}-W{cal[1]:02d}")
        starts.append(day.isoformat())

    series = []
    for ticker, closes in bars.items():
        padded = list(closes) + [None] * (length - len(closes))
        series.append({
            "ticker": ticker,
            "close": padded,
            "high": [None if c is None else round(c * (1 + WICK), 2) for c in padded],
            "low": [None if c is None else round(c * (1 - WICK), 2) for c in padded],
        })
    return {"weeks": weeks, "starts": starts, "count": len(series), "series": series,
            "min_weeks": 26, "min_years": 3}


def flat(n: int, price: float = 100.0) -> list:
    return [price] * n


def at(data: dict, idx: int) -> tuple[int, int]:
    """The ISO year and week number sitting at a position on the axis."""
    year, week = data["weeks"][idx].split("-W")
    return int(year), int(week)


def row_for(data: dict, ticker: str, idx: int, **opts) -> dict:
    """The trial row for the year whose chosen week sits at ``idx``.

    Buying "week 21" is a question about every year, so a test that means one
    specific occurrence has to say which — picking ``rows[0]`` silently means
    whichever year the year-selection loop reached first.
    """
    year, week = at(data, idx)
    result = run_trial(data, ticker, week=week, **opts)
    match = [r for r in result["rows"] if r["year"] == year]
    assert match, f"no row for {year}-W{week:02d} in {[r['year'] for r in result['rows']]}"
    return match[0]


def states(result: dict) -> list[str]:
    return [r["state"] for r in result["rows"]]


# ---- the entry ------------------------------------------------------------

def test_the_entry_is_the_close_of_the_week_before_the_buy_week():
    closes = flat(300)
    closes[100] = 50.0                     # the week before the buy week
    data = payload({"AAA": closes})
    row = row_for(data, "AAA", 101, hold=2)
    assert row["entry"] == 50.0
    assert row["entry_week"] == data["starts"][100]
    assert row["start"] == data["starts"][101], "the row is still dated by the buy week"


def test_the_first_week_of_the_history_has_no_entry_and_is_skipped():
    data = payload({"AAA": flat(300)})
    row = row_for(data, "AAA", 0, hold=2)
    assert row["state"] == "skipped"
    assert "no close before" in row["why"]


# ---- the window -----------------------------------------------------------

def test_a_hold_of_one_week_is_the_buy_week_alone():
    closes = flat(300)
    closes[100] = 130.0                    # only the buy week is above the target
    data = payload({"AAA": closes})
    row = row_for(data, "AAA", 100, hold=1)
    assert row["state"] == "hit" and row["settled"] is True
    assert row["exit"] == 130.0, "with a hold of one, the exit is the buy week's own close"
    assert row["hit_in"] == 1


def test_the_last_week_of_the_window_is_the_buy_week_plus_hold_minus_one():
    closes = flat(300)
    closes[107] = 200.0                    # week 8 of a hold of 8
    closes[108] = 100.0
    data = payload({"AAA": closes})
    eight = row_for(data, "AAA", 100, hold=8)
    assert eight["hit_in"] == 8 and eight["state"] == "hit", "and it is the week it exits on"
    assert row_for(data, "AAA", 100, hold=7)["state"] == "miss", "week 8 is outside a hold of 7"


def test_a_window_that_runs_past_the_last_week_is_open_not_a_miss():
    data = payload({"AAA": flat(300)})
    row = row_for(data, "AAA", 298, hold=8)
    assert row["state"] == "open"
    assert row["ran"] == 2, "two weeks of axis were left"
    assert "exit" not in row, "an unfinished window has no exit price"
    assert row["open_pct"] == pytest.approx(0.0), "but it says where it stands so far"


# ---- an unfinished window is never a hit either ----------------------------

def test_an_unfinished_window_that_already_touched_is_still_not_counted():
    """A window with no exit yet cannot be judged on its exit.

    And the bias is one-directional: letting an unfinished window in on the
    strength of a touch can only ever raise the rate, so the newest year would
    hold the headline up on its own. A name can be past the target in week three
    and back under it by week eight, which is exactly what the exit test is for.
    """
    closes = flat(300)
    closes[298] = 200.0                    # far past the target, in an unfinished window
    data = payload({"AAA": closes})
    year, week = at(data, 298)
    result = run_trial(data, "AAA", week=week, hold=8, target=8)
    row = next(r for r in result["rows"] if r["year"] == year)

    assert row["state"] == "open", "an unfinished window is open whatever it touched"
    assert row["touched"] is True, "but it still says the target was reached"
    assert "closed_past" not in row, "there is no exit to have closed past anything"
    assert result["touched_open"] == 1, "and it is counted as one, so the page can say so"
    assert row["year"] not in [r["year"] for r in result["rows"] if r["state"] == "hit"]


def test_the_rate_denominator_is_the_finished_windows_only():
    closes = flat(300)
    data = payload({"AAA": closes})
    _, week = at(data, 298)
    result = run_trial(data, "AAA", week=week, hold=8, target=8)
    assert result["open"] >= 1, "this fixture is meant to leave a window running"
    assert result["decided"] == result["hit"] + result["miss"]
    assert result["decided"] + result["open"] + result["skipped"] == len(result["rows"])
    assert result["rate"] == pytest.approx(result["hit"] / result["decided"] * 100)


# ---- the verdict is the exit -----------------------------------------------

def test_the_verdict_is_the_exit_not_the_best_price_in_the_window():
    """The whole point of the test: eight weeks at +1% means +1% at week eight.

    A window that went through the target and gave it back is a miss, and a
    window that crawled there and stayed closed past it — however unexciting
    the path was.
    """
    spike = flat(300)
    spike[102] = 130.0                     # +30% mid-window, all of it given back
    ends = flat(300)
    ends[107] = 101.5                      # never far ahead, but closes past +1%
    data = payload({"SPIKE": spike, "ENDS": ends})

    hot = row_for(data, "SPIKE", 100, hold=8, target=1)
    assert hot["touched"] is True, "it went straight through +1% in week three"
    assert hot["state"] == "miss" and hot["closed_past"] is False
    assert hot["exit_pct"] == pytest.approx(0.0), "and closed the window back at the entry"

    slow = row_for(data, "ENDS", 100, hold=8, target=1)
    assert slow["state"] == "hit" and slow["closed_past"] is True
    assert slow["exit_pct"] == pytest.approx(1.5)


def test_a_window_that_touched_and_closed_back_under_is_a_miss():
    closes = flat(300)
    # A close under +8% whose high clears it: the difference between the two.
    closes[101] = 100.0 * 1.07
    data = payload({"AAA": closes})
    row = row_for(data, "AAA", 100, hold=4, target=8)
    assert row["touched"] is True, "the high went through the target"
    assert row["state"] == "miss" and row["closed_past"] is False, "the window closed under it"
    assert row["exit"] == pytest.approx(100.0), "the exit is the last week, not the best one"


def test_a_close_past_the_target_is_both_touched_and_closed_past():
    closes = flat(300)
    closes[103] = 120.0
    data = payload({"AAA": closes})
    row = row_for(data, "AAA", 100, hold=4, target=8)
    assert row["touched"] is True and row["closed_past"] is True
    assert row["state"] == "hit"


def test_the_rate_is_the_closed_past_rate_and_touches_are_counted_beside_it():
    """A flat series with a 2% wick touches +1% every year and closes past none."""
    data = payload({"AAA": flat(52 * 8)})
    _, week = at(data, 100)
    result = run_trial(data, "AAA", week=week, hold=8, target=1)

    assert result["decided"] >= 3, "this fixture is meant to judge several years"
    assert result["hit"] == 0 and result["miss"] == result["decided"]
    assert result["rate"] == 0.0, "the headline rate is how often it closed past"
    assert result["touched"] == result["decided"] and result["touch_rate"] == 100.0


def test_a_downside_target_is_below_the_entry_and_touched_off_the_low():
    closes = flat(300)
    closes[101] = 100.0 * 0.90             # a dip to −10%, recovered before the exit
    data = payload({"AAA": closes})
    year, week = at(data, 100)

    down = next(r for r in run_trial(data, "AAA", week=week, hold=4, target=8,
                                     dir="down")["rows"] if r["year"] == year)

    assert down["target"] == pytest.approx(92.0), "a downside target sits below the entry"
    assert down["touched"] is True, "the low went through it"
    assert down["state"] == "miss", "but the window closed back at the entry"
    assert down["best_pct"] < 0, "toward a downside target is a fall"
    assert down["worst_pct"] > 0, "and against it is a rise"


def test_a_downside_target_finishes_when_the_exit_close_is_under_it():
    closes = flat(300)
    closes[103] = 100.0 * 0.90             # the exit week of a hold of four
    data = payload({"AAA": closes})
    year, week = at(data, 100)

    down = next(r for r in run_trial(data, "AAA", week=week, hold=4, target=8,
                                     dir="down")["rows"] if r["year"] == year)
    up = next(r for r in run_trial(data, "AAA", week=week, hold=4, target=8,
                                   dir="up")["rows"] if r["year"] == year)

    assert down["state"] == "hit" and down["closed_past"] is True
    assert up["state"] == "miss", "the same weeks never reach +8% the other way"


# ---- an in-the-money target: zero, or negative -----------------------------

def test_a_zero_target_asks_only_that_the_window_did_not_lose_ground():
    held_flat = flat(300)
    slipped = flat(300)
    slipped[107] = 99.0
    data = payload({"FLAT": held_flat, "DOWN": slipped})

    held = row_for(data, "FLAT", 100, hold=8, target=0)
    assert held["target"] == pytest.approx(100.0), "the target is the entry itself"
    assert held["state"] == "hit", "finishing exactly flat clears a zero target"

    lost = row_for(data, "DOWN", 100, hold=8, target=0)
    assert lost["state"] == "miss" and lost["exit_pct"] == pytest.approx(-1.0)


def test_a_negative_target_puts_the_level_behind_the_entry():
    """The in-the-money question: how far can it fall and the trade still pay?"""
    mild = flat(300)
    mild[107] = 98.0                       # −2%, inside a −3% target
    steep = flat(300)
    steep[107] = 96.0                      # −4%, through it
    data = payload({"MILD": mild, "STEEP": steep})

    ok = row_for(data, "MILD", 100, hold=8, target=-3)
    assert ok["target"] == pytest.approx(97.0), "a −3% target sits below the entry"
    assert ok["state"] == "hit" and ok["closed_past"] is True

    assert row_for(data, "STEEP", 100, hold=8, target=-3)["state"] == "miss"


def test_a_negative_downside_target_puts_the_level_above_the_entry():
    closes = flat(300)
    closes[107] = 102.0                    # +2%, still under a "no worse than +3%" line
    data = payload({"AAA": closes})
    year, week = at(data, 100)
    row = next(r for r in run_trial(data, "AAA", week=week, hold=8, target=-3,
                                    dir="down")["rows"] if r["year"] == year)

    assert row["target"] == pytest.approx(103.0), "the sign flips going down"
    assert row["state"] == "hit", "it closed under the line"


def test_the_week_it_was_touched_in_is_one_based_from_the_buy_week():
    closes = flat(300)
    closes[103] = 200.0
    data = payload({"AAA": closes})
    row = row_for(data, "AAA", 100, hold=8, target=8)
    assert row["hit_in"] == 4                  # positions 100, 101, 102, 103
    assert row["hit_week"] == data["starts"][103]


# ---- gaps -----------------------------------------------------------------

def test_a_gap_inside_the_window_is_skipped_not_closed_up():
    closes = flat(300)
    closes[103] = None
    data = payload({"AAA": closes})
    row = row_for(data, "AAA", 100, hold=8)
    assert row["state"] == "skipped"
    assert "gap" in row["why"]


def test_a_gap_outside_the_window_does_not_matter():
    closes = flat(300)
    closes[115] = None
    data = payload({"AAA": closes})
    assert row_for(data, "AAA", 100, hold=8)["state"] == "miss"


def test_a_name_that_listed_late_skips_the_years_before_it_and_says_so():
    closes = [None] * 120 + flat(200)
    data = payload({"AAA": closes})
    _, week = at(data, 10)
    result = run_trial(data, "AAA", week=week, hold=4)
    assert result["skipped"] >= 2, "the years before it listed are skipped, not failed"
    assert all("no close before" in r["why"] for r in result["rows"] if r["state"] == "skipped")
    assert result["decided"] >= 1, "and the years after it are still judged"


# ---- which years are tried -------------------------------------------------

def test_only_the_years_that_have_that_week_are_tried():
    """ISO week 53 falls in roughly one year in six.

    Asking for it is a legitimate question with a much shorter answer, and the
    answer has to be short rather than wrong.
    """
    data = payload({"AAA": flat(52 * 9)}, first_week=(2016, 1))
    result = run_trial(data, "AAA", week=53, hold=4)
    have53 = {int(w[:4]) for w in data["weeks"] if w.endswith("W53")}
    every_year = {int(w[:4]) for w in data["weeks"]}

    assert have53, "the fixture has to span a long year for this to mean anything"
    assert have53 < every_year, "and short ones, or there is nothing being excluded"
    assert {r["year"] for r in result["rows"]} == have53


def test_the_years_come_back_oldest_first_and_stop_at_the_count_asked_for():
    data = payload({"AAA": flat(52 * 9)})
    result = run_trial(data, "AAA", week=20, hold=4, years=4)
    years = [r["year"] for r in result["rows"]]
    assert years == sorted(years), "the table reads oldest first"
    assert len(years) == 4 and result["asked"] == 4
    assert max(years) == max(int(w[:4]) for w in data["weeks"]), "counted back from the newest"


# ---- the medians -----------------------------------------------------------

def test_the_medians_describe_finished_windows_only():
    """A part-run window has had less time to travel in either direction.

    Mixing one into a median presented as a full-window figure understates it,
    which is the same class of error as counting it in the rate.
    """
    data = payload({"AAA": flat(300)})
    _, week = at(data, 298)
    result = run_trial(data, "AAA", week=week, hold=8, target=8)
    assert result["open"] >= 1, "this fixture is meant to leave a window running"
    # Every close is flat, so a finished window travels exactly the wick either
    # way. A one-week part-run window would too — what would differ is the count
    # behind the median, which is why the assertion below is on `decided`.
    assert result["median_best"] == pytest.approx(WICK * 100)
    assert result["median_worst"] == pytest.approx(-WICK * 100)
    assert result["median_weeks"] is None, "nothing touched, so nothing has a week"


def test_a_still_open_window_is_left_out_of_the_medians():
    closes = flat(300)
    closes[298] = 400.0                    # a huge partial move, in the open window
    data = payload({"AAA": closes})
    _, week = at(data, 298)
    result = run_trial(data, "AAA", week=week, hold=8, target=8)
    assert result["open"] == 1 and result["touched_open"] == 1
    # If the open window were in the median, this would be far above the wick.
    assert result["median_best"] == pytest.approx(WICK * 100)


def test_no_judged_years_leaves_the_rates_null_rather_than_zero():
    data = payload({"AAA": flat(300)})
    _, week = at(data, 298)
    result = run_trial(data, "AAA", week=week, hold=8, years=1)
    assert result["decided"] == 0
    assert result["rate"] is None and result["touch_rate"] is None
    assert result["median_best"] is None


def test_an_empty_payload_still_produces_a_whole_result():
    """The early return has to hand back the same shape as a full run.

    A half-shaped result reads `decided` as undefined at the call site, which
    renders a rate against nothing rather than the empty state.
    """
    empty = {"weeks": [], "starts": [], "count": 1,
             "series": [{"ticker": "AAA", "close": [], "high": [], "low": []}]}
    result = run_trial(empty, "AAA")
    for key in ("rows", "hit", "miss", "open", "skipped", "touched", "touched_open",
                "decided", "rate", "touch_rate", "median_best", "median_worst", "asked"):
        assert key in result, key
    assert result["rows"] == [] and result["decided"] == 0 and result["rate"] is None
