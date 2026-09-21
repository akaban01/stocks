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

  function has(v) { return v !== null && v !== undefined && v !== ""; }

  /* A move, as the *position* felt it — not as the price printed it.
     Going down, the trade gains when the price falls, so the sign turns over
     with the direction: a short that watched its name rise 18% is −18%, and a
     short that watched it fall 13% is +13%. Without this a bearish row reads
     as its own opposite, and the search below would crown a short on a name
     that tripled and call the loss an edge.
     `fav` and `adv` already pick the right extreme for the direction, so the
     one flip serves the exit, the best and the worst alike. */
  function ret(pct, up) { return pct === null || pct === undefined ? null : (up ? pct : -pct); }

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
     bought, and everything else decides what that was worth.

     `opt.also` is a second rule, with its own lookback in `opt.alsoLook`, and
     the week has to satisfy both. It is an AND and nothing cleverer: "the
     squeeze, but only while the name is above its 20-week average" is the
     question people actually ask next, and it is one line here because the two
     rules are already independent of everything downstream of them. Both
     windows have to be answerable before either fires, so the warmup is the
     longer of the two — a filter that cannot see far enough back yet does not
     get to pass a week by default. */
  function signals(payload, series, opt, start, until) {
    var out = [];
    var last = (payload.weeks || []).length - 1;
    if (last < 0 || !series) return out;
    // `until` bounds where a signal may *open*, not where its trade may run.
    // A search that holds out the last few years needs that distinction: a
    // trade opened inside the training window and still running when the window
    // ends was a trade you were holding, and truncating it would quietly bias
    // the search toward shorter holds.
    var stopAt = (until === undefined || until === null) ? last : Math.min(last, until);

    var look = Math.max(1, Math.round(opt.look || 1));
    // "every" as the second rule is no filter at all, which is what the control
    // reads as "— nothing" and what an absent field has always meant.
    var also = opt.also && opt.also !== "every" && RULES[opt.also] ? opt.also : null;
    var alsoLook = Math.max(1, Math.round(opt.alsoLook || look));

    var w = opt.rule === "squeeze" ? widths(series, look) : null;
    var w2 = also === "squeeze" ? widths(series, alsoLook) : null;

    for (var i = Math.max(0, start || 0); i <= stopAt; i++) {
      if (!fires(series, i, opt.rule, look, w)) continue;
      if (also && !fires(series, i, also, alsoLook, w2)) continue;
      out.push(i);
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

    row.best_pct = fav === null ? null : ret((fav / row.entry - 1) * 100, up);
    row.worst_pct = adv === null ? null : ret((adv / row.entry - 1) * 100, up);
    row.touched = hitAt >= 0;
    if (row.touched) { row.hit_in = hitAt - i; row.hit_week = payload.starts[hitAt]; }

    if (end > last) {
      // The hold has not run out, so the trade has no exit — and the exit is
      // the verdict. Admitting it would let the newest, unfinished trades hold
      // the headline up in one direction only.
      row.state = "open";
      row.ran = stop - i;
      row.open_pct = ret((series.close[stop] / row.entry - 1) * 100, up);
      return row;
    }
    row.settled = true;
    row.exit = series.close[end];
    row.exit_week = payload.starts[end];
    row.exit_pct = ret((row.exit / row.entry - 1) * 100, up);
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
    // `opt.from` / `opt.until` bound the stretch a signal may open in, and
    // override the years control when present — that is how the search below
    // asks the same question of two halves of one history.
    var start = has(opt.from) ? opt.from : from(payload, opt.years);
    var busyUntil = -1;

    // `found` is the same name's signals, already located — the sweep hands
    // them in so that eight holding periods share one search. Absent, they are
    // found here. Either way they are the signals for this rule and lookback
    // over this stretch of history, and the hold cannot have moved them.
    var at = found || signals(payload, series, opt, start, opt.until);

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
        // The grid sweeps the *primary* rule's lookback. A second rule keeps
        // the lookback you set for it, in every cell — said on the page,
        // because a grid that quietly swept both axes of a two-rule signal
        // would be a different question in every row.
        found.push(signals(payload, series[n], { rule: opt.rule, look: looks[l],
                                                 also: opt.also, alsoLook: opt.alsoLook },
                           start));
      }
      for (var k = 0; k < holds.length; k++) {
        var cell = all(payload, { rule: opt.rule, look: looks[l], also: opt.also,
                                  alsoLook: opt.alsoLook, dir: opt.dir, hold: holds[k],
                                  target: opt.target, years: opt.years,
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

  /* ---- the best strategy for one name ------------------------------------

     Search every rule at every lookback, hold and direction, and crown the one
     that did best. That is a data-mining exercise, and left there it would be
     the most dishonest thing on this page: three hundred tries against ten
     years of one stock will always turn up a winner, and the winner will
     usually be noise wearing a rule's name.

     So it is not left there. The history is cut in two. Every combination is
     searched on the **first** part, the winner is chosen there, and the number
     this reports is what that same setting went on to do on the **rest** — a
     stretch the search never saw. `calibrate.py` fits the Setup Score's weights
     the same way, on a train split, for the same reason.

     What "best" means here is the thing to be careful about. A rule can touch
     its target far more often than average and still lose money on every
     trade, so ranking on the hit rate crowns strategies nobody would take —
     on this data it crowned a *short* on a name that rose eightfold, because
     that name dipped 2% more reliably than a blind short did. So the ranking
     is the **return**: how many points the median trade beat the median
     trade of the same name's baseline by. The hit-rate gap is still carried
     on every cell as `edge`, because it is worth seeing; it just does not get
     to choose. Every percentage is signed as the position felt it, so a short
     that made money is positive (see `ret` at the top of this file).

     Three numbers come out, and the order matters:

       * `train` — the best edge the search found. This is the number a tool
         without a holdout would print, and it means almost nothing.
       * `test` — what that same setting did afterwards. This is the finding.
       * `hindsight` — the best edge available on the held-out stretch, which is
         what you would have picked if you could see it. The gap between it and
         `test` is the part of the answer the search did not capture, and it is
         usually most of it.

     A search that works has `test` near `hindsight` and well above zero. A
     search that is fitting noise has a large `train`, a `test` around nothing,
     and the page says so rather than printing the crown alone. */

  // Where the history is cut. 0.7 is `calibrate.py`'s default train fraction,
  // and the same reasoning applies: enough behind to find something, enough
  // ahead to find out whether it was there.
  var TRAIN_FRAC = 0.7;

  // A combination needs this many finished trades on a slice before it can be
  // crowned on it. Three trades at a huge edge is the search finding one good
  // quarter, not a strategy.
  var SEARCH_FLOOR = 10;

  var SEARCH_RULES = ["squeeze", "high", "low", "above", "below"];
  var SEARCH_LOOKS = [4, 8, 13, 26, 39, 52];
  var SEARCH_HOLDS = [2, 4, 8, 13, 26];
  var SEARCH_DIRS = ["up", "down"];

  // The position the history is cut at: signals before it are the search's,
  // signals at or after it are the holdout's. A trade opened before the cut may
  // still be running after it — that is what holding across a boundary is.
  function cutAt(payload, frac) {
    var n = (payload.weeks || []).length;
    return Math.max(0, Math.min(n - 1, Math.round(n * (frac || TRAIN_FRAC))));
  }

  /* Every combination, on both slices, for one name.

     The two economies the sweep uses apply again: signals depend on the rule
     and its lookback, so one search per pair serves every hold and direction
     below it; and the baseline depends on the direction, hold and slice, never
     on the rule, so one per triple serves every combination measured against
     it. Here the baseline is *this name's* own — a strategy for NVDA is worth
     what it beat on NVDA, not on the screen as a whole. */
  function search(payload, series, opt, axes) {
    var rules = (axes && axes.rules) || SEARCH_RULES;
    var looks = (axes && axes.looks) || SEARCH_LOOKS;
    var holds = (axes && axes.holds) || SEARCH_HOLDS;
    var dirs = (axes && axes.dirs) || SEARCH_DIRS;
    var target = opt.target, overlap = opt.overlap;
    var cut = cutAt(payload, (axes && axes.frac) || TRAIN_FRAC);
    var last = (payload.weeks || []).length - 1;

    var slices = {
      train: { from: 0, until: cut - 1 },
      test:  { from: cut, until: last }
    };

    // One baseline per direction, hold and slice — never per combination. This
    // is the tab's usual yardstick, and it answers "did the rule pick the
    // weeks": a long against every long, a short against every short.
    var base = {};
    for (var d = 0; d < dirs.length; d++) {
      for (var h = 0; h < holds.length; h++) {
        for (var k in slices) {
          base[dirs[d] + "|" + holds[h] + "|" + k] = run(payload, series, {
            rule: "every", look: 1, dir: dirs[d], hold: holds[h], target: target,
            overlap: true, from: slices[k].from, until: slices[k].until
          });
        }
      }
    }

    /* And one common yardstick, the same for every direction: simply being
       long the name for the same number of weeks.

       This is what makes a search across directions mean anything. Measured
       against its own direction, a short on a name that rose eightfold looks
       superb — it only has to beat shorting blindly, which lost 17% a go, so
       returning nothing scores +17. Measured against having just held the
       thing, which is the alternative anyone actually had, it scores −17. The
       second number is the one a person means by "best strategy", and it is
       the only one that can be compared between a long and a short at all. */
    var held = {};
    for (var hh2 = 0; hh2 < holds.length; hh2++) {
      for (var k2 in slices) {
        held[holds[hh2] + "|" + k2] = run(payload, series, {
          rule: "every", look: 1, dir: "up", hold: holds[hh2], target: target,
          overlap: true, from: slices[k2].from, until: slices[k2].until
        });
      }
    }

    var cells = [];
    for (var r = 0; r < rules.length; r++) {
      for (var l = 0; l < looks.length; l++) {
        var found = {};
        for (var key in slices) {
          found[key] = signals(payload, series, { rule: rules[r], look: looks[l] },
                               slices[key].from, slices[key].until);
        }
        for (var hh = 0; hh < holds.length; hh++) {
          for (var dd = 0; dd < dirs.length; dd++) {
            var cell = { rule: rules[r], look: looks[l], hold: holds[hh], dir: dirs[dd] };
            for (var sk in slices) {
              var got = run(payload, series, {
                rule: rules[r], look: looks[l], dir: dirs[dd], hold: holds[hh],
                target: target, overlap: overlap,
                from: slices[sk].from, until: slices[sk].until
              }, found[sk]);
              var against = base[dirs[dd] + "|" + holds[hh] + "|" + sk];
              var alt = held[holds[hh] + "|" + sk];
              var gap = edge(got, against);
              cell[sk] = { trades: got.trades, rate: got.rate, exit: got.median_exit,
                           base_rate: against.rate, base_exit: against.median_exit,
                           held_exit: alt.median_exit,
                           // Three different claims, and only the last one is
                           // what "best" should mean. `rate` is how often it
                           // hit; `edge` is the hit-rate gap against its own
                           // direction, in points; `ret` is how many points of
                           // *return* the median trade beat simply holding the
                           // name by. The search ranks on `ret`: a rule can hit
                           // its target more often than average and still lose
                           // money, and a short can beat every other short
                           // while losing to having done nothing.
                           edge: gap.rate,
                           ret: (got.median_exit === null || alt.median_exit === null)
                                  ? null : got.median_exit - alt.median_exit,
                           thin: got.trades < SEARCH_FLOOR };
            }
            cells.push(cell);
          }
        }
      }
    }
    return { cells: cells, cut: cut, tried: cells.length,
             train_weeks: cut, test_weeks: last - cut + 1,
             train_from: (payload.starts || [])[0],
             train_to: (payload.starts || [])[Math.max(0, cut - 1)],
             test_from: (payload.starts || [])[cut],
             test_to: (payload.starts || [])[last] };
  }

  /* The pick, and everything needed to disbelieve it.

     Ranked on the training slice only — `test` is never allowed to choose, or
     the holdout stops being one and the whole exercise becomes the thing it
     exists to catch. */
  function best(payload, series, opt, axes) {
    var found = search(payload, series, opt, axes);
    var out = { ticker: (series || {}).ticker || null, tried: found.tried, cut: found.cut,
                train_from: found.train_from, train_to: found.train_to,
                test_from: found.test_from, test_to: found.test_to,
                pick: null, hindsight: null, held_up: false,
                median_usable_test: null, ranked: 0 };

    // Only combinations with enough finished trades on *both* slices can be
    // ranked: one that traded plenty while it was being searched and twice in
    // the holdout has not been tested, it has been guessed at.
    //
    // And it has to have made money while it was being searched. The baseline
    // is matched on direction, which is what stops a rising decade flattering
    // every long — but it also means a short is only ever measured against
    // shorting blindly, and on a name that rose eightfold that bar is on the
    // floor. Beating it by thirty points while returning nothing is not a
    // strategy, so a negative median return cannot be crowned however well it
    // compares.
    var usable = found.cells.filter(function (c) {
      return c.train.ret !== null && c.test.ret !== null &&
             !c.train.thin && !c.test.thin && c.train.exit > 0;
    });
    out.ranked = usable.length;
    if (!usable.length) return out;

    var byTrain = usable.slice().sort(function (a, b) { return b.train.ret - a.train.ret; });
    var byTest = usable.slice().sort(function (a, b) { return b.test.ret - a.test.ret; });
    out.pick = byTrain[0];
    out.hindsight = byTest[0];
    // What an ordinary combination did on the holdout. The pick has to beat
    // this to have been worth searching for, never mind beating zero.
    //
    // Not `median_test`: `bestAll` below has a field by that name meaning
    // something else entirely — the median across names of the *picks'* test
    // edge. One is a spread within a name, the other a middle across names,
    // and a reader who grabbed the wrong one would get a plausible number
    // rather than an error.
    out.median_usable_test = median(usable.map(function (c) { return c.test.ret; }));
    // The whole verdict, in one boolean, and it takes both halves: did the
    // setting the search crowned go on to beat that name's own baseline on
    // weeks it never saw, *and* actually make money doing it? Either alone
    // can be had cheaply — a losing short beats a worse short, and a long on
    // a rising name makes money without beating anything.
    out.held_up = out.pick.test.ret > 0 && out.pick.test.exit > 0;
    return out;
  }

  /* The same search for every name, and — the number that actually settles it —
     how many of the picks held up.

     If choosing a strategy per name were a real thing to do, the picks would
     beat their baselines out of sample far more often than a coin would. If
     roughly half of them do, the search is an expensive way to generate noise,
     and the honest thing is to report that in those words. */
  function bestAll(payload, opt, axes) {
    var series = (payload || {}).series || [];
    var names = [];
    for (var i = 0; i < series.length; i++) names.push(best(payload, series[i], opt, axes));
    var rated = names.filter(function (n) { return n.pick; });
    var held = rated.filter(function (n) { return n.held_up; });
    return {
      names: names, rated: rated.length, held: held.length,
      held_pct: rated.length ? (held.length / rated.length) * 100 : null,
      median_train: median(rated.map(function (n) { return n.pick.train.ret; })),
      median_test: median(rated.map(function (n) { return n.pick.test.ret; })),
      median_hindsight: median(rated.map(function (n) { return n.hindsight.test.ret; })),
      // What the picks *returned*, next to what they beat. The edge is a gap
      // between two rates; this is the money, and they are not the same claim.
      median_return: median(rated.map(function (n) { return n.pick.test.exit; })),
      median_return_train: median(rated.map(function (n) { return n.pick.train.exit; })),
      tried: rated.length ? rated[0].tried : 0,
      train_from: rated.length ? rated[0].train_from : null,
      train_to: rated.length ? rated[0].train_to : null,
      test_from: rated.length ? rated[0].test_from : null,
      test_to: rated.length ? rated[0].test_to : null
    };
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

  // Both rules' sentences, when there are two. Returned as a pair rather than
  // one string because the page sets them in different type — the rule is the
  // claim, the filter is a condition on it — and joining them here would decide
  // that for it.
  function describePair(opt) {
    var also = opt.also && opt.also !== "every" && RULES[opt.also] ? opt.also : null;
    return {
      rule: describe(opt.rule, Math.round(opt.look)),
      also: also ? describe(also, Math.round(opt.alsoLook || opt.look)) : "",
      label: (RULES[opt.rule] || {}).label || opt.rule,
      alsoLabel: also ? RULES[also].label : ""
    };
  }

  return { RULES: RULES, RULE_KEYS: RULE_KEYS, NOTES: NOTES, median: median, width: width,
           widths: widths, fires: fires, signals: signals, warmup: warmup, from: from,
           trade: trade, run: run, all: all, sweep: sweep, sweepAxes: sweepAxes, here: here,
           baselineOf: baselineOf,
           edge: edge, describe: describe, describePair: describePair,
           search: search, best: best, bestAll: bestAll, cutAt: cutAt,
           SWEEP_HOLDS: SWEEP_HOLDS, SWEEP_LOOKS: SWEEP_LOOKS, SWEEP_FLOOR: SWEEP_FLOOR,
           TRAIN_FRAC: TRAIN_FRAC, SEARCH_FLOOR: SEARCH_FLOOR, SEARCH_RULES: SEARCH_RULES,
           SEARCH_LOOKS: SEARCH_LOOKS, SEARCH_HOLDS: SEARCH_HOLDS, SEARCH_DIRS: SEARCH_DIRS };
});
