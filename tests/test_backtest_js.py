"""The Backtest tab's rules — run against the JavaScript that ships.

`public/assets/backtest.js` decides when a rule fires, what it may look at when
it decides, where the trade is entered, and what closing past the target means.
That is the whole substance of the Backtest tab, and every one of those four is
a place where a backtest quietly starts flattering itself:

* a rule that reads one week past the signal is not a rule anyone could have
  traded, and it will beat the market on any history you give it;
* an entry taken before the close the signal was made on buys at a price that
  did not yet exist;
* counting five overlapping windows as five trades turns one good quarter into
  five pieces of evidence;
* and a rate with no baseline under it measures the decade, not the rule.

So these tests execute the real file under node rather than re-implementing it
in Python — a mirror would keep passing while the shipped rules drifted away
from it. `tests/test_backtest.py` is a different file about a different thing:
the Python backtest of the Setup Score that writes data/backtest.json.

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

BACKTEST_JS = Path(__file__).resolve().parents[1] / "public" / "assets" / "backtest.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node is not installed; the backtest rules are JavaScript")

# The fixture's high and low sit this far either side of each close, unless a
# test supplies its own. Deliberately well inside the targets used below, so a
# flat series touches nothing and every hit comes from a close that moved.
WICK = 0.02

DEFAULTS = {"rule": "every", "look": 4, "dir": "up", "hold": 4, "target": 8,
            "years": 0, "overlap": False}


def node(body: str) -> dict:
    script = f"""
      const bt = require({json.dumps(str(BACKTEST_JS))});
      {body}
    """
    done = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def run_rule(data: dict, ticker: str, **opts) -> dict:
    """`SpreadBacktest.run` over one name, and what it produced."""
    options = {**DEFAULTS, **opts}
    return node(f"""
      const payload = {json.dumps(data)};
      const series = payload.series.find(s => s.ticker === {json.dumps(ticker)});
      process.stdout.write(JSON.stringify(bt.run(payload, series, {json.dumps(options)})));
    """)


def run_all(data: dict, **opts) -> dict:
    options = {**DEFAULTS, **opts}
    return node(f"""
      const payload = {json.dumps(data)};
      process.stdout.write(JSON.stringify(bt.all(payload, {json.dumps(options)})));
    """)


def fired_at(data: dict, ticker: str, **opts) -> list[int]:
    """Which positions on the axis the rule fires on, asked directly."""
    options = {**DEFAULTS, **opts}
    return node(f"""
      const payload = {json.dumps(data)};
      const opt = {json.dumps(options)};
      const series = payload.series.find(s => s.ticker === {json.dumps(ticker)});
      const out = [];
      for (let i = 0; i < payload.weeks.length; i++) {{
        if (bt.fires(series, i, opt.rule, opt.look)) out.push(i);
      }}
      process.stdout.write(JSON.stringify(out));
    """)


# ---- building payloads by hand --------------------------------------------

def payload(bars: dict[str, list], first_week: tuple[int, int] = (2016, 1),
            wicks: dict[str, tuple[list, list]] | None = None) -> dict:
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
        if wicks and ticker in wicks:
            high, low = wicks[ticker]
            high = list(high) + [None] * (length - len(high))
            low = list(low) + [None] * (length - len(low))
        else:
            high = [None if c is None else round(c * (1 + WICK), 4) for c in padded]
            low = [None if c is None else round(c * (1 - WICK), 4) for c in padded]
        series.append({"ticker": ticker, "close": padded, "high": high, "low": low})
    return {"weeks": weeks, "starts": starts, "count": len(series), "series": series,
            "min_weeks": 26, "min_years": 3}


def flat(n: int, price: float = 100.0) -> list:
    return [price] * n


def states(result: dict) -> list[str]:
    return [r["state"] for r in result["rows"]]


def at_week(result: dict, index: int) -> dict:
    """The row for the signal at position ``index`` on the axis.

    Rows are the signals a run took, not one per week, so a test that means a
    particular week has to say which — ``rows[40]`` is week 40 only by accident
    and stops being so the moment the overlap rule drops a signal.
    """
    match = [r for r in result["rows"] if r["at"] == index]
    assert match, f"no signal at position {index} in {[r['at'] for r in result['rows']]}"
    return match[0]


# ---- what the rules are allowed to look at --------------------------------

def test_a_rule_never_reads_a_week_after_the_one_it_fires_on():
    """The one property a backtest cannot be wrong about.

    Two histories identical up to week 60 and different after it must produce
    the same signals up to week 60. If anything downstream of a signal reached
    its decision, this is where it shows.
    """
    rising = [100.0 + i for i in range(120)]
    crashing = rising[:61] + [50.0] * 59
    data = payload({"AAA": rising, "BBB": crashing})
    for rule in ["squeeze", "high", "low", "above", "below"]:
        a = [i for i in fired_at(data, "AAA", rule=rule, look=8) if i <= 60]
        b = [i for i in fired_at(data, "BBB", rule=rule, look=8) if i <= 60]
        assert a == b, rule


def test_a_rule_says_nothing_until_it_has_the_history_it_needs():
    # A history that fires constantly once it can, so that "nothing before the
    # warmup" is about the warmup rather than about a quiet series.
    closes = [100.0 + i for i in range(200)]
    data = payload({"AAA": closes})
    # A new-high window of 10 cannot be answered before week 10; a squeeze also
    # needs the trailing year of ranges to compare this one against.
    assert min(fired_at(data, "AAA", rule="high", look=10)) == 10
    assert all(i >= 62 for i in fired_at(data, "AAA", rule="squeeze", look=10))


def test_warmup_names_the_history_each_rule_needs():
    got = node("""
      process.stdout.write(JSON.stringify({
        every: bt.warmup("every", 10), high: bt.warmup("high", 10),
        above: bt.warmup("above", 10), squeeze: bt.warmup("squeeze", 10)
      }));
    """)
    assert got == {"every": 0, "high": 10, "above": 10, "squeeze": 62}


# ---- the rules themselves -------------------------------------------------

def test_a_new_high_needs_a_close_past_every_one_in_the_window():
    closes = flat(60)
    closes[40] = 101.0                      # the only week that is above the rest
    data = payload({"AAA": closes})
    assert fired_at(data, "AAA", rule="high", look=6) == [40]


def test_a_flat_history_makes_no_new_highs_and_no_new_lows():
    data = payload({"AAA": flat(60)})
    assert fired_at(data, "AAA", rule="high", look=6) == []
    assert fired_at(data, "AAA", rule="low", look=6) == []


def test_a_new_low_is_the_mirror_of_a_new_high():
    closes = flat(60)
    closes[40] = 99.0
    data = payload({"AAA": closes})
    assert fired_at(data, "AAA", rule="low", look=6) == [40]


def test_above_and_below_split_the_history_at_the_moving_average():
    closes = [100.0, 100.0, 100.0, 100.0, 104.0, 96.0]
    data = payload({"AAA": closes})
    # Week 4 closes 104 against a 4-week mean of 101; week 5 closes 96 against 99.
    assert fired_at(data, "AAA", rule="above", look=4) == [4]
    assert fired_at(data, "AAA", rule="below", look=4) == [5]


def test_a_squeeze_is_the_narrowest_range_of_the_trailing_year():
    # A wide history with one quiet stretch in it. The quiet weeks are flat, so
    # their four-week range is the narrowest reading on the axis.
    closes = [100.0 + (8 if i % 2 else -8) for i in range(120)]
    for i in range(80, 90):
        closes[i] = 100.0
    data = payload({"AAA": closes})
    hits = fired_at(data, "AAA", rule="squeeze", look=4)
    # Two weeks, and both are the rule working rather than misfiring: at 82 the
    # window is three quiet weeks and one noisy one, already the narrowest
    # reading of the trailing year, and at 83 it is quiet the whole way through
    # and narrower still. Weeks 84 onward are as quiet but no quieter, and a tie
    # is not a new record — see the test below.
    assert hits == [82, 83]


def test_a_squeeze_does_not_fire_on_a_tie():
    """Equal-narrowest is not narrowest.

    A perfectly flat decade has the same range every week; firing on all of
    them would turn "the quietest it has been in a year" into "every week".
    """
    data = payload({"AAA": flat(200)})
    assert fired_at(data, "AAA", rule="squeeze", look=4) == []


def test_every_week_fires_wherever_there_is_a_bar():
    closes = flat(10)
    closes[5] = None
    data = payload({"AAA": closes})
    assert fired_at(data, "AAA", rule="every") == [0, 1, 2, 3, 4, 6, 7, 8, 9]


def test_a_gap_inside_the_window_a_rule_reads_stops_it_firing():
    closes = flat(60)
    closes[40] = 101.0
    closes[37] = None                      # inside the six-week high window
    data = payload({"AAA": closes})
    assert fired_at(data, "AAA", rule="high", look=6) == []


# ---- the trade ------------------------------------------------------------

def test_the_entry_is_the_close_of_the_week_the_rule_fired_on():
    closes = flat(60)
    closes[40] = 101.0
    data = payload({"AAA": closes})
    out = run_rule(data, "AAA", rule="high", look=6, hold=4)
    assert len(out["rows"]) == 1
    assert out["rows"][0]["entry"] == 101.0
    assert out["rows"][0]["start"] == data["starts"][40]


def test_the_exit_is_hold_weeks_after_the_entry():
    closes = flat(60)
    closes[40] = 101.0
    closes[44] = 120.0                      # four weeks after the signal
    data = payload({"AAA": closes})
    row = run_rule(data, "AAA", rule="high", look=6, hold=4)["rows"][0]
    assert row["exit"] == 120.0
    assert row["exit_week"] == data["starts"][44]
    assert row["exit_pct"] == pytest.approx((120.0 / 101.0 - 1) * 100)


def test_the_signal_weeks_own_high_is_not_part_of_the_trade():
    """You bought at that close; its high already happened.

    Counting it would hand every trade an excursion it was never in for — and
    on a breakout rule, where the signal week is a new high by construction,
    it would do so systematically.
    """
    closes = flat(60)
    closes[40] = 101.0
    highs = [c * (1 + WICK) for c in closes]
    highs[40] = 400.0                       # a spike on the entry week itself
    lows = [c * (1 - WICK) for c in closes]
    data = payload({"AAA": closes}, wicks={"AAA": (highs, lows)})
    row = run_rule(data, "AAA", rule="high", look=6, hold=4, target=50)["rows"][0]
    assert row["touched"] is False
    assert row["best_pct"] < 50


def test_the_verdict_is_the_exit_and_touching_is_reported_beside_it():
    closes = flat(60)
    closes[40] = 100.0
    closes[42] = 150.0                      # way past the target, mid-window
    closes[44] = 100.0                      # and all the way back by the exit
    data = payload({"AAA": closes})
    row = at_week(run_rule(data, "AAA", rule="every", hold=4, target=8, overlap=True), 40)
    assert row["state"] == "miss"
    assert row["closed_past"] is False
    assert row["touched"] is True
    assert row["hit_in"] == 2


def test_a_trade_whose_window_has_not_run_out_is_open_not_a_miss():
    data = payload({"AAA": flat(10)})
    out = run_rule(data, "AAA", rule="every", hold=4, target=8, overlap=True)
    assert states(out)[-4:] == ["open", "open", "open", "open"]
    assert out["trades"] == 6          # only the finished ones are judged
    assert out["open"] == 4
    assert out["rate"] == 0.0          # a flat history never closes +8%


def test_a_gap_inside_the_trade_window_skips_the_trade_with_a_reason():
    closes = flat(20)
    closes[6] = None
    data = payload({"AAA": closes})
    out = run_rule(data, "AAA", rule="every", hold=4, target=8, overlap=True)
    row = at_week(out, 4)
    assert row["state"] == "skipped"
    assert "gap" in row["why"]


def test_going_down_flips_the_target_and_the_excursions():
    closes = flat(60)
    closes[44] = 80.0
    data = payload({"AAA": closes})
    row = at_week(run_rule(data, "AAA", rule="every", dir="down", hold=4, target=8,
                           overlap=True), 40)
    assert row["target"] == pytest.approx(92.0)
    assert row["closed_past"] is True
    assert row["exit_pct"] == pytest.approx(-20.0)
    assert row["best_pct"] < 0         # "best" is the furthest toward the target
    assert row["worst_pct"] > 0


def test_a_target_may_sit_at_or_behind_the_entry():
    """The in-the-money question: did it hold up, rather than did it travel."""
    closes = flat(60)
    closes[44] = 98.0                       # 2% down at the exit
    data = payload({"AAA": closes})
    row = at_week(run_rule(data, "AAA", rule="every", hold=4, target=-3, overlap=True), 40)
    assert row["target"] == pytest.approx(97.0)
    assert row["closed_past"] is True       # closed no worse than 3% down


# ---- one trade at a time --------------------------------------------------

def test_a_signal_inside_a_running_trade_is_passed_over():
    closes = [100.0 + i for i in range(30)]   # a new high every single week
    data = payload({"AAA": closes})
    out = run_rule(data, "AAA", rule="high", look=4, hold=4, overlap=False)
    assert out["fired"] > out["taken"]        # the rule spoke far more often
    opened = [r["at"] for r in out["rows"]]
    assert all(b - a >= 4 for a, b in zip(opened, opened[1:]))


def test_overlap_counts_every_firing():
    closes = [100.0 + i for i in range(30)]
    data = payload({"AAA": closes})
    out = run_rule(data, "AAA", rule="high", look=4, hold=4, overlap=True)
    assert out["taken"] == out["fired"]


def test_a_skipped_signal_does_not_block_the_next_one():
    """Nothing was opened, so there is no position to be holding.

    Blocking on it would silently drop the weeks after every hole in the
    history — a data gap quietly becoming a trading rule.
    """
    closes = flat(30)
    closes[6] = None
    data = payload({"AAA": closes})
    out = run_rule(data, "AAA", rule="every", hold=4, overlap=False)
    # Week 0 trades and holds the slot to week 4. Week 5 is refused for the gap
    # sitting in its window, and week 7 — the first whose window clears the gap
    # — trades. Had the refusal taken the slot, week 7 would still be waiting
    # on a position that was never opened.
    assert at_week(out, 5)["state"] == "skipped"
    assert at_week(out, 7)["settled"] is True


# ---- the stretch of history -----------------------------------------------

def test_years_limits_the_history_the_run_walks():
    data = payload({"AAA": flat(400)}, first_week=(2016, 1))
    whole = run_rule(data, "AAA", rule="every", hold=4, years=0)
    recent = run_rule(data, "AAA", rule="every", hold=4, years=2)
    assert recent["taken"] < whole["taken"]
    newest = int(data["weeks"][-1].split("-W")[0])
    assert all(int(r["week"].split("-W")[0]) >= newest - 1 for r in recent["rows"])


# ---- pooling and the baseline ---------------------------------------------

def test_pooling_adds_trades_not_rates():
    """One name with many trades and one with few must not weigh the same."""
    many = flat(60)
    many[44] = 200.0                        # the one window that closes up
    few = flat(60)
    data = payload({"AAA": many, "BBB": few})
    out = run_all(data, rule="every", hold=4, target=8)
    assert out["trades"] == sum(n["trades"] for n in out["names"])
    assert out["hit"] == 1
    assert out["rate"] == pytest.approx((1 / out["trades"]) * 100)


def test_the_baseline_is_every_week_with_the_same_settings():
    got = node("""
      const opt = {rule: "high", look: 9, dir: "down", hold: 6, target: 3,
                   years: 5, overlap: false};
      process.stdout.write(JSON.stringify(bt.baselineOf(opt)));
    """)
    assert got["rule"] == "every"
    assert got["overlap"] is True           # the baseline is every week, not a plan
    assert (got["dir"], got["hold"], got["target"], got["years"]) == ("down", 6, 3, 5)


def test_edge_is_the_rule_minus_the_baseline():
    got = node("""
      const rule = {rate: 62, median_exit: 3, touch_rate: 80, median_worst: -6};
      const base = {rate: 50, median_exit: 2, touch_rate: 70, median_worst: -5};
      process.stdout.write(JSON.stringify(bt.edge(rule, base)));
    """)
    assert got == {"rate": 12, "exit": 1, "touch": 10, "worst": -1}


def test_edge_is_a_dash_rather_than_a_zero_when_a_side_has_nothing_to_say():
    got = node("""
      const rule = {rate: null, median_exit: null, touch_rate: null, median_worst: null};
      const base = {rate: 50, median_exit: 2, touch_rate: 70, median_worst: -5};
      process.stdout.write(JSON.stringify(bt.edge(rule, base)));
    """)
    assert got == {"rate": None, "exit": None, "touch": None, "worst": None}


def test_a_rule_that_picks_nothing_special_has_no_edge_over_the_baseline():
    """Every week, run as a rule, must equal every week run as the baseline."""
    closes = [100.0 + (i % 13) * 2 for i in range(200)]
    data = payload({"AAA": closes})
    rule = run_rule(data, "AAA", rule="every", hold=4, target=2, overlap=True)
    base = node(f"""
      const payload = {json.dumps(data)};
      const series = payload.series[0];
      const opt = {json.dumps({**DEFAULTS, "rule": "every", "hold": 4, "target": 2})};
      process.stdout.write(JSON.stringify(bt.run(payload, series, bt.baselineOf(opt))));
    """)
    assert rule["rate"] == base["rate"]
    assert rule["trades"] == base["trades"]


# ---- the prose ------------------------------------------------------------

def test_every_rule_has_a_label_and_a_sentence():
    rules = node("process.stdout.write(JSON.stringify({keys: bt.RULE_KEYS, rules: bt.RULES}));")
    assert set(rules["keys"]) == set(rules["rules"])
    for key in rules["keys"]:
        assert rules["rules"][key]["label"]
        assert rules["rules"][key]["what"]


def test_the_tab_ships_the_rules_it_is_read_by():
    """The bullets printed under the answer, next to the code they describe.

    The Repeat test takes its equivalents from weekly.json because the backend
    owns them. Entry, exit, the verdict and the overlap rule are decisions this
    file makes, so a payload cannot be the place they are explained from.
    """
    notes = node("process.stdout.write(JSON.stringify(bt.NOTES));")
    assert len(notes) >= 6
    assert all(isinstance(n, str) and len(n) > 40 for n in notes)


def test_describe_fills_the_lookback_into_the_sentence():
    said = node("""
      process.stdout.write(JSON.stringify({
        high: bt.describe("high", 12), every: bt.describe("every", 12),
        nonsense: bt.describe("no-such-rule", 12)
      }));
    """)
    assert "12-week" in said["high"]
    assert "{n}" not in said["every"]
    assert said["nonsense"] == ""


# ---- the payload that actually ships --------------------------------------

def test_the_shipped_weekly_history_runs_through_every_rule():
    """A smoke test against the real data/weekly.json, when there is one.

    The hand-built payloads above are the specification; this is the check that
    the specification and the file the page fetches are about the same thing.
    """
    real = Path(__file__).resolve().parents[1] / "public" / "data" / "weekly.json"
    if not real.exists():
        pytest.skip("no generated weekly.json in this checkout")
    data = json.loads(real.read_text())
    if not data.get("series"):
        pytest.skip("weekly.json carries no series")
    for rule in ["every", "squeeze", "high", "low", "above", "below"]:
        out = node(f"""
          const payload = require({json.dumps(str(real))});
          const opt = {json.dumps({**DEFAULTS, "rule": rule, "look": 8, "hold": 8})};
          const all = bt.all(payload, opt);
          process.stdout.write(JSON.stringify({{
            trades: all.trades, hit: all.hit, rate: all.rate, names: all.names.length
          }}));
        """)
        assert out["names"] == len(data["series"])
        assert out["hit"] <= out["trades"]
        if out["trades"]:
            assert 0 <= out["rate"] <= 100


# ---- the shared width table -----------------------------------------------

def test_the_width_table_agrees_with_computing_each_one_alone():
    """Two paths to the same number, and the fast one is the one that ships.

    `widths` exists only so the squeeze stops rebuilding every neighbour's
    range from its own bars 53 times over. The moment it disagrees with
    `width`, it is not an optimisation, it is a second implementation.
    """
    closes = [100.0 + (i % 11) * 3 for i in range(120)]
    closes[40] = None
    data = payload({"AAA": closes})
    same = node(f"""
      const payload = {json.dumps(data)};
      const s = payload.series[0];
      for (const look of [2, 4, 13]) {{
        const table = bt.widths(s, look);
        for (let i = 0; i < payload.weeks.length; i++) {{
          const alone = bt.width(s, i, look);
          if (table[i] !== alone) {{
            process.stdout.write(JSON.stringify({{ok: false, look, i, table: table[i], alone}}));
            process.exit(0);
          }}
        }}
      }}
      process.stdout.write(JSON.stringify({{ok: true}}));
    """)
    assert same == {"ok": True}


def test_a_precomputed_width_table_does_not_change_which_weeks_fire():
    closes = [100.0 + (8 if i % 2 else -8) for i in range(120)]
    for i in range(80, 90):
        closes[i] = 100.0
    data = payload({"AAA": closes})
    same = node(f"""
      const payload = {json.dumps(data)};
      const s = payload.series[0];
      const w = bt.widths(s, 4);
      const withTable = [], without = [];
      for (let i = 0; i < payload.weeks.length; i++) {{
        if (bt.fires(s, i, "squeeze", 4, w)) withTable.push(i);
        if (bt.fires(s, i, "squeeze", 4)) without.push(i);
      }}
      process.stdout.write(JSON.stringify({{withTable, without}}));
    """)
    assert same["withTable"] == same["without"]
    assert same["withTable"]                      # and it is not vacuously equal


def test_signals_is_the_fires_loop():
    closes = [100.0 + i for i in range(120)]
    data = payload({"AAA": closes})
    same = node(f"""
      const payload = {json.dumps(data)};
      const s = payload.series[0];
      const out = {{}};
      for (const rule of ["every", "squeeze", "high", "low", "above", "below"]) {{
        const loop = [];
        for (let i = 0; i < payload.weeks.length; i++) {{
          if (bt.fires(s, i, rule, 8)) loop.push(i);
        }}
        out[rule] = {{loop, api: bt.signals(payload, s, rule, 8, 0)}};
      }}
      process.stdout.write(JSON.stringify(out));
    """)
    for rule, got in same.items():
        assert got["loop"] == got["api"], rule


def test_handing_run_its_signals_changes_nothing_about_the_answer():
    """The sweep shares one search across eight holds; it must not shift a trade."""
    closes = [100.0 + (i % 17) * 2 for i in range(200)]
    data = payload({"AAA": closes})
    same = node(f"""
      const payload = {json.dumps(data)};
      const s = payload.series[0];
      const opt = {json.dumps({**DEFAULTS, "rule": "high", "look": 6, "hold": 5})};
      const found = bt.signals(payload, s, opt.rule, opt.look, bt.from(payload, opt.years));
      process.stdout.write(JSON.stringify({{
        alone: bt.run(payload, s, opt), shared: bt.run(payload, s, opt, found)
      }}));
    """)
    assert same["alone"] == same["shared"]


# ---- the sweep ------------------------------------------------------------

def sweep(data: dict, **opts) -> dict:
    options = {**DEFAULTS, **opts}
    return node(f"""
      const payload = {json.dumps(data)};
      process.stdout.write(JSON.stringify(bt.sweep(payload, {json.dumps(options)})));
    """)


def grid_data() -> dict:
    """Two names with enough history for the sweep's axes to mean something."""
    a = [100.0 + i * 0.5 + (4 if i % 5 == 0 else 0) for i in range(400)]
    b = [80.0 + (i % 23) * 1.5 for i in range(400)]
    return payload({"AAA": a, "BBB": b})


