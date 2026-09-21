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
     than it claims, and the one thing worse than no signal is a wrong one. */
  function fires(series, i, rule, look) {
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
    var here = width(series, i, look);
    if (here === null) return false;
    for (var y = i - 51; y < i; y++) {
      var was = width(series, y, look);
      if (was !== null && was <= here) return false;
    }
    return true;
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
  function run(payload, series, opt) {
    var out = { rows: [], fired: 0, taken: 0, hit: 0, miss: 0, open: 0, skipped: 0,
                touched: 0, trades: 0, rate: null, touch_rate: null, median_exit: null,
                median_best: null, median_worst: null, median_weeks: null,
                ticker: (series || {}).ticker || null, first: null, last: null };
    var last = (payload.weeks || []).length - 1;
    if (last < 0 || !series) return out;

    var look = Math.max(1, Math.round(opt.look || 1));
    var start = from(payload, opt.years);
    var busyUntil = -1;

    for (var i = start; i <= last; i++) {
      if (!fires(series, i, opt.rule, look)) continue;
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
  function all(payload, opt) {
    var out = { names: [], rows: [], fired: 0, taken: 0, trades: 0, hit: 0, touched: 0,
                open: 0, skipped: 0, rate: null, touch_rate: null, median_exit: null,
                median_best: null, median_worst: null };
    var series = (payload || {}).series || [];
    var exits = [], bests = [], worsts = [];
    for (var i = 0; i < series.length; i++) {
      var one = run(payload, series[i], opt);
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

  return { RULES: RULES, RULE_KEYS: RULE_KEYS, NOTES: NOTES, median: median, width: width, fires: fires,
           warmup: warmup, from: from, trade: trade, run: run, all: all,
           baselineOf: baselineOf, edge: edge, describe: describe };
});
