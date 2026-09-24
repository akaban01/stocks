/* Spread Scanner — frontend: the Backtest tab views (rules, sweep, best per name, pricing). The rules themselves are assets/backtest.js.
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.btStore = btStore;
  App.btSweepDraw = btSweepDraw;
  App.bmDraw = bmDraw;
  App.wireMoney = wireMoney;
  App.bsDraw = bsDraw;
  App.renderBacktest = renderBacktest;
  App.wireBacktest = wireBacktest;

  // ------------------------------------------------------------- backtest
  //
  // "If I had always bought the squeeze and held it eight weeks, what
  // happened?" The Repeat test asks about one week of the calendar; this asks
  // about a rule, run over every name and every week there was.
  //
  // Three things make an answer like that worth printing, and all three are in
  // assets/backtest.js rather than here: the rule sees week i and the weeks
  // before it and nothing after; the trade is entered at that week's close; and
  // the verdict is the exit, with touched reported beside it. What this file
  // does is draw the result — and put the baseline next to it, because a rate
  // on its own is a fact about the decade, not about the rule.

  var bt = { rule: "squeeze", look: 8, also: "every", alsoLook: 20, dir: "up", hold: 8,
             target: 8, years: 10, overlap: false, ticker: "" }; App.bt = bt;
  var btSort = { key: "edge", dir: -1 };

  /* One range per numeric control, for the same reason the money section has
     one: the input handlers are not the only way into this state — a stale or
     hand-edited `backtest` blob in localStorage reaches the arithmetic without
     passing a control, and a `hold` of zero would divide the history into
     trades with no window. The markup's min/max are the same numbers. */
  var BT_RANGE = {
    look:     { lo: 2, hi: 52, fallback: 8, whole: true },
    alsoLook: { lo: 2, hi: 52, fallback: 20, whole: true },
    hold:     { lo: 1, hi: 52, fallback: 8, whole: true },
    years:    { lo: 1, hi: 25, fallback: 10, whole: true },
    // Zero or negative is the in-the-money question — "did it hold up" rather
    // than "did it travel" — exactly as on the Repeat test.
    target:   { lo: -95, hi: 300, fallback: 8 }
  };

  function btClamp(key, value) {
    var r = BT_RANGE[key];
    if (!r) return value;
    var v = Number(value);
    if (value === "" || value === null || isNaN(v)) v = r.fallback;
    v = Math.min(r.hi, Math.max(r.lo, v));
    return r.whole ? Math.round(v) : v;
  }

  function btStore() {
    try { localStorage.setItem("backtest", JSON.stringify(bt)); } catch (e) { /* private mode */ }
  }

  function btOpt() {
    return { rule: bt.rule, look: bt.look, also: bt.also, alsoLook: bt.alsoLook,
             dir: bt.dir, hold: bt.hold, target: bt.target, years: bt.years,
             overlap: bt.overlap };
  }

  // Whether a second rule is actually filtering anything. "every" is the
  // control's "— nothing", and it is the same no-op the engine reads it as.
  function btFiltered() {
    return !!(bt.also && bt.also !== "every" && SpreadBacktest.RULES[bt.also]);
  }

  function btMove() { return bt.dir === "up" ? bt.target : -bt.target; }

  function btGoal() {
    var m = btMove();
    return (m > 0 ? "+" : m < 0 ? "−" : "") + App.num(Math.abs(m), 1) + "%";
  }

  function btSide() { return bt.dir === "up" ? " or better" : " or lower"; }

  // Points, not percent: the gap between two rates is a difference of
  // percentages and calling it a percentage is how "6 points better" becomes
  // "6% better", which is a different and much smaller claim.
  function points(v, digits) {
    if (!App.has(v) || isNaN(v)) return "—";
    var d = digits === undefined ? 1 : digits;
    var shown = Math.abs(Number(v)).toFixed(d);
    return (v >= 0 ? "+" : "−") + App.num(Math.abs(v), d) +
      (Number(shown) === 1 ? " pt" : " pts");
  }

  /* The baseline is the expensive half of every redraw and depends on none of
     the rule controls, so turning the lookback dial does not recompute it. */
  var btBase = { key: null, value: null };

  function btBaseline(d) {
    var want = SpreadBacktest.baselineOf(btOpt());
    var key = JSON.stringify(want);
    if (btBase.key !== key) btBase = { key: key, value: SpreadBacktest.all(d, want) };
    return btBase.value;
  }

  // ---- rendering

  function btTiles(res, base, gap) {
    function tile(cls, k, v) {
      return '<div class="rule ' + cls + '"><span class="k">' + k + '</span><div class="v">' + v +
        "</div></div>";
    }
    var goal = btGoal() + btSide();
    var verdict = gap.rate === null ? "fair" : gap.rate > 0 ? "cheap" : "rich";
    var edgeSaid = gap.rate === null ? "no edge to report"
      : points(gap.rate, 0) + " against every week";

    return '<div class="rulebar">' +
      tile(verdict, "The edge — " + edgeSaid,
           gap.rate === null
             ? "Nothing finished on these settings, so there is no rate to compare. Shorten the "
               + "hold, widen the history, or pick a rule that fires more often."
             : "How much more often the rule's weeks closed " + goal + " than weeks in general "
               + "did, on the same names over the same history. This is the whole claim: "
               + "everything else on this tab is either an input to it or a reason to distrust "
               + "it. Under about thirty trades, read it as a hint at best.") +
      tile("fair", "The rule — " + (res.trades ? App.num(res.rate, 0) + "% of " + App.num(res.trades, 0) +
             " trade" + (res.trades === 1 ? "" : "s") : "no finished trade"),
           "Trades that closed " + goal + " at the exit. The rule fired " + App.num(res.fired, 0) +
           " time" + (res.fired === 1 ? "" : "s") + " and " +
           (bt.overlap ? "every firing was counted" : "took " + App.num(res.taken, 0) +
             " of them one at a time") + ".") +
      tile("fair", "Every week — " + (base.trades ? App.num(base.rate, 0) + "% of " +
             App.num(base.trades, 0) : "nothing to compare"),
           "The same names, hold and target, entered on every week of the same history. Not a "
           + "strategy — a description of what these weeks did in general, which is what the "
           + "rule has to beat to have said anything.") +
      tile("fair", "Median exit — " + App.signed(res.median_exit) +
             " against " + App.signed(base.median_exit),
           "Where the typical trade closed, against where the typical week closed. Two rules can "
           + "clear the line equally often and be nothing alike; this is what separates them. "
           + (gap.worst === null ? "" : "The typical worst point inside the window was " +
              App.signed(res.median_worst) + ", against " + App.signed(base.median_worst) + ".")) +
      "</div>";
  }

  /* The baseline, selected as a rule.

     It is the one setting where the edge is not a finding, and saying so is
     worth more than the number: with overlapping windows counted, the rule and
     the baseline are the same run and the gap is exactly zero — which is what
     "nothing to find" is supposed to look like. One trade at a time, "every
     week" becomes every Nth week, a thinned sample of the very thing it is
     being compared against, so whatever gap appears is the noise floor on
     these settings. A rule has to clear that before it has said anything, and
     a reader who has not seen how big it is has no way to know that. */
  function btNoiseFloor(res, base, gap) {
    if (bt.rule !== "every") return "";
    if (bt.overlap) {
      return "This <i>is</i> the baseline, run as a rule, so the edge is zero by construction. "
        + "That is the point of looking at it: it is what this comparison shows when there is "
        + "nothing to find.";
    }
    return "One trade at a time turns <i>every week</i> into every " + bt.hold +
      "th week — a thinned sample of the baseline rather than a rule, so the " +
      App.esc(points(gap.rate, 0)) + " above is not a finding. It is the <b>noise floor</b> on these "
      + "settings: the gap a rule that knows nothing still shows, and the bar any rule has to "
      + "clear before it has said anything.";
  }

  /* The sentence under the tiles. It says one of three things — the rule beat
     the baseline, it did not, or there is not enough here to tell — and the
     third is a real answer rather than a failure to produce one. */
  function btVerdict(res, base, gap) {
    var thin = res.trades < 30;
    var floor = btNoiseFloor(res, base, gap);
    if (floor) return '<div class="verdict bad">' + floor + "</div>";
    if (gap.rate === null) {
      return '<div class="verdict bad">Nothing finished on these settings, so there is nothing ' +
        "to compare. A rule that fires ten times in ten years cannot be backtested on ten " +
        "years.</div>";
    }
    var better = gap.rate > 0;
    var said = better
      ? "On this history the rule picked better weeks than the average week: " +
        App.num(res.rate, 0) + "% of its trades closed " + btGoal() + btSide() + ", against " +
        App.num(base.rate, 0) + "% of all weeks — " + points(gap.rate, 0) + "."
      : gap.rate === 0
        ? "On this history the rule picked weeks that were indistinguishable from weeks in "
          + "general: both closed " + btGoal() + btSide() + " " + App.num(base.rate, 0) +
          "% of the time."
        : "On this history the rule picked worse weeks than the average week: " +
          App.num(res.rate, 0) + "% against " + App.num(base.rate, 0) + "% — " + points(gap.rate, 0) +
          ". Going the other way on the direction chip is not the fix it looks like; "
          + "a rule that is wrong is rarely exactly inverted.";
    var caution = thin
      ? " That rests on " + App.num(res.trades, 0) + " finished trade" +
        (res.trades === 1 ? "" : "s") + ", which is too few to separate a rule from a run of luck."
      : " And these names move together, so the trades behind it are not as independent as their "
        + "number looks.";
    return '<div class="verdict ' + (better && !thin ? "good" : "bad") + '">' + App.esc(said) +
      App.esc(caution) + "</div>";
  }

  // What the run set aside, said out loud. An open trade and a skipped one are
  // in neither column, and a tab that did not say so would be reporting a rate
  // against a denominator the reader cannot see.
  function btPending(res) {
    var bits = [];
    if (res.open) {
      bits.push(res.open + (res.open === 1 ? " trade is" : " trades are") +
                " still running — no exit yet, so counted in neither column");
    }
    if (res.skipped) bits.push(res.skipped + " skipped for a gap in the history");
    if (!bits.length) return "";
    return '<p class="faint" style="font-size:.83rem;margin:-12px 0 16px">' +
      App.esc(bits.join(" · ")) + ".</p>";
  }

  /* Every name on the same rule, each against its own baseline.

     One name at 70% says nothing until you can see whether that is 70% against
     a baseline of 50 or against one of 68 — which is the entire reason the
     baseline column sits beside the rate rather than only in the headline. */
  function btNameTable(res, base) {
    var floor = 5;                     // trades, below which a name is reported but not ranked
    var baseBy = {};
    for (var b = 0; b < base.names.length; b++) baseBy[base.names[b].ticker] = base.names[b];

    var rows = res.names.map(function (n) {
      var mine = baseBy[n.ticker] || {};
      var gap = SpreadBacktest.edge(n, mine);
      return { ticker: n.ticker, trades: n.trades, hit: n.hit, rate: n.rate,
               base: App.has(mine.rate) ? mine.rate : null, edge: gap.rate,
               exit: n.median_exit, worst: n.median_worst, thin: n.trades < floor };
    }).filter(function (r) { return r.trades > 0; });
    if (!rows.length) return "";

    var key = btSort.key;
    rows.sort(function (a, b2) {
      if (key === "ticker") return a.ticker.localeCompare(b2.ticker) * btSort.dir;
      // Names with too few trades are reported but never ranked: three trades
      // at 100% would otherwise sit above forty at 65% and read as the name the
      // rule works on. Same floor, same reason, as the Repeat test's ranking.
      if (a.thin !== b2.thin) return a.thin ? 1 : -1;
      var x = a[key], y = b2[key];
      x = App.has(x) ? x : -Infinity; y = App.has(y) ? y : -Infinity;
      return (x === y ? a.trades - b2.trades : x - y) * btSort.dir;
    });

    function th(k, label, cls) {
      return App.sortableTh(k, label, cls || "",
        btSort.key === k ? (btSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    var body = rows.map(function (r) {
      return '<tr class="srow' + (r.thin ? " thin" : "") +
        (r.ticker === bt.ticker ? " picked" : "") + '" data-ticker="' + App.esc(r.ticker) +
        '" tabindex="0" title="' + App.esc(r.thin
          ? r.ticker + " has only " + r.trades + " finished trade" + (r.trades === 1 ? "" : "s") +
            " here — shown, but not ranked"
          : "show " + r.ticker + "'s trades below") + '">' +
        '<td class="t">' + App.esc(r.ticker) + "</td>" +
        '<td class="r">' + App.num(r.trades, 0) + "</td>" +
        '<td class="r' + (r.hit ? " hit" : "") + '">' + App.num(r.hit, 0) + "</td>" +
        '<td class="r ' + (r.rate >= 50 ? "hit" : "miss") + '"><b>' + App.num(r.rate, 0) + "%</b></td>" +
        '<td class="r faint">' + (App.has(r.base) ? App.num(r.base, 0) + "%" : "—") + "</td>" +
        '<td class="r ' + (!App.has(r.edge) ? "" : r.edge > 0 ? "hit" : r.edge < 0 ? "miss" : "") +
          '">' + points(r.edge, 0) + "</td>" +
        '<td class="r soft-hit">' + App.signed(r.exit) + "</td>" +
        '<td class="r soft-miss">' + App.signed(r.worst) + "</td></tr>";
    }).join("");

    var gap = SpreadBacktest.edge(res, base);
    var foot = "<tfoot><tr>" +
      '<td class="t">' + rows.length + " name" + (rows.length === 1 ? "" : "s") + "</td>" +
      '<td class="r">' + App.num(res.trades, 0) + "</td>" +
      '<td class="r' + (res.hit ? " hit" : "") + '">' + App.num(res.hit, 0) + "</td>" +
      '<td class="r ' + (res.rate >= 50 ? "hit" : "miss") + '">' + App.num(res.rate, 0) + "%</td>" +
      '<td class="r faint">' + App.num(base.rate, 0) + "%</td>" +
      '<td class="r ' + (!App.has(gap.rate) ? "" : gap.rate > 0 ? "hit" : gap.rate < 0 ? "miss" : "") +
        '">' + points(gap.rate, 0) + "</td>" +
      '<td class="r faint" title="' + App.esc("no total: a median of medians is not a median — the " +
        "pooled figure in the tiles is taken over every trade") + '">—</td>' +
      '<td class="r faint">—</td>' +
      "</tr></tfoot>";

    return "<h2>The same rule, every name</h2>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Each name\'s record on this rule, ' +
      "beside what every week of that same name did — the two columns whose difference is the " +
      "only part of a rate that belongs to the rule. Click a row for its trades. Names with " +
      "fewer than " + floor + " finished trades sit at the bottom, greyed: reported, never " +
      "ranked.</p>" +
      '<div class="tablewrap"><table class="scan rank"><thead><tr>' +
      th("ticker", "Name") + th("trades", "Trades", "r") + th("hit", "Closed", "r") +
      th("rate", "Closed %", "r") + th("base", "Every week", "r") + th("edge", "Edge", "r") +
      th("exit", "Median exit", "r") + th("worst", "Median worst", "r") +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>";
  }

  // The trades themselves, newest first, for the name on the picker. Capped:
  // a rule that fires every week over ten years is a thousand rows nobody
  // reads, and the pooled answer is above.
  var BT_MAX_ROWS = 40;

  function btTradeTable(res) {
    var one = null;
    for (var i = 0; i < res.names.length; i++) {
      if (res.names[i].ticker === bt.ticker) { one = res.names[i]; break; }
    }
    if (!one) return "";
    if (!one.rows.length) {
      return "<h2>" + App.esc(bt.ticker) + " — every trade</h2>" +
        '<p class="empty">This rule never fired on ' + App.esc(bt.ticker) +
        " over that stretch of history.</p>";
    }
    var labels = { hit: "Closed past", miss: "Fell short", open: "Still open", skipped: "Skipped" };
    var shown = one.rows.slice().reverse().slice(0, BT_MAX_ROWS);
    var body = shown.map(function (r) {
      if (r.state === "skipped") {
        return '<tr class="dim"><td class="t">' + App.esc(r.week) + "</td><td>" + App.esc(r.start) +
          '</td><td colspan="5" class="faint">' + App.esc(r.why || "not enough history") +
          '</td><td class="skipped">Skipped</td></tr>';
      }
      var exit = r.settled
        ? '<td class="r ' + (r.closed_past ? "hit" : "miss") + '">' + App.signed(r.exit_pct) + "</td>"
        : '<td class="r faint">running</td>';
      // Touched keeps its own colour, off the buy/sell axis, for the reason it
      // does on the Repeat test: a trade that touched and then closed back is
      // red at the exit and still blue here, and those are two different claims.
      var touched = '<td class="r' + (r.touched ? " touch" : "") + '">' +
        (r.touched ? "week " + r.hit_in : "—") + "</td>";
      var verdict = r.state === "open"
        ? (r.touched ? "Touched, still open" : "Still open") + " (" + r.ran + "/" + bt.hold + "w)"
        : labels[r.state];
      return '<tr><td class="t">' + App.esc(r.week) + "</td><td>" + App.esc(r.start) + "</td>" +
        '<td class="r">' + App.money(r.entry) + "</td>" +
        '<td class="r">' + App.money(r.target) + "</td>" + exit +
        '<td class="r">' + App.signed(r.best_pct) + "</td>" + touched +
        '<td class="' + r.state + '">' + verdict + "</td></tr>";
    }).join("");

    var more = one.rows.length > BT_MAX_ROWS
      ? '<p class="faint" style="font-size:.83rem;margin:8px 0 0">The ' + BT_MAX_ROWS +
        " most recent of " + one.rows.length + " — the counts above are over all of them.</p>"
      : "";
    var foot = !one.trades ? "" : "<tfoot><tr>" +
      '<td class="t">' + one.trades + " judged</td>" +
      '<td class="faint">' + (one.open + one.skipped ? (one.open + one.skipped) + " set aside" : "") +
        "</td><td></td><td></td>" +
      '<td class="r ' + (one.rate >= 50 ? "hit" : "miss") + '">' + one.hit + " of " + one.trades +
        "</td>" +
      '<td class="r">' + App.signed(one.median_best) + "</td>" +
      '<td class="r' + (one.touched ? " touch" : "") + '">' + one.touched + " of " + one.trades +
        "</td>" +
      "<td>" + App.num(one.rate, 0) + "% closed · " + App.num(one.touch_rate, 0) + "% touched</td>" +
      "</tr></tfoot>";

    return "<h2>" + App.esc(bt.ticker) + " — every trade this rule took</h2>" +
      '<div class="tablewrap"><table class="scan trial"><thead><tr>' +
      "<th>Signal week</th><th>Entered</th><th class=\"r\">Entry</th><th class=\"r\">Target</th>" +
      '<th class="r">At exit</th><th class="r">Best</th><th class="r">Touched</th>' +
      "<th>Result</th></tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>" + more;
  }

  /* What this test does, and — separately — what it is not.

     The rules it does are backtest.js's own and ship with it. The one fact
     about the *data* comes off weekly.json, where the backend owns it. */
  function btCaveats(d) {
    var notes = SpreadBacktest.NOTES.map(function (n) { return "<li>" + App.esc(n) + "</li>"; });
    var prices = ((d && d.reference) || {}).prices;
    if (prices) notes.push("<li>" + App.esc(prices) + "</li>");
    return '<div class="panelcard" style="margin-top:18px">' +
      "<h3>What this test does, exactly</h3><ul>" + notes.join("") + "</ul>" +
      '<p class="dim" style="font-size:.85rem;margin-top:10px"><b>And what it does not.</b> ' +
      "This list is whoever passes the screen <i>today</i>, so ten years of it is ten years of " +
      "the survivors — the names a rule would have lost money on are the ones no longer here to " +
      "be measured. That cuts both ways and the baseline is why it is survivable: it carries the " +
      "same bias, so the gap between the two is far more honest than either number alone. " +
      "Nothing here charges commission, slippage or the spread you would actually have crossed, " +
      "and nothing knows about earnings dates. A week is the finest grain there is — a stop " +
      "inside the week is invisible to it. And <b>a stock finishing past your level is not the " +
      "spread paying out</b>: that is priced on the Spreads tab, and a closing rate here is the " +
      "first of the two conditions, not a backtested return.</p>" + btMissingPrice(d) + "</div>";
  }

  /* The one bullet this tab takes from the payload, named rather than dropped
     when an older scan wrote a file without it. Quietly and in place: a missing
     definition is a gap in the prose, not a reason to distrust the numbers —
     rpStale above is what says that, and it says it at the top. */
  function btMissingPrice(d) {
    if (((d && d.reference) || {}).prices) return "";
    return '<p class="faint" style="font-size:.83rem;margin-top:10px">This history does not ' +
      "carry the rule <code>prices</code>, so how the closes are adjusted is absent from the " +
      "list above rather than inapplicable to it — it was written by an older scan than this " +
      "page. The next scan restores it.</p>";
  }

  /* The rule's own sentence, with the lookback filled in, above the answer.
     Where there are two, the second is set as a condition on the first rather
     than as a second claim — which is what an AND is. */
  function btRuleSaid() {
    var said = SpreadBacktest.describePair(btOpt());
    if (!said.rule) return "";
    return '<p class="dim" style="font-size:.87rem;margin:0 0 14px"><b>' +
      App.esc(said.label) + (said.alsoLabel ? " + " + App.esc(said.alsoLabel) : "") + ".</b> " +
      App.esc(said.rule) +
      (said.also ? " <b>And only where</b> " + App.esc(said.also.charAt(0).toLowerCase() +
        said.also.slice(1)) : "") +
      " Held " + bt.hold + " week" + (bt.hold === 1 ? "" : "s") + ", counted as a hit at " +
      App.esc(btGoal() + btSide()) + " on the closing week, over the last " + bt.years +
      " year" + (bt.years === 1 ? "" : "s") + ".</p>";
  }

  /* ---------------------------------------------- sweeping the two dials
   *
   * The controls ask you to pick a hold and a lookback out of the hundreds of
   * pairs available, with nothing to go on. This runs the rule on all of them
   * and paints the grid, so you can see whether the pair you picked sits on a
   * ridge of settings that all worked or is the one green cell in a red field —
   * which is most of what tells a pattern from a coincidence.
   *
   * It is off by default: it is forty-odd runs of the answer above, and it is a
   * second question rather than the one the controls ask.
   */

  // Keyed on what would change the grid. The two dials the grid sweeps are not
  // in it — that is the point: adopt a cell and the grid it came from is still
  // the grid, so it stays put instead of flickering through a recompute.
  var btGrid = { key: null, value: null };

  // The last run this tab drew, kept so the money section below can price the
  // very trades on screen rather than running the rule a second time and
  // hoping the two agree.
  var btLast = null;

  function btSweep(d) {
    var opt = btOpt();
    // The shape is asked for first, and the grid is only built on a miss —
    // resolving the axes by running the sweep would be doing the very work the
    // cache exists to skip.
    var axes = SpreadBacktest.sweepAxes(opt);
    // Everything that changes a cell's value but is not one of the two axes —
    // the second rule very much included, or changing the filter would hand
    // back the unfiltered grid.
    var key = JSON.stringify([bt.rule, bt.also, bt.alsoLook, bt.dir, bt.target,
                              bt.years, bt.overlap, axes.looks, axes.holds]);
    if (btGrid.key !== key) btGrid = { key: key, value: SpreadBacktest.sweep(d, opt) };
    // Always re-marked, hit or miss: the grid outlives the dials, and the cell
    // the controls are on is the one thing about it that moves.
    return SpreadBacktest.here(btGrid.value, opt);
  }

  // The 90th percentile of the edges on the grid, so one runaway cell cannot
  // wash the rest of it out — the same shape of scale the month heat map uses.
  function btSweepScale(cells) {
    var mags = cells.filter(function (c) { return App.has(c.edge) && !isNaN(c.edge); })
                    .map(function (c) { return Math.abs(c.edge); })
                    .sort(function (a, b) { return a - b; });
    if (!mags.length) return 1;
    return Math.max(1, mags[Math.floor(mags.length * 0.9)] || mags[mags.length - 1]);
  }

  function btWeeks(n) { return n + "w"; }

  function btSweepCell(c, scale) {
    var say = btWeeks(c.look) + " lookback held " + btWeeks(c.hold) + ": " +
      (App.has(c.rate) ? App.num(c.rate, 0) + "% of " + App.num(c.trades, 0) + " trades closed " +
        btGoal() + btSide() + ", against " + App.num(c.base_rate, 0) + "% for every week — " +
        points(c.edge, 0)
       : "nothing finished") +
      (c.thin ? ". Under " + SpreadBacktest.SWEEP_FLOOR + " trades, so it is shown but never "
              + "ranked." : "");
    // The cell you are on is outlined; a thin one is faded. Neither is carried
    // by colour alone — the number is in the cell and the whole of it is in the
    // label a screen reader reads.
    return '<td class="sweepcell' + (c.thin ? " thin" : "") + '" tabindex="0"' +
      ' data-look="' + c.look + '" data-hold="' + c.hold + '"' +
      (c.here ? ' aria-pressed="true"' : '') +
      ' style="' + App.heatStyle(c.thin ? null : c.edge, scale) + '"' +
      ' title="' + App.esc(say) + '" aria-label="' + App.esc(say) + '">' +
      (App.has(c.edge) ? points(c.edge, 0).replace(" pts", "").replace(" pt", "") : "—") +
      "</td>";
  }

  function btSweepTable(sw) {
    var scale = btSweepScale(sw.cells);
    var by = {};
    for (var i = 0; i < sw.cells.length; i++) {
      var c = sw.cells[i];
      (by[c.look] = by[c.look] || {})[c.hold] = c;
    }
    var head = "<tr><th>lookback \\ hold</th>" + sw.holds.map(function (h) {
      return '<th class="r">' + btWeeks(h) + "</th>";
    }).join("") + "</tr>";
    var body = sw.looks.map(function (l) {
      return '<tr><td class="t">' + btWeeks(l) + "</td>" + sw.holds.map(function (h) {
        return btSweepCell(by[l][h], scale);
      }).join("") + "</tr>";
    }).join("");
    return '<div class="tablewrap"><table class="scan heat sweep"><thead>' + head +
      "</thead><tbody>" + body + "</tbody></table></div>";
  }

  /* The crown, and the field it is sitting on.
   *
   * The best of forty-eight tries is a low bar to clear by chance, so the
   * winner is never printed alone: the runner-up says whether the win is a
   * ridge or a spike, and the middle of the rankable cells says what an
   * ordinary cell on this grid looks like. A grid whose middle is negative and
   * whose best is +5 is not a strategy that works — it is one cell. */
  function btSweepVerdict(sw) {
    if (!sw.best) {
      return '<p class="empty">No cell on this grid has ' + SpreadBacktest.SWEEP_FLOOR +
        " finished trades behind it, so none of them can be ranked. Widen the history, or " +
        "pick a rule that fires more often.</p>";
    }
    var spread = sw.runner ? sw.best.edge - sw.runner.edge : null;
    return '<div class="rulebar">' +
      '<div class="rule ' + (sw.best.edge > 0 ? "cheap" : "rich") + '">' +
        '<span class="k">Best — ' + btWeeks(sw.best.look) + " lookback, held " +
          btWeeks(sw.best.hold) + ", " + App.esc(points(sw.best.edge, 0)) + "</span>" +
        '<div class="v">' + App.num(sw.best.rate, 0) + "% of " + App.num(sw.best.trades, 0) +
          " trades closed " + App.esc(btGoal() + btSide()) + ", against " +
          App.num(sw.best.base_rate, 0) + "% for weeks in general. " +
          '<button class="bestweek" data-look="' + sw.best.look + '" data-hold="' +
          sw.best.hold + '">Use these settings</button></div></div>' +
      '<div class="rule fair"><span class="k">Runner-up — ' +
        (sw.runner ? App.esc(points(sw.runner.edge, 0)) + " at " + btWeeks(sw.runner.look) +
          " / " + btWeeks(sw.runner.hold) : "none") + "</span>" +
        '<div class="v">' + (spread === null ? "Only one cell could be ranked."
          : "The winner is " + App.esc(points(spread, 1)) + " clear of it. A cell well clear of "
            + "the field is a ridge; a cell a fraction clear is the same crown and much "
            + "weaker evidence.") + "</div></div>" +
      '<div class="rule ' + (sw.middle > 0 ? "cheap" : "rich") + '">' +
        '<span class="k">The middle cell — ' + App.esc(points(sw.middle, 0)) + "</span>" +
        '<div class="v">Half the ranked cells did better than this and half worse. This is ' +
          "what an <i>ordinary</i> setting on this grid is worth, and it is the number the " +
          "winner has to be read against: a grid whose middle is negative has one good cell, " +
          "not a rule that works.</div></div>" +
      "</div>";
  }

  function btSweepBody(d) {
    var sw = btSweep(d);
    var lookless = !(SpreadBacktest.RULES[bt.rule] || {}).look;
    return '<div class="lede">The same rule at every hold' +
      (lookless ? "" : " against every lookback") +
      (btFiltered() ? ", with the second rule held fixed at " + bt.alsoLook +
        " weeks in every cell — the grid sweeps the rule you are testing, not the " +
        "filter on it" : "") +
      ", each cell measured against " +
      "<b>its own column's baseline</b> — every week at that same hold, which is the only " +
      "thing a hold of 26 weeks can honestly be compared with. Green is an edge over that, " +
      "red is worse than it. Click a cell to move the controls there.</div>" +
      btSweepVerdict(sw) + btSweepTable(sw) +
      '<p class="faint" style="font-size:.83rem;margin:10px 0 0">' + sw.tried +
      " cells, " + sw.ranked + " of them with at least " + SpreadBacktest.SWEEP_FLOOR +
      " finished trades behind them — the rest are faded, shown but never ranked. " +
      "<b>These are not " + sw.tried + " independent tries.</b> Neighbouring cells share most " +
      "of their trades, and the names move together on top of that, so the grid is nearer one " +
      "broad answer than " + sw.tried + " of them. And the winner is the best of " + sw.tried +
      " — a high bar to clear on purpose and a low one to clear by luck, which is why the " +
      "runner-up and the middle are printed beside it.</p>";
  }

  function btSweepAdopt(look, hold) {
    bt.look = btClamp("look", look);
    bt.hold = btClamp("hold", hold);
    App.$("#bt-look").value = bt.look;
    App.$("#bt-hold").value = bt.hold;
    btStore();
    btDraw();
    App.$("#btcontrols").scrollIntoView({ block: "nearest" });
  }

  function btSweepDraw() {
    var host = App.$("#sweepbody");
    if (App.btView !== "sweep" || !App.store.weekly) { host.innerHTML = ""; return; }
    if (!App.store.weekly.series.length) { host.innerHTML = ""; return; }
    host.innerHTML = btSweepBody(App.store.weekly);

    function adopt(el) { btSweepAdopt(Number(el.dataset.look), Number(el.dataset.hold)); }
    var cells = host.querySelectorAll("td.sweepcell, button.bestweek");
    for (var i = 0; i < cells.length; i++) {
      cells[i].addEventListener("click", function () { adopt(this); });
      cells[i].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
          e.preventDefault();
          adopt(this);
        }
      });
    }
  }

  function btDraw() {
    var d = App.store.weekly, host = App.$("#backtestbody");
    if (!d) return;
    if (!d.series.length) {
      host.innerHTML = '<p class="empty">The weekly history is empty — no name in this screen ' +
        "has the " + (d.min_weeks || 26) + " weeks a backtest needs.</p>";
      return;
    }
    var res = SpreadBacktest.all(d, btOpt());
    btLast = res;
    var base = btBaseline(d);
    var gap = SpreadBacktest.edge(res, base);

    host.innerHTML = App.rpStale(d) + btRuleSaid() + btTiles(res, base, gap) + btPending(res) +
      btVerdict(res, base, gap) + btNameTable(res, base) + btTradeTable(res) + btCaveats(d);

    App.wireSort(host.querySelectorAll("table.scan.rank thead th"), function (k) {
      if (btSort.key === k) btSort.dir = -btSort.dir;
      else { btSort.key = k; btSort.dir = k === "ticker" ? 1 : -1; }
      btDraw();
      var again = host.querySelector('table.scan.rank thead th[data-key="' + k + '"]');
      if (again) again.focus();
    });

    var picks = host.querySelectorAll("tr.srow[data-ticker]");
    for (var p = 0; p < picks.length; p++) {
      picks[p].addEventListener("click", function () { btPick(this.dataset.ticker); });
      picks[p].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
          e.preventDefault();
          btPick(this.dataset.ticker);
        }
      });
    }

    // The grid reads the same settings this tab does, so it redraws with it —
    // cached on the axes, so adopting a cell does not recompute the grid it
    // came from.
    btSweepDraw();
    // And the money section prices the run that was just drawn.
    bmDraw();
    bsDraw();
  }

  /* ------------------------------------------- pricing the Backtest's trades
   *
   * Everything above this is percentages of the stock and rests on nothing but
   * the closes. This is the other half of the question — what it would have
   * cost and what it would have paid, in dollars — and it is opened
   * deliberately, because it needs one number this repo does not hold.
   *
   * The arithmetic is `SpreadTrial.economics`, the same function the Repeat
   * test's money section uses. It reads `settled`, `entry`, `exit` and
   * `exit_pct` off a row and nothing else, and the rows here carry exactly
   * those — so this tab borrows the option maths rather than keeping a second
   * copy of it that would drift. What lives here is only the rendering, and the
   * controls that feed it.
   */

  var bm = { structure: "spread", long: 0, short: 8, debit: 40, contracts: 1 };
  var bmSort = { key: "net", dir: -1 };

  // The same bounds the Repeat test's money controls carry, for the same
  // reason: localStorage is input, not state, and a hand-edited blob reaches
  // the arithmetic without passing a control.
  var BM_RANGE = {
    long:      { lo: -50, hi: 100, fallback: 0 },
    short:     { lo: -50, hi: 200, fallback: 8 },
    debit:     { lo: 1, hi: 99, fallback: 40 },
    contracts: { lo: 1, hi: 1000, fallback: 1, whole: true }
  };

  function bmClamp(key, value) {
    var r = BM_RANGE[key];
    if (!r) return value;
    var v = Number(value);
    if (value === "" || value === null || isNaN(v)) v = r.fallback;
    v = Math.min(r.hi, Math.max(r.lo, v));
    return r.whole ? Math.round(v) : v;
  }

  function bmSingle() { return bm.structure === "single"; }

  function bmDeal() {
    // `dir` comes off the Backtest tab's own direction chip: a debit spread is
    // a call spread going up and a put spread going down, which is the same
    // fact that chip already carries. Two controls for one fact would let them
    // disagree.
    return { dir: bt.dir, structure: bm.structure, long: bm.long, short: bm.short,
             debit: bm.debit, contracts: bm.contracts };
  }

  function bmStore() {
    try { localStorage.setItem("backtest-money", JSON.stringify(bm)); } catch (e) { /* private */ }
  }

  // ---- rendering

  function bmTiles(econ) {
    function tile(cls, k, v) {
      return '<div class="rule ' + cls + '"><span class="k">' + k + '</span><div class="v">' + v +
        "</div></div>";
    }
    var lots = econ.lots === 1 ? "one contract" : App.num(econ.lots, 0) + " contracts";
    var won = econ.years ? App.num((econ.won / econ.years) * 100, 0) + "%" : "—";
    return '<div class="rulebar">' +
      tile(econ.net > 0 ? "cheap" : econ.net < 0 ? "rich" : "fair",
           "Net — " + App.cash(econ.net),
           App.num(econ.years, 0) + " finished trade" + (econ.years === 1 ? "" : "s") + " at " +
           lots + " each: " + App.cash(econ.paid) + " paid in, " + App.cash(econ.received) +
           " back. Every one of them is in the table below.") +
      tile("fair", "Return on what you staked — " + (App.has(econ.roi) ? App.signed(econ.roi, 0) : "—"),
           "The net over the total debit. It is not an annual figure and it is not " +
           "compounded — the trades overlap or they do not depending on the setting above, " +
           "so there is no one account this could have been run in.") +
      // The sentence under this one has to agree with the number above it. A
      // generic warning that a debit structure usually loses, printed beside a
      // 7-of-7 record, reads as a page not looking at its own output.
      tile("fair", "Won " + econ.won + " of " + App.num(econ.years, 0) + " — " + won,
           (econ.maxed ? App.num(econ.maxed, 0) + " reached the full width; " : "") +
           App.num(econ.worthless, 0) + " expired worthless. " +
           (econ.years && econ.won / econ.years >= 0.5
             ? "Winning this often is not the structure being safe — it is " + App.esc(bt.ticker) +
               " over this stretch, on " + App.num(econ.years, 0) + " trade" +
               (econ.years === 1 ? "" : "s") + ". The net and the breakeven are what to read."
             : "A debit structure loses its whole cost more often than it wins, which is why " +
               "the net matters and the hit rate does not.")) +
      tile(App.has(econ.breakeven) && econ.breakeven >= bm.debit ? "cheap" : "rich",
           "Breakeven debit — " + (App.has(econ.breakeven) ? App.num(econ.breakeven, 1) + "%" : "—"),
           "The debit, as a share of " + (bmSingle() ? "the entry price" : "the width") +
           ", that would have made this whole run wash. Under it these trades made money, " +
           "over it they did not — and unlike everything else here it needs no view on what " +
           "the option actually cost. This is the number to take to a live quote.") +
      "</div>";
  }

  // The trades themselves, newest first and capped: a rule that fires every
  // week over ten years is a thousand rows nobody reads.
  var BM_MAX_ROWS = 40;

  function bmTable(econ) {
    if (!econ.rows.length) return "";
    var shown = econ.rows.slice().reverse().slice(0, BM_MAX_ROWS);
    var body = shown.map(function (r) {
      var net = r.net > 1e-9 ? "up" : r.net < -1e-9 ? "down" : "";
      return "<tr><td class=\"t\">" + App.esc(String(r.when)) + "</td>" +
        '<td class="r">' + App.money(r.entry) + "</td>" +
        '<td class="r">' + App.money(r.long) + "</td>" +
        '<td class="r">' + (r.short === null ? "—" : App.money(r.short)) + "</td>" +
        '<td class="r">' + App.money(r.exit) + "</td>" +
        '<td class="r' + (r.maxed ? " maxed" : r.worthless ? " zero" : "") + '">' +
          App.money(r.worth) + "</td>" +
        '<td class="r out">' + App.cash(r.paid) + "</td>" +
        '<td class="r">' + App.cash(r.received) + "</td>" +
        '<td class="r net ' + net + '">' + App.cash(r.net) + "</td></tr>";
    }).join("");
    var more = econ.rows.length > BM_MAX_ROWS
      ? '<p class="faint" style="font-size:.83rem;margin:8px 0 0">The ' + BM_MAX_ROWS +
        " most recent of " + econ.rows.length + " — the totals above are over all of them.</p>"
      : "";
    var foot = "<tfoot><tr><td class=\"t\">" + App.num(econ.years, 0) + " trades</td>" +
      "<td></td><td></td><td></td><td></td><td></td>" +
      '<td class="r out">' + App.cash(econ.paid) + "</td>" +
      '<td class="r">' + App.cash(econ.received) + "</td>" +
      '<td class="r net ' + (econ.net > 0 ? "up" : econ.net < 0 ? "down" : "") + '">' +
        App.cash(econ.net) + "</td></tr></tfoot>";
    return '<div class="tablewrap"><table class="scan money"><thead><tr>' +
      "<th>Opened</th><th class=\"r\">Entry</th><th class=\"r\">Long</th>" +
      '<th class="r">Short</th><th class="r">Exit</th><th class="r">Worth</th>' +
      '<th class="r">Paid</th><th class="r">Back</th><th class="r">Net</th>' +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>" + more;
  }

  function bmApplyStructure() {
    var single = bmSingle();
    var chips = document.querySelectorAll("#bm-structure button");
    for (var i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-pressed",
        chips[i].dataset.structure === bm.structure ? "true" : "false");
    }
    var shortCtl = App.$("#bm-short-ctl");
    if (shortCtl) shortCtl.hidden = single;
    var label = App.$("#bm-debit-label");
    if (label) {
      label.textContent = single ? "Premium paid, % of entry price" : "Debit paid, % of width";
    }
  }

  /* The trades this prices are the ones on screen: the same rule, the same
     name, the same settings. Deliberately the picked name rather than every
     name pooled — a dollar total across twenty-nine names is a portfolio
     nobody ran, sized by nothing. */
  function bmDraw() {
    var host = App.$("#bmbody");
    if (App.btView !== "money" || !App.store.weekly || !btLast) { if (host) host.innerHTML = ""; return; }

    var one = null;
    for (var i = 0; i < btLast.names.length; i++) {
      if (btLast.names[i].ticker === bt.ticker) { one = btLast.names[i]; break; }
    }
    if (!one || !one.trades) {
      host.innerHTML = '<p class="empty">' + App.esc(bt.ticker) +
        " has no finished trade on these settings, so there is nothing to price.</p>";
      return;
    }

    var econ = SpreadTrial.economics(one, bmDeal());
    if (econ.why) {
      host.innerHTML = '<div class="notice">Nothing to price — ' + App.esc(econ.why) + ".</div>";
      return;
    }
    host.innerHTML = "<h2>" + App.esc(bt.ticker) + " — priced as " +
      (bmSingle() ? "a single option" : "a debit spread") + "</h2>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Every finished trade the ' +
      "rule took on " + App.esc(bt.ticker) + ", each priced off its own entry — strikes are " +
      "percentages of it, for the same reason the target is. Pick another name in the " +
      "ranking above to price that one instead.</p>" +
      bmTiles(econ) + bmTable(econ);
  }

  function wireMoney() {
    function onNum(id, key) {
      App.$(id).addEventListener("input", function () {
        bm[key] = bmClamp(key, this.value);
        bmStore();
        bmDraw();
      });
    }
    onNum("#bm-long", "long");
    onNum("#bm-short", "short");
    onNum("#bm-debit", "debit");
    onNum("#bm-contracts", "contracts");

    var structures = document.querySelectorAll("#bm-structure button");
    for (var s = 0; s < structures.length; s++) {
      structures[s].addEventListener("click", function () {
        bm.structure = this.dataset.structure === "single" ? "single" : "spread";
        bmStore();
        bmApplyStructure();
        bmDraw();
      });
    }

    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("backtest-money") || "null"); } catch (e) { saved = null; }
    if (saved) {
      for (var key in bm) {
        if (!App.has(saved[key])) continue;
        bm[key] = key === "structure" ? (saved[key] === "single" ? "single" : "spread")
          : bmClamp(key, saved[key]);
      }
    }
    App.$("#bm-long").value = bm.long;
    App.$("#bm-short").value = bm.short;
    App.$("#bm-debit").value = bm.debit;
    App.$("#bm-contracts").value = bm.contracts;
    bmApplyStructure();
  }

  /* ------------------------------------ the best strategy for each name
   *
   * Three hundred combinations per name, and a winner for every one of them.
   * Left there this would be the most dishonest thing on the page — search
   * hard enough against ten years of one stock and something always wins.
   *
   * So the crown is never the headline. The search runs on the older part of
   * the history, and what this leads with is what the winner went on to do on
   * the part it never saw, next to what an *ordinary* combination did on the
   * same stretch. On the data as it stands that comparison is brutal, and it
   * is supposed to be: the page's job here is to show you the answer and why
   * not to trust it, in that order.
   */


  var bsSort = { key: "test", dir: -1 };
  var bsCache = { key: null, data: null, value: null };

  // Keyed on what the search actually reads. The rule, lookback, hold and
  // direction are all swept, so moving those dials does not invalidate it.
  //
  // The payload is part of the key. `load` only ever writes store.weekly once,
  // so today a differing `d` cannot happen — but a key that does not name its
  // own input is a key you have to go and prove, and the cost of holding the
  // reference is one word against silently serving numbers from another
  // dataset if that ever stops being true.
  function bsRun(d) {
    var key = JSON.stringify([bt.target, bt.overlap]);
    if (bsCache.key !== key || bsCache.data !== d) {
      bsCache = { key: key, data: d,
                  value: SpreadBacktest.bestAll(d, { target: bt.target,
                                                     overlap: bt.overlap }) };
    }
    return bsCache.value;
  }

  function bsCombo(c) {
    var r = SpreadBacktest.RULES[c.rule] || {};
    return App.esc((r.label || c.rule).split(" — ")[0]) + " " + c.look + "w, held " + c.hold +
      "w, " + (c.dir === "down" ? "down ↓" : "up ↑");
  }

  /* The verdict, and it is not the crown.
   *
   * A search that works picks settings that go on beating their own baseline
   * far more often than settings picked blind would (`random_held_pct`: on
   * names that mostly rose, that is not a coin's 50%). One that is fitting
   * noise picks settings that hold up about as often as random ones, with a
   * large in-sample edge and nothing left out of sample — and prints that. */
  function bsVerdict(all) {
    function tile(cls, k, v) {
      return '<div class="rule ' + cls + '"><span class="k">' + k + '</span><div class="v">' + v +
        "</div></div>";
    }
    var pct = all.held_pct, rand = all.random_held_pct;
    // Better than settings picked at random, by a margin worth the name. Under
    // that, the search is an expensive way to generate noise and says so.
    var works = App.has(pct) && (App.has(rand) ? pct >= rand + 15 : pct >= 65);
    var decay = App.has(all.median_train) && App.has(all.median_test)
      ? all.median_train - all.median_test : null;

    return '<div class="rulebar">' +
      tile(works ? "cheap" : "rich",
           "Held up out of sample — " + all.held + " of " + all.rated +
             (App.has(pct) ? " (" + App.num(pct, 0) + "%)" : ""),
           "The settings the search crowned on the older history, then measured on the " +
           "years it never saw: this many both <b>made money</b> and <b>beat simply holding " +
           "the name</b> over the same weeks. Either test alone is cheap — a short that loses " +
           "less than other shorts passes the first, and any long on a name that rose passes " +
           "the second. " +
           (App.has(rand)
             ? "A setting picked at random, from the same list, held up " + App.num(rand, 0) +
               "% of the time — that is the bar, not a coin. "
             : "") +
           (works
             ? "The picks clear it by enough to be worth something — but read the decay beside "
               + "it before believing any single row."
             : "<b>That is about what picking at random does.</b> Choosing a strategy per name, "
               + "on this much history, mostly finds what already happened rather than what is "
               + "going to.")) +
      tile("rich", "Against simply holding — " + points(all.median_train, 0) + " → " +
             points(all.median_test, 0),
           "The yardstick is the alternative anyone actually had: buying the name and keeping " +
           "it for the same number of weeks. This is how many percentage points of return the " +
           "median pick beat that by, while it was being searched and afterwards. " +
           (App.has(decay) && decay > 0
             ? "<b>" + App.esc(points(decay, 0)) + " of it was not there once the holdout started.</b> "
             : "") +
           "That gap is the cost of searching: most of what a search finds is the search.") +
      tile("fair", "What they returned — " + App.signed(all.median_return),
           "The median pick's own return out of sample, before any comparison. A strategy has " +
           "to clear two different bars and they are not the same one: this number says it made " +
           "money, the one beside it says it was worth the trouble. A rule can do either without " +
           "the other.") +
      tile("fair", "Best that was available — " + points(all.median_hindsight, 0),
           "The best edge the holdout actually contained, per name — what you would have " +
           "picked knowing the answer. There <i>were</i> edges out there; the search just " +
           "could not tell in advance which. The distance from the middle number to this one " +
           "is the part it missed.") +
      tile("fair", "Searched — " + App.num(all.tried, 0) + " per name",
           "Every rule at every lookback, hold and direction, on " + App.esc(all.names.length) +
           " names. Split at " + App.esc(all.train_to || "—") + ": searched on " +
           App.esc(all.train_from || "—") + "–" + App.esc(all.train_to || "—") + ", reported on " +
           App.esc(all.test_from || "—") + "–" + App.esc(all.test_to || "—") + ". A combination needs " +
           SpreadBacktest.SEARCH_FLOOR + " finished trades on <i>both</i> to be ranked, and " +
           "has to have made money on the first to be crowned at all.") +
      "</div>";
  }

  function bsTable(all) {
    var rows = all.names.filter(function (n) { return n.pick; });
    if (!rows.length) return '<p class="empty">No name had a combination with enough ' +
      "finished trades on both halves of the history to be ranked.</p>";

    var key = bsSort.key;
    function field(n, k) {
      return k === "train" ? n.pick.train.ret
           : k === "test" ? n.pick.test.ret
           : k === "returned" ? n.pick.test.exit
           : k === "hindsight" ? n.hindsight.test.ret : n.pick.test.trades;
    }
    rows.sort(function (a, b) {
      if (key === "ticker") return a.ticker.localeCompare(b.ticker) * bsSort.dir;
      return (field(a, key) - field(b, key)) * bsSort.dir;
    });

    function th(k, label, cls) {
      return App.sortableTh(k, label, cls || "",
        bsSort.key === k ? (bsSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    var body = rows.map(function (n) {
      var p = n.pick;
      return '<tr class="srow" data-ticker="' + App.esc(n.ticker) + '" data-rule="' + App.esc(p.rule) +
        '" data-look="' + p.look + '" data-hold="' + p.hold + '" data-dir="' + App.esc(p.dir) +
        '" tabindex="0" title="' + App.esc("put " + n.ticker + "'s pick into the controls above") +
        '">' +
        '<td class="t">' + App.esc(n.ticker) + "</td>" +
        "<td>" + bsCombo(p) + "</td>" +
        '<td class="r soft-hit">' + points(p.train.ret, 0) + "</td>" +
        '<td class="r ' + (p.test.ret > 0 ? "hit" : "miss") + '"><b>' +
          points(p.test.ret, 0) + "</b></td>" +
        '<td class="r ' + (p.test.exit > 0 ? "hit" : "miss") + '">' +
          App.signed(p.test.exit) + "</td>" +
        '<td class="r faint">' + points(n.hindsight.test.ret, 0) + "</td>" +
        '<td class="r">' + App.num(p.test.trades, 0) + "</td>" +
        '<td class="' + (n.held_up ? "hit" : "miss") + '">' +
          (n.held_up ? "held up" : "did not") + "</td></tr>";
    }).join("");

    return "<h2>The pick for each name</h2>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">The best combination found on ' +
      "the older history, and what it did afterwards. <b>Out</b> is the column that matters — " +
      "<b>found</b> is what the search saw while it was choosing, and it is the number a tool " +
      "without a holdout would have shown you on its own. Both are measured against holding " +
      "the name for the same weeks, which is what makes a long and a short comparable at all; " +
      "<b>returned</b> is the pick's own result, with nothing subtracted. " +
      "Click a row to put that name and its settings into the controls above.</p>" +
      '<div class="tablewrap"><table class="scan rank"><thead><tr>' +
      th("ticker", "Name") + '<th>Pick</th>' + th("train", "Found", "r") +
      th("test", "Out", "r") + th("returned", "Returned", "r") +
      th("hindsight", "Best available", "r") +
      th("trades", "Trades out", "r") + "<th>Verdict</th>" +
      "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      '<p class="faint" style="font-size:.83rem;margin:10px 0 0">A row that held up is not a ' +
      "strategy for that name. It is one combination out of " + App.num(all.tried, 0) + " that " +
      "survived one holdout on one stock, and these names move together besides — a good few " +
      "years for the market floats most of the column at once. Treat the table as a ranking of " +
      "<i>hypotheses to go and test properly</i>, never as a list of trades.</p>";
  }

  function bsAdopt(el) {
    bt.ticker = el.dataset.ticker;
    bt.rule = SpreadBacktest.RULES[el.dataset.rule] ? el.dataset.rule : bt.rule;
    bt.look = btClamp("look", el.dataset.look);
    bt.hold = btClamp("hold", el.dataset.hold);
    bt.dir = el.dataset.dir === "down" ? "down" : "up";
    App.$("#bt-ticker").value = bt.ticker;
    App.$("#bt-rule").value = bt.rule;
    App.$("#bt-look").value = bt.look;
    App.$("#bt-hold").value = bt.hold;
    var dirs = document.querySelectorAll("#bt-dir button");
    for (var i = 0; i < dirs.length; i++) {
      dirs[i].setAttribute("aria-pressed", dirs[i].dataset.dir === bt.dir ? "true" : "false");
    }
    btStore();
    App.writeHash();
    btApplyRule();
    btDraw();
    App.$("#btcontrols").scrollIntoView({ block: "nearest" });
  }

  function bsDraw() {
    var host = App.$("#bsbody");
    if (App.btView !== "search" || !App.store.weekly) { if (host) host.innerHTML = ""; return; }
    if (!App.store.weekly.series.length) { host.innerHTML = ""; return; }

    var all = bsRun(App.store.weekly);
    host.innerHTML =
      '<div class="lede">Every rule, at every lookback, hold and direction — ' +
      App.num(all.tried, 0) + " combinations for each of " + all.names.length + " names — " +
      "searched on the <b>older</b> part of the history. What is reported is what each " +
      "winner then did on the <b>rest</b>, which the search never saw. That split is the " +
      "whole point: a search always finds a winner, and the only question worth asking is " +
      "whether the winner was still one afterwards. It is the same train/holdout split " +
      "<code>calibrate.py</code> fits the Setup Score's weights on.</div>" +
      bsVerdict(all) + bsTable(all);

    App.wireSort(host.querySelectorAll("table.scan.rank thead th"), function (k) {
      if (bsSort.key === k) bsSort.dir = -bsSort.dir;
      else { bsSort.key = k; bsSort.dir = k === "ticker" ? 1 : -1; }
      bsDraw();
      var again = host.querySelector('table.scan.rank thead th[data-key="' + k + '"]');
      if (again) again.focus();
    });

    var picks = host.querySelectorAll("tr.srow[data-ticker]");
    for (var p = 0; p < picks.length; p++) {
      picks[p].addEventListener("click", function () { bsAdopt(this); });
      picks[p].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
          e.preventDefault();
          bsAdopt(this);
        }
      });
    }
  }

  function btPick(ticker) {
    bt.ticker = ticker;
    App.$("#bt-ticker").value = ticker;
    btStore();
    App.writeHash();
    btDraw();
  }

  // The lookback means something different for every rule, and for the baseline
  // it means nothing at all — so the label says which, and the control goes away
  // where it would be answering a question nobody asked.
  function btApplyRule() {
    var r = SpreadBacktest.RULES[bt.rule] || {};
    var ctl = App.$("#bt-look-ctl");
    if (ctl) ctl.hidden = !r.look;
    var label = App.$("#bt-look-label");
    if (label && r.look) {
      label.textContent = r.look.charAt(0).toUpperCase() + r.look.slice(1) + ", weeks";
    }
    // The second rule's lookback is its own, and named for what it means to
    // *that* rule — the whole reason the two are not one control.
    var also = btFiltered() ? SpreadBacktest.RULES[bt.also] : null;
    var alsoCtl = App.$("#bt-alsolook-ctl");
    if (alsoCtl) alsoCtl.hidden = !(also && also.look);
    var alsoLabel = App.$("#bt-alsolook-label");
    if (alsoLabel && also && also.look) {
      alsoLabel.textContent = also.look.charAt(0).toUpperCase() + also.look.slice(1) +
        ", weeks";
    }
    var note = App.$("#bt-overlap-note");
    if (note) {
      note.textContent = bt.overlap
        ? "On — every firing counted, including windows that overlap each other. The right "
          + "reading for a survey, the wrong one for a plan."
        : "Off, one position at a time — what you could actually have held.";
    }
  }

  function renderBacktest() {
    App.load("weekly").then(function (d) {
      var select = App.$("#bt-ticker");
      if (!select.options.length) {
        select.innerHTML = d.series.map(function (s) {
          return '<option value="' + App.esc(s.ticker) + '">' + App.esc(s.ticker) + "</option>";
        }).join("");
      }
      // Every render, not only the first. A remembered name that has since
      // dropped out of the screen is not an error — it just is not on this page
      // any more — and neither is one that arrived in a fragment before the
      // payload did, which is the only path that can still get one here.
      var known = d.series.some(function (s) { return s.ticker === bt.ticker; });
      if (!known) bt.ticker = (d.series[0] || {}).ticker || "";
      select.value = bt.ticker;
      // Whatever name it settled on — asked for, remembered or fallen back to —
      // is the one the address bar should say.
      App.writeHash();
      btDraw();
    }).catch(function (e) {
      App.$("#backtestbody").innerHTML = App.loadError(e, "weekly", "python run.py");
    });
  }

  function wireBacktest() {
    function onChange(fn) {
      return function () {
        fn(this);
        btStore();
        btApplyRule();
        // The name is in the fragment, so changing it there moves the URL too.
        // For every other control this is a no-op: writeHash returns early when
        // the fragment already says what is on screen.
        App.writeHash();
        if (App.store.weekly) btDraw();
      };
    }
    App.$("#bt-rule").addEventListener("change", onChange(function (el) {
      bt.rule = SpreadBacktest.RULES[el.value] ? el.value : "every";
    }));
    App.$("#bt-ticker").addEventListener("change", onChange(function (el) { bt.ticker = el.value; }));
    App.$("#bt-look").addEventListener("input", onChange(function (el) {
      bt.look = btClamp("look", el.value);
    }));
    App.$("#bt-also").addEventListener("change", onChange(function (el) {
      bt.also = SpreadBacktest.RULES[el.value] ? el.value : "every";
    }));
    App.$("#bt-alsolook").addEventListener("input", onChange(function (el) {
      bt.alsoLook = btClamp("alsoLook", el.value);
    }));
    App.$("#bt-hold").addEventListener("input", onChange(function (el) {
      bt.hold = btClamp("hold", el.value);
    }));
    App.$("#bt-years").addEventListener("input", onChange(function (el) {
      bt.years = btClamp("years", el.value);
    }));
    App.$("#bt-target").addEventListener("input", onChange(function (el) {
      bt.target = btClamp("target", el.value);
    }));
    App.$("#bt-overlap").addEventListener("change", onChange(function (el) {
      bt.overlap = !!el.checked;
    }));

    var dirs = document.querySelectorAll("#bt-dir button");
    for (var i = 0; i < dirs.length; i++) {
      dirs[i].addEventListener("click", function () {
        bt.dir = this.dataset.dir === "down" ? "down" : "up";
        for (var j = 0; j < dirs.length; j++) {
          dirs[j].setAttribute("aria-pressed", dirs[j].dataset.dir === bt.dir ? "true" : "false");
        }
        btStore();
        if (App.store.weekly) btDraw();
      });
    }

    // The rule list is built from backtest.js, so the labels cannot drift from
    // the rules they name.
    App.$("#bt-rule").innerHTML = SpreadBacktest.RULE_KEYS.map(function (k) {
      return '<option value="' + App.esc(k) + '">' + App.esc(SpreadBacktest.RULES[k].label) + "</option>";
    }).join("");
    // The second list is the same rules, with "every" reading as the no-op it
    // is: a filter that passes every week is no filter.
    App.$("#bt-also").innerHTML = SpreadBacktest.RULE_KEYS.map(function (k) {
      return '<option value="' + App.esc(k) + '">' +
        (k === "every" ? "— nothing" : App.esc(SpreadBacktest.RULES[k].label)) + "</option>";
    }).join("");

    // Whatever was set last time, clamped on the way in: localStorage is input,
    // not state, and it is treated as input.
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("backtest") || "null"); } catch (e) { saved = null; }
    if (saved) {
      for (var key in bt) {
        if (!App.has(saved[key])) continue;
        bt[key] = key === "overlap" ? !!saved[key]
          : (key === "rule" || key === "also")
            ? (SpreadBacktest.RULES[saved[key]] ? saved[key] : bt[key])
          : key === "dir" ? (saved[key] === "down" ? "down" : "up")
          : key === "ticker" ? String(saved[key])
          : btClamp(key, saved[key]);
      }
    }
    App.$("#bt-rule").value = bt.rule;
    App.$("#bt-look").value = bt.look;
    App.$("#bt-also").value = bt.also;
    App.$("#bt-alsolook").value = bt.alsoLook;
    App.$("#bt-hold").value = bt.hold;
    App.$("#bt-target").value = bt.target;
    App.$("#bt-years").value = bt.years;
    App.$("#bt-overlap").checked = bt.overlap;
    for (var k = 0; k < dirs.length; k++) {
      dirs[k].setAttribute("aria-pressed", dirs[k].dataset.dir === bt.dir ? "true" : "false");
    }
    btApplyRule();
  }
})(window.SpreadApp = window.SpreadApp || {});
