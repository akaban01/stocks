/* The Backtest tab's rules — "if I had always bought on this signal, what
 * happened next?"
 *
 * The Repeat test asks about one name on one week of the calendar. This asks
 * the other question: take a *rule* — a squeeze, a new high, a close under the
 * average — run it over every name and every week of the history, and count
 * what the next N weeks did. One rule, one holding period, one target, every
 * occurrence it ever had.
 *
 * Three things decide whether an answer like that means anything, and all three
 * are in here rather than in the page:
 *
 *   1. **No look-ahead.** A signal at week i is computed from weeks at or before
 *      i and nothing else, and the trade it opens is entered at that week's own
 *      close — the first price you could actually have paid once the rule had
 *      fired. The window that judges it starts the week after.
 *   2. **The verdict is the exit**, exactly as on the Repeat test: where the
 *      trade closed, not the best price it saw. `touched` is reported beside it
 *      and never as it.
 *   3. **A rate on its own says nothing.** These names rose over the window the
 *      history covers, so almost any rule shows a positive record. What the tab
 *      reports is the rule against the *baseline* — the same names, the same
 *      hold, the same target, entered on every week there was — and the gap
 *      between them. `edge` below is that subtraction, and it is the number the
 *      page leads with.
 *
 * It is a separate file so it can be tested: `tests/test_backtest_js.py` runs
 * this exact source under node against hand-built payloads. Keep it free of the
 * DOM for that reason. (`tests/test_backtest.py` is a different thing — the
 * Python backtest of the Setup Score that writes data/backtest.json.)
 *
 * The window arithmetic is positional: from a signal at i, week i + hold is the
 * exit. That is only sound because weekly.json puts every name on one shared,
 * gapless ISO-week axis — see spread_scanner/weekly.py.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpreadBacktest = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* Deliberately its own copy rather than a call into trial.js: this file is
     required on its own under node by the tests, and a load-order dependency
     between two browser globals is a worse trade than four lines. */
  function median(values) {
    var v = values.filter(function (x) { return x !== null && x !== undefined && !isNaN(x); });
    if (!v.length) return null;
    v.sort(function (a, b) { return a - b; });
    var m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  }

  /* ---- the rules ---------------------------------------------------------

     Each one is a question about the *last `look` weeks* and nothing after
     them. `label` is what the control says, `what` is the sentence the page
     prints under the answer, and `look` names what the lookback control means
     for that rule — the same number means different things to a squeeze and to
     a moving average, and a control labelled "Lookback" over all of them would
     be a control that explains nothing.

     `every` is the baseline in rule form. It is selectable because a reader who
     wants to know what these names simply *did* over ten years should be able
     to ask that directly, and because it makes the comparison the tab is built
     on inspectable rather than internal. */
  var RULES = {
    every:    { label: "Every week (no rule)", look: null,
                what: "Every week in the history is an entry. This is the baseline the other "
                    + "rules are measured against, not a strategy." },
    squeeze:  { label: "Squeeze — narrowest range in a year", look: "range window",
                what: "The high-to-low range of the last {n} weeks, as a share of price, is the "
                    + "narrowest it has been in the trailing year. The coiled spring the scanner "
                    + "looks for, in weekly form." },
    high:     { label: "Breakout — a new high", look: "high window",
                what: "The close is above every close of the previous {n} weeks — a {n}-week "
                    + "high, made on this week's close." },
    low:      { label: "Breakdown — a new low", look: "low window",
                what: "The close is below every close of the previous {n} weeks — a {n}-week "
                    + "low, made on this week's close." },
    above:    { label: "Above the average", look: "average window",
                what: "The close is above the mean of the last {n} weekly closes, itself "
                    + "included. A trend filter, and the one rule here that fires most of the "
                    + "time." },
    below:    { label: "Below the average", look: "average window",
                what: "The close is below the mean of the last {n} weekly closes, itself "
                    + "included — the dip, for anyone who buys them." }
  };

  var RULE_KEYS = ["every", "squeeze", "high", "low", "above", "below"];

  /* What this tab does, in the order the page prints it.

     The Repeat test takes its equivalent bullets from weekly.json, because the
     backend owns them: whole ISO weeks, the week in progress dropped, one
     gapless axis. These are not those. Entry, exit, the verdict and the overlap
     rule are decisions *this file* makes and the tests below it pin, so the
     sentences describing them live next to the code rather than in a payload
     that knows nothing about them — the same reason the glossary ships inside
     scan.json and not here. The one fact about the data itself, `prices`, is
     still read off weekly.json where it belongs. */
  var NOTES = [
    "The entry is the close of the week the rule fired on — the first price "
      + "available once the signal existed. Nothing here is bought at a price "
      + "that had not printed yet.",
    "A hold of N weeks means the N weeks after that entry. The trade is "
      + "measured from the entry close to the close of week N, and the signal "
      + "week's own high and low are history you bought after, not an "
      + "excursion you sat through.",
    "A trade counts when it *closes* past the target: the close of the last "
      + "week in the window, at or beyond the entry moved by your percentage. "
      + "Touched is the looser test — the weekly high, or the low going down, "
      + "at any point inside the window — and is reported beside the verdict "
      + "rather than deciding it.",
    "A trade whose window runs past the last complete week has no exit yet, so "
      + "it is reported as still open and counted in neither column. A gap in "
      + "the history inside the window is skipped and said to be skipped.",
    "One trade at a time, unless you turn it off: a signal that fires while a "
      + "trade is already running is passed over, because one position is what "
      + "you could have held — and because five overlapping windows over one "
      + "good quarter are not five pieces of evidence.",
    "The baseline is the same names, direction, hold and target entered on "
      + "every week there was, overlapping windows and all. It is what the "
      + "history did in general, so the gap between the rule and it is the only "
      + "part of the rate that is about the rule."
  ];

  // How far back each rule has to be able to see before it can answer at all.
  // A squeeze compares this window against a year of them, so it needs both.
  function warmup(rule, look) {
    if (rule === "every") return 0;
    if (rule === "squeeze") return look + 52;
    return look;
  }

  function ok(series, i) {
    return series.close[i] !== null && series.close[i] !== undefined &&
           series.high[i] !== null && series.high[i] !== undefined &&
           series.low[i] !== null && series.low[i] !== undefined;
  }

  /* Does the rule fire on week `i`? Reads `i` and the weeks before it, never
     one after — the whole honesty of a backtest is in that sentence.

     A hole anywhere in the window the rule needs is a refusal, not a shrug: a
     squeeze computed across a gap is a squeeze measured over more calendar time
     than it claims, and the one thing worse than no signal is a wrong one.

     `w` is an optional array of precomputed widths for this series and this
     lookback — see `widths` below. It changes nothing about the answer; it is
     how the squeeze stops being quadratic when a caller asks the same question
     of every week. Left out, each width is computed on the spot. */
  function fires(series, i, rule, look, w) {
    if (!ok(series, i)) return false;
    if (rule === "every") return true;

    var need = warmup(rule, look);
    if (i < need) return false;
    for (var j = i - need; j <= i; j++) if (!ok(series, j)) return false;

    if (rule === "high" || rule === "low") {
      // Strictly past the previous `look` closes, the current week excluded
      // from its own comparison — otherwise a flat series makes a "new high"
      // every week and the rule fires on nothing happening.
      var edge = null;
      for (var k = i - look; k < i; k++) {
        var c = series.close[k];
        if (edge === null || (rule === "high" ? c > edge : c < edge)) edge = c;
      }
      return rule === "high" ? series.close[i] > edge : series.close[i] < edge;
    }

    if (rule === "above" || rule === "below") {
      var sum = 0;
      for (var m = i - look + 1; m <= i; m++) sum += series.close[m];
      var mean = sum / look;
      return rule === "above" ? series.close[i] > mean : series.close[i] < mean;
    }

    // Squeeze: this week's range, against every reading of the same measure over
    // the trailing year. Scaled by the close, because a $4 range on a $40 stock
    // and on a $400 one are not the same range.
    var here = w ? w[i] : width(series, i, look);
    if (here === null || here === undefined) return false;
    for (var y = i - 51; y < i; y++) {
      var was = w ? w[y] : width(series, y, look);
      if (was !== null && was !== undefined && was <= here) return false;
    }
    return true;
  }

  /* Every week's `width`, for one series and one lookback, in one pass.

     The squeeze compares this week's range against all fifty-two before it, and
     each of those was being rebuilt from its own `look` bars every time it was
     asked for — so one week's answer cost 52 × look bar reads, and a whole
     history rebuilt each width fifty-three times over. Built once here, each
     costs `look` reads and every comparison after that is an array lookup. The
     window is still walked rather than rolled: a rolling extreme has to drop
     values as it slides, and a hole in the history invalidates the window
     outright, which is fiddlier than it is worth for the factor it saves.

     The numbers are identical either way, and the tests run both paths against
     each other to keep it that way. */
  function widths(series, look) {
    var out = new Array(series.close.length);
    for (var i = 0; i < out.length; i++) out[i] = width(series, i, look);
    return out;
  }

  /* Every week the rule fires on, for one name, in one pass.

     Signals depend on the rule, its lookback and the history — and on nothing
     else. The hold, the target, the direction and the overlap rule all belong
     to what happens *after* a signal, so they cannot change where the signals
     are. Separating the two is what lets the sweep below ask about eight
     holding periods without finding the same signals eight times, and it is
     also the honest shape of the thing: the rule decides when you would have
     bought, and everything else decides what that was worth. */
  function signals(payload, series, rule, look, start) {
    var out = [];
    var last = (payload.weeks || []).length - 1;
    if (last < 0 || !series) return out;
    var w = rule === "squeeze" ? widths(series, look) : null;
    for (var i = Math.max(0, start || 0); i <= last; i++) {
      if (fires(series, i, rule, look, w)) out.push(i);
    }
    return out;
  }

  // The high-to-low range of the `look` weeks ending at `i`, as a share of the
  // close. Null where the window runs off the start of the history.
  function width(series, i, look) {
    if (i - look + 1 < 0) return null;
    var hi = null, lo = null;
    for (var j = i - look + 1; j <= i; j++) {
      if (!ok(series, j)) return null;
      if (hi === null || series.high[j] > hi) hi = series.high[j];
      if (lo === null || series.low[j] < lo) lo = series.low[j];
    }
    return series.close[i] ? (hi - lo) / series.close[i] : null;
  }

  /* One trade, from a signal at week `i`.

     Entry is the close of the signal week — the first price available once the
     rule has fired. The window that judges it is therefore the `hold` weeks
     *after* that close: week i's own high and low are history you bought after,
     not an excursion you sat through, and counting them would hand the trade a
     high it was never in for. */
  function trade(payload, series, opt, i, last) {
    var up = opt.dir !== "down";
    var row = { week: payload.weeks[i], start: payload.starts[i], at: i, state: "skipped" };
    row.entry = series.close[i];
    row.target = row.entry * (up ? 1 + opt.target / 100 : 1 - opt.target / 100);

    var end = i + opt.hold, stop = Math.min(end, last);
    var fav = null, adv = null, hitAt = -1;
    for (var k = i + 1; k <= stop; k++) {
      if (!ok(series, k)) {
        // A hole inside the window. Refused rather than closed early: skipping
        // the missing weeks would make the trade span more calendar time than
        // the hold it claims.
        row.why = "the history has a gap inside that window";
        return row;
      }
      var f = up ? series.high[k] : series.low[k];       // the extreme toward the target
      var a = up ? series.low[k] : series.high[k];       // and the one against it
      if (fav === null || (up ? f > fav : f < fav)) fav = f;
      if (adv === null || (up ? a < adv : a > adv)) adv = a;
      if (hitAt < 0 && (up ? f >= row.target : f <= row.target)) hitAt = k;
    }

    row.best_pct = fav === null ? null : (fav / row.entry - 1) * 100;
    row.worst_pct = adv === null ? null : (adv / row.entry - 1) * 100;
    row.touched = hitAt >= 0;
    if (row.touched) { row.hit_in = hitAt - i; row.hit_week = payload.starts[hitAt]; }

    if (end > last) {
      // The hold has not run out, so the trade has no exit — and the exit is
      // the verdict. Admitting it would let the newest, unfinished trades hold
      // the headline up in one direction only.
      row.state = "open";
      row.ran = stop - i;
      row.open_pct = (series.close[stop] / row.entry - 1) * 100;
      return row;
    }
    row.settled = true;
    row.exit = series.close[end];
    row.exit_week = payload.starts[end];
    row.exit_pct = (row.exit / row.entry - 1) * 100;
    row.closed_past = up ? row.exit >= row.target : row.exit <= row.target;
    row.state = row.closed_past ? "hit" : "miss";
    return row;
  }

  /* The first position on the axis inside the asked-for stretch of history.

     Counted in ISO years off the newest week rather than in weeks, because
     "the last five years" is what the control says and an ISO year is 52 weeks
     or 53. Years wider than the history simply start at the beginning. */
  function from(payload, years) {
    if (!years || years <= 0) return 0;
    var weeks = payload.weeks || [];
    if (!weeks.length) return 0;
    var newest = parseInt(weeks[weeks.length - 1].slice(0, 4), 10);
    var floor = newest - years + 1;
    for (var i = 0; i < weeks.length; i++) {
      if (parseInt(weeks[i].slice(0, 4), 10) >= floor) return i;
    }
    return weeks.length;
  }

  /* Every trade the rule would have opened on one name.

     `opt.overlap` is the difference between a strategy and a survey. Left off
     — the default — a signal that fires while a trade is already running is
     passed over, because one position at a time is what you could actually
     have held, and it is also what stops a single good quarter being counted
     five times over by five overlapping windows. Turned on, every firing is
     counted, which is the right reading for the baseline and the wrong one for
     anything you plan to trade. Both are reported: `fired` is how often the
     rule spoke, `rows` is how many of those became trades. */
  function run(payload, series, opt, found) {
    var out = { rows: [], fired: 0, taken: 0, hit: 0, miss: 0, open: 0, skipped: 0,
                touched: 0, trades: 0, rate: null, touch_rate: null, median_exit: null,
                median_best: null, median_worst: null, median_weeks: null,
                ticker: (series || {}).ticker || null, first: null, last: null };
    var last = (payload.weeks || []).length - 1;
    if (last < 0 || !series) return out;

    var look = Math.max(1, Math.round(opt.look || 1));
    var start = from(payload, opt.years);
    var busyUntil = -1;

    // `found` is the same name's signals, already located — the sweep hands
    // them in so that eight holding periods share one search. Absent, they are
    // found here. Either way they are the signals for this rule and lookback
    // over this stretch of history, and the hold cannot have moved them.
    var at = found || signals(payload, series, opt.rule, look, start);

    for (var n = 0; n < at.length; n++) {
      var i = at[n];
      out.fired++;
      // One trade at a time, unless the caller asked for all of them.
      if (!opt.overlap && i <= busyUntil) continue;
      var row = trade(payload, series, opt, i, last);
      out.rows.push(row);
      out[row.state]++;
      if (row.settled && row.touched) out.touched++;
      // A skipped signal never opened a position, so it cannot block the next
      // one — only a trade that actually ran holds the slot.
      if (row.state !== "skipped") busyUntil = i + opt.hold;
    }

    out.taken = out.rows.length;
    var settled = out.rows.filter(function (r) { return r.settled; });
    out.trades = settled.length;
    if (settled.length) {
      out.first = settled[0].start;
      out.last = settled[settled.length - 1].start;
    }
    out.rate = out.trades ? (out.hit / out.trades) * 100 : null;
    out.touch_rate = out.trades ? (out.touched / out.trades) * 100 : null;
    // Medians over finished trades only. A part-run window has had less time to
    // travel in either direction, so mixing one in understates both figures
    // while being presented as a full-window number.
    out.median_exit = median(settled.map(function (r) { return r.exit_pct; }));
    out.median_best = median(settled.map(function (r) { return r.best_pct; }));
    out.median_worst = median(settled.map(function (r) { return r.worst_pct; }));
    out.median_weeks = median(settled.filter(function (r) { return r.touched; })
                                     .map(function (r) { return r.hit_in; }));
    return out;
  }

  /* The same rule across every name, plus the pooled counts.

     Pooling is a count of trades, not an average of rates: a name with three
     trades puts three into the denominator rather than a 100% record into a
     mean. What pooling does not fix is that these names move together — said on
     the page, under the number, every time it is printed. */
  function all(payload, opt, found) {
    var out = { names: [], rows: [], fired: 0, taken: 0, trades: 0, hit: 0, touched: 0,
                open: 0, skipped: 0, rate: null, touch_rate: null, median_exit: null,
                median_best: null, median_worst: null };
    var series = (payload || {}).series || [];
    var exits = [], bests = [], worsts = [];
    for (var i = 0; i < series.length; i++) {
      // `found` is one entry per series, in the same order — the sweep's shared
      // signal search. Absent, each name finds its own.
      var one = run(payload, series[i], opt, found ? found[i] : null);
      out.names.push(one);
      out.fired += one.fired;
      out.taken += one.taken;
      out.trades += one.trades;
      out.hit += one.hit;
      out.touched += one.touched;
      out.open += one.open;
      out.skipped += one.skipped;
      for (var j = 0; j < one.rows.length; j++) {
        var r = one.rows[j];
        out.rows.push({ ticker: series[i].ticker, row: r });
        if (!r.settled) continue;
        exits.push(r.exit_pct);
        bests.push(r.best_pct);
        worsts.push(r.worst_pct);
      }
    }
    out.rate = out.trades ? (out.hit / out.trades) * 100 : null;
    out.touch_rate = out.trades ? (out.touched / out.trades) * 100 : null;
    // Pooled over every trade, not over the per-name medians: a median of
    // medians is not a median, which is why the per-name table dashes its own
    // total rather than adding one up.
    out.median_exit = median(exits);
    out.median_best = median(bests);
    out.median_worst = median(worsts);
    return out;
  }

  /* ---- the sweep --------------------------------------------------------

     One setting is a number; the grid around it is evidence.

     The Repeat test has the same problem and answers it the same way: asking
     someone to pick one week out of fifty-three, and then painting all
     fifty-three so they can see whether the one they picked sits on a green
     ridge or is the single good week in a red field. Here the two dials are the
     lookback and the hold, so the sweep is a grid rather than a strip, and each
     cell is that pair's edge over its own baseline.

     Two things make it cheap enough to do at all, and both are facts about what
     depends on what:

       * signals depend on the rule and its lookback, never on the hold — so one
         search per lookback serves the whole row;
       * the baseline depends on the hold, the direction, the target and the
         stretch of history, never on the lookback — so one baseline per hold
         serves the whole column.

     Getting the second one wrong is the subtle way a grid like this lies: a
     column measured against another column's baseline would show an edge that
     is really just the difference between holding four weeks and holding
     twenty-six. */

  // A week to a year for the hold; a month to a year for the lookback. Both are
  // in weeks, and both are coarse on purpose — a grid fine enough to hide the
  // shape of the thing is a grid that only finds noise.
  var SWEEP_HOLDS = [1, 2, 4, 8, 13, 26, 39, 52];
  var SWEEP_LOOKS = [4, 8, 13, 26, 39, 52];

  // Below this many finished trades a cell is reported but never crowned. Three
  // trades at 100% is not the best setting on the grid, for the same reason two
  // judged years is not the best week on the Repeat test.
  var SWEEP_FLOOR = 30;

  function axis(values, current) {
    var out = values.slice();
    // The cell you are on is always in the grid, so the sweep is about your
    // setting rather than about a menu that happens not to contain it.
    if (current && out.indexOf(current) === -1) out.push(current);
    out.sort(function (a, b) { return a - b; });
    return out;
  }

  /* Which rows and columns a sweep of these settings would have.

     Exported because a caller that caches a grid has to know whether the grid
     it holds is still the right *shape* before deciding to recompute it — and
     asking by running the sweep is not asking, it is doing the work. The two
     dials the grid sweeps are deliberately absent from what it returns beyond
     making sure the current pair is on it. */
  function sweepAxes(opt, axes) {
    return {
      holds: axis((axes && axes.holds) || SWEEP_HOLDS, Math.round(opt.hold)),
      looks: RULES[opt.rule] && RULES[opt.rule].look
        ? axis((axes && axes.looks) || SWEEP_LOOKS, Math.round(opt.look))
        : [Math.round(opt.look) || 1]     // a rule with no lookback has one row
    };
  }

  /* Re-mark which cell the controls are sitting on.

     Separate from building the grid because a cached grid outlives the dials:
     move from an 8-week hold to a 13-week one and the grid is unchanged — every
     cell of it was already computed — but the cell you are *on* is not, and an
     outline left on the old one is a grid quietly pointing at the wrong answer. */
  function here(sw, opt) {
    for (var i = 0; i < sw.cells.length; i++) {
      sw.cells[i].here = sw.cells[i].look === Math.round(opt.look) &&
                         sw.cells[i].hold === Math.round(opt.hold);
    }
    return sw;
  }

  function sweep(payload, opt, axes) {
    var resolved = sweepAxes(opt, axes);
    var holds = resolved.holds, looks = resolved.looks;
    var series = (payload || {}).series || [];
    var start = from(payload, opt.years);

    // One baseline per hold — never per cell, and never shared across holds.
    var base = {};
    for (var h = 0; h < holds.length; h++) {
      base[holds[h]] = all(payload, baselineOf({ dir: opt.dir, hold: holds[h],
                                                 target: opt.target, years: opt.years }));
    }

    var cells = [];
    for (var l = 0; l < looks.length; l++) {
      // One signal search per lookback, shared by every hold in the row.
      var found = [];
      for (var n = 0; n < series.length; n++) {
        found.push(signals(payload, series[n], opt.rule, looks[l], start));
      }
      for (var k = 0; k < holds.length; k++) {
        var cell = all(payload, { rule: opt.rule, look: looks[l], dir: opt.dir,
                                  hold: holds[k], target: opt.target, years: opt.years,
                                  overlap: opt.overlap }, found);
        var gap = edge(cell, base[holds[k]]);
        cells.push({ look: looks[l], hold: holds[k], trades: cell.trades,
                     rate: cell.rate, base_rate: base[holds[k]].rate, edge: gap.rate,
                     exit: cell.median_exit, worst: cell.median_worst,
                     thin: cell.trades < SWEEP_FLOOR, here: false });
      }
    }

    // The crown, and the distribution it is sitting on. A cell twenty points
    // clear of the field and a cell two points clear are the same crown and very
    // different evidence, so the runner-up and the middle are reported beside it
    // — the Repeat test's week strip says the same thing about its own winner.
    var ranked = cells.filter(function (c) { return c.edge !== null && !c.thin; })
                      .sort(function (a, b) { return b.edge - a.edge; });
    here({ cells: cells }, opt);
    return { cells: cells, holds: holds, looks: looks, floor: SWEEP_FLOOR,
             ranked: ranked.length,
             best: ranked[0] || null, runner: ranked[1] || null,
             middle: median(ranked.map(function (c) { return c.edge; })),
             tried: cells.length };
  }

  /* The settings that make the baseline: the same names, direction, hold,
     target and stretch of history, entered on every week there was.

     Overlap is forced on for it, and that is the point of the comparison rather
     than a detail of it. The baseline is a description of what the history did
     over `hold` weeks — all of it, every starting week — and a rule beats it
     only by picking weeks that were better than the weeks in general. Anything
     else is measuring the market and calling it a strategy. */
  function baselineOf(opt) {
    return { rule: "every", look: 1, dir: opt.dir, hold: opt.hold, target: opt.target,
             years: opt.years, overlap: true };
  }

  /* Rule minus baseline, in points. Null where either side has nothing to say,
     so the page prints a dash instead of a confident zero. */
  function edge(result, base) {
    function gap(a, b) {
      return (a === null || a === undefined || b === null || b === undefined) ? null : a - b;
    }
    return { rate: gap(result.rate, base.rate),
             exit: gap(result.median_exit, base.median_exit),
             touch: gap(result.touch_rate, base.touch_rate),
             worst: gap(result.median_worst, base.median_worst) };
  }

  // The rule's sentence with its lookback filled in, so the page never has to
  // hold a second copy of the prose that describes what it just ran.
  function describe(rule, look) {
    var r = RULES[rule];
    if (!r) return "";
    return r.what.replace(/\{n\}/g, String(look));
  }

  return { RULES: RULES, RULE_KEYS: RULE_KEYS, NOTES: NOTES, median: median, width: width,
           widths: widths, fires: fires, signals: signals, warmup: warmup, from: from,
           trade: trade, run: run, all: all, sweep: sweep, sweepAxes: sweepAxes, here: here,
           baselineOf: baselineOf,
           edge: edge, describe: describe,
           SWEEP_HOLDS: SWEEP_HOLDS, SWEEP_LOOKS: SWEEP_LOOKS, SWEEP_FLOOR: SWEEP_FLOOR };
});