def cell(result: dict, look: int, hold: int) -> dict:
    match = [c for c in result["cells"] if c["look"] == look and c["hold"] == hold]
    assert match, f"no cell at look={look} hold={hold}"
    return match[0]


def test_every_cell_is_the_run_it_claims_to_be():
    """The grid is a shortcut, not a different calculation.

    Each cell shares its signals along its row and its baseline down its column;
    if either sharing were wrong, the cell would stop matching the run it stands
    for — which is what this asks, on the corners and the middle.
    """
    data = grid_data()
    got = sweep(data, rule="high", look=8, hold=4, years=0)
    checks = [(c["look"], c["hold"]) for c in got["cells"]][::7]
    for look, hold in checks:
        direct = node(f"""
          const payload = {json.dumps(data)};
          const opt = {json.dumps({**DEFAULTS, "rule": "high", "years": 0})};
          opt.look = {look}; opt.hold = {hold};
          const rule = bt.all(payload, opt);
          const base = bt.all(payload, bt.baselineOf(opt));
          process.stdout.write(JSON.stringify({{
            edge: bt.edge(rule, base).rate, trades: rule.trades, base_rate: base.rate
          }}));
        """)
        here = cell(got, look, hold)
        assert here["trades"] == direct["trades"], (look, hold)
        assert here["edge"] == direct["edge"], (look, hold)
        assert here["base_rate"] == direct["base_rate"], (look, hold)


