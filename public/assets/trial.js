/* The Repeat test's counting rules.
 *
 * "Buy in week 37 every year, hold it eight weeks — how many of those years
 * *closed* at least 8% up?" This file is the whole of what *closed past*, *fell
 * short*, *still open* and *skipped* mean; app.js only draws what comes out of
 * it.
 *
 * The verdict is the exit, not the best price on the way. A window that spiked
 * past the target in week three and gave it all back by week eight fell short,
 * and is counted as one — `touched` is reported beside the verdict rather than
 * being it, because it says the price was there, not that you were still in the
 * trade when it was.
 *
 * It is a separate file so it can be tested. `tests/test_trial.py` runs this
 * exact source under node against hand-built payloads, so the rules are pinned
 * to the code that ships rather than to a Python re-implementation that would
 * drift from it. Keep it free of the DOM for that reason.
 *
 * The window arithmetic is positional: week i + hold − 1 is the last week of
 * the trade. That is only sound because weekly.json puts every name on one
 * shared, gapless ISO-week axis — see spread_scanner/weekly.py, which also
 * ships the prose describing these rules, under `reference`.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpreadTrial = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function weekId(year, week) {
    return year + "-W" + (week < 10 ? "0" + week : String(week));
  }

  function median(values) {
    var v = values.filter(function (x) { return x !== null && x !== undefined && !isNaN(x); });
    if (!v.length) return null;
    v.sort(function (a, b) { return a - b; });
    var m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  }

  /* ISO week id -> position on the shared axis. Built once per payload. */
  function index(payload) {
    var at = {};
    for (var i = 0; i < payload.weeks.length; i++) at[payload.weeks[i]] = i;
    return at;
  }

  /* One year of the trial for one name.
     Every branch that cannot reach a verdict says which branch it is. Neither
     "still open" nor "skipped" is a failure, and quietly counting either as one
     is how a hit rate ends up flattering. */
  function year(payload, series, opt, y, at, last) {
    var up = opt.dir !== "down";
    var i = at[weekId(y, opt.week)];
    var row = { year: y, week: payload.weeks[i], start: payload.starts[i], state: "skipped" };

    var entryAt = i - 1;          // you buy as the week opens: the last close before it
    if (entryAt < 0 || series.close[entryAt] === null) {
      row.why = "no close before that week";
      return row;
    }
    row.entry = series.close[entryAt];
    row.entry_week = payload.starts[entryAt];
    // `opt.target` may be zero or negative — the in-the-money thesis, where the
    // level to finish past sits *at or behind* the entry rather than beyond it
    // ("−3% over eight weeks" asks how many years closed no worse than 3%
    // down). The arithmetic is the same either way, and so is the comparison
    // below; only the reading changes. Going down the sign flips with it, so a
    // downside target of −3% is a level 3% above the entry.
    row.target = row.entry * (up ? 1 + opt.target / 100 : 1 - opt.target / 100);

    var end = i + opt.hold - 1, stop = Math.min(end, last);
    var fav = null, adv = null, hitAt = -1;
    for (var k = i; k <= stop; k++) {
      if (series.close[k] === null || series.high[k] === null || series.low[k] === null) {
        // A hole in this name's history. Refused rather than closed up: skipping
        // the missing weeks would silently make the window span more calendar
        // time than the hold it claims to be.
        row.why = "the history has a gap inside that window";
        return row;
      }
      var f = up ? series.high[k] : series.low[k];       // the extreme toward the target
      var a = up ? series.low[k] : series.high[k];       // and the one against it
      if (fav === null || (up ? f > fav : f < fav)) fav = f;
      if (adv === null || (up ? a < adv : a > adv)) adv = a;
      if (hitAt < 0 && (up ? f >= row.target : f <= row.target)) hitAt = k;
    }

    row.best_pct = (fav / row.entry - 1) * 100;
    row.worst_pct = (adv / row.entry - 1) * 100;
    row.touched = hitAt >= 0;
    if (row.touched) { row.hit_in = hitAt - i + 1; row.hit_week = payload.starts[hitAt]; }

    if (end > last) {
      // The window has not run out, so it has no exit — and the exit is the
      // whole verdict. That holds even when the target is already behind it: a
      // name can be past the target in week three and back under it by week
      // eight, so admitting one would move the rate in one direction only and
      // the newest year would quietly hold the headline up. `touched` and
      // `open_pct` ride along, so the page can say where it stands without
      // counting it.
      row.state = "open";
      row.ran = stop - i + 1;
      row.open_pct = (series.close[stop] / row.entry - 1) * 100;
      return row;
    }
    row.settled = true;
    row.exit = series.close[end];
    row.exit_pct = (row.exit / row.entry - 1) * 100;
    // Where the trade *closed*. Eight weeks at +1% asks whether the close of
    // week eight is 1% above the entry — nothing else in the window decides it.
    // `settled` says the window ran out; `closed_past` says it ended on the
    // right side of the target. Only the second one is a verdict.
    row.closed_past = up ? row.exit >= row.target : row.exit <= row.target;
    row.state = row.closed_past ? "hit" : "miss";
    return row;
  }

  /* The same trade in the same week of each of the last `opt.years` years. */
  function run(payload, series, opt, at) {
    // Every field of the result exists from the start, including on the early
    // return below: a caller that got a half-shaped object back read `decided`
    // as undefined and quietly rendered a rate against nothing.
    // `hit`/`miss` are closed past / fell short; `touched` is the softer count
    // reported next to them, never the verdict.
    var out = { rows: [], hit: 0, miss: 0, open: 0, skipped: 0, touched: 0,
                touched_open: 0, decided: 0, rate: null, touch_rate: null,
                median_best: null, median_worst: null, median_weeks: null,
                asked: opt.years };
    var last = (payload.weeks || []).length - 1;
    if (last < 0 || !series) return out;

    var newest = parseInt(payload.weeks[last].slice(0, 4), 10);
    var oldest = parseInt(payload.weeks[0].slice(0, 4), 10);
    var years = [];
    // Backwards from the newest year, keeping only the years that *have* the
    // chosen week. ISO week 53 falls in about one year in six, so asking for it
    // is a legitimate question with a much shorter answer.
    for (var y = newest; y >= oldest && years.length < opt.years; y--) {
      if (at[weekId(y, opt.week)] !== undefined) years.push(y);
    }
    years.reverse();

    for (var n = 0; n < years.length; n++) {
      var row = year(payload, series, opt, years[n], at, last);
      out.rows.push(row);
      out[row.state]++;
      if (row.state === "open" && row.touched) out.touched_open++;
      if (row.settled && row.touched) out.touched++;
    }

    // One denominator for both rates: the years whose window actually finished.
    var settled = out.rows.filter(function (r) { return r.settled; });
    out.decided = settled.length;
    out.rate = out.decided ? (out.hit / out.decided) * 100 : null;
    out.touch_rate = out.decided ? (out.touched / out.decided) * 100 : null;
    // The medians describe finished windows too. A part-run window has had less
    // time to travel in either direction, so mixing one in understates both
    // numbers while being presented as a full-window figure.
    out.median_best = median(settled.map(function (r) { return r.best_pct; }));
    out.median_worst = median(settled.map(function (r) { return r.worst_pct; }));
    out.median_weeks = median(settled.filter(function (r) { return r.touched; })
                                     .map(function (r) { return r.hit_in; }));
    return out;
  }

  return { weekId: weekId, median: median, index: index, year: year, run: run };
});