def test_the_baseline_belongs_to_the_column_not_the_grid():
    """One baseline per hold — the subtle way a grid like this lies.

    A column measured against another column's baseline would report an edge
    that is really the difference between holding four weeks and holding
    twenty-six, dressed up as the rule's doing.
    """
    got = sweep(grid_data(), rule="high", look=8, hold=4, years=0)
    by_hold = {}
    for c in got["cells"]:
        by_hold.setdefault(c["hold"], set()).add(c["base_rate"])
    for hold, rates in by_hold.items():
        assert len(rates) == 1, f"hold {hold} was measured against {len(rates)} baselines"
    # And the columns do not all share one: a longer hold is a different question.
    assert len({next(iter(r)) for r in by_hold.values()}) > 1


def test_the_setting_you_are_on_is_always_a_cell_in_the_grid():
    got = sweep(grid_data(), rule="high", look=7, hold=3, years=0)
    assert 7 in got["looks"] and 3 in got["holds"]
    here = [c for c in got["cells"] if c["here"]]
    assert len(here) == 1
    assert (here[0]["look"], here[0]["hold"]) == (7, 3)


def test_a_rule_with_no_lookback_sweeps_one_row():
    got = sweep(grid_data(), rule="every", hold=4, years=0)
    assert len(got["looks"]) == 1
    assert len(got["cells"]) == len(got["holds"])


def test_a_thin_cell_is_reported_but_never_crowned():
    # Two years of history and a rare rule: most cells finish far under the floor.
    got = sweep(grid_data(), rule="squeeze", look=8, hold=4, years=2)
    thin = [c for c in got["cells"] if c["thin"]]
    assert thin, "expected some cells under the trade floor on this short history"
    assert all(c["trades"] < got["floor"] for c in thin)
    if got["best"]:
        assert not got["best"]["thin"]
    assert got["ranked"] == len([c for c in got["cells"]
                                 if not c["thin"] and c["edge"] is not None])


def test_the_crown_is_reported_with_the_field_under_it():
    """Best, runner-up and the middle of the rankable cells, so the winner is
    visibly sitting on a distribution rather than arriving alone."""
    got = sweep(grid_data(), rule="high", look=8, hold=4, years=0)
    assert got["best"] and got["runner"]
    assert got["best"]["edge"] >= got["runner"]["edge"]
    assert got["middle"] is not None
    assert got["best"]["edge"] >= got["middle"]
    assert got["tried"] == len(got["cells"])


def test_the_axes_can_be_asked_for_without_running_the_sweep():
    """A cache that has to build the grid to find out whether it needs the grid
    is not a cache. `sweepAxes` is what the page keys on, so it has to agree
    with the grid the sweep actually produces."""
    data = grid_data()
    same = node(f"""
      const payload = {json.dumps(data)};
      const opt = {json.dumps({**DEFAULTS, "rule": "high", "look": 7, "hold": 3, "years": 0})};
      const axes = bt.sweepAxes(opt);
      const full = bt.sweep(payload, opt);
      process.stdout.write(JSON.stringify({{axes, looks: full.looks, holds: full.holds}}));
    """)
    assert same["axes"]["looks"] == same["looks"]
    assert same["axes"]["holds"] == same["holds"]


def test_the_axes_do_not_move_when_only_the_dials_do():
    """Which is what makes a cached grid reusable: moving to another cell of the
    same grid is not a different grid."""
    data = grid_data()
    same = node(f"""
      const payload = {json.dumps(data)};
      const base = {json.dumps({**DEFAULTS, "rule": "high", "years": 0})};
      const a = bt.sweepAxes(Object.assign({{}}, base, {{look: 8, hold: 4}}));
      const b = bt.sweepAxes(Object.assign({{}}, base, {{look: 26, hold: 13}}));
      const c = bt.sweepAxes(Object.assign({{}}, base, {{look: 7, hold: 4}}));
      process.stdout.write(JSON.stringify({{a, b, c}}));
    """)
    assert same["a"] == same["b"], "both pairs are already on the default grid"
    assert same["c"] != same["a"], "a lookback off the default grid adds a row"


def test_the_you_are_here_mark_follows_the_dials_not_the_grid():
    """The bug this pins: a cached grid keeps whatever cell was outlined when it
    was built, so after adopting a cell the outline sat on the old one."""
    data = grid_data()
    same = node(f"""
      const payload = {json.dumps(data)};
      const opt = {json.dumps({**DEFAULTS, "rule": "high", "look": 8, "hold": 4, "years": 0})};
      const grid = bt.sweep(payload, opt);            // built while on 8 / 4
      const first = grid.cells.filter(c => c.here).map(c => [c.look, c.hold]);
      bt.here(grid, Object.assign({{}}, opt, {{look: 26, hold: 13}}));   // dials moved
      const second = grid.cells.filter(c => c.here).map(c => [c.look, c.hold]);
      process.stdout.write(JSON.stringify({{first, second}}));
    """)
    assert same["first"] == [[8, 4]]
    assert same["second"] == [[26, 13]], "the outline stayed on the cell the grid was built at"
