/* Spread Scanner — frontend: the Repeat test tab (same trade, same week, every year) and its money section.
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.rpStore = rpStore;
  App.signed = signed;
  App.rpStale = rpStale;
  App.wireSpread = wireSpread;
  App.renderRepeat = renderRepeat;
  App.wireRepeat = wireRepeat;

  // -------------------------------------------------------- repeat test
  //
  // "Buy in week 37 every year, hold it eight weeks — how many of those years
  // *closed* at least 8% up?" The trial runs here, in the browser, because every
  // control re-runs it and there is no server on Pages to re-run it on.
  //
  // The verdict is the exit: where the window closed, not the best price it saw
  // inside it. Touching is reported next to it — green in the table, because it
  // is worth knowing you could have taken profit early — but a year that
  // touched and then closed back under the target reads red at the exit and
  // counts as a miss.
  //
  // What the backend owns is the half that is quietly easy to get wrong, and it
  // arrives in weekly.json already done: whole ISO weeks only, the week in
  // progress dropped, and one shared gapless axis shared by every name — so
  // "eight weeks later" is eight positions later and never eight *rows* that
  // span a hole. The rules that payload is read by ship inside it, under
  // `reference`, and are printed at the foot of this tab rather than restated
  // here, for the same reason the glossary lives in scan.json.

  var rp = { ticker: "", dir: "up", week: 37, hold: 8, target: 8, years: 10 }; App.rp = rp;
  var rpAt = null;                          // "2025-W37" -> position on the axis
  var rpSort = { key: "rate", dir: -1 };

  // The optional money section. `dir` is not in here: a debit spread is a call
  // spread going up and a put spread going down, and that is the same question
  // the direction chip already answers — two controls for one fact would let
  // them disagree. `structure` is "spread" for a vertical or "single" for one
  // leg alone, held to expiry with nothing sold against it.
  var sp = { on: false, structure: "spread", long: 0, short: 8, debit: 40, contracts: 1 };
  var spSort = { key: "net", dir: -1 };

  /* One range per numeric control, declared once. Two paths write into `sp` —
     the input handlers and the localStorage restore — and only the first of
     them used to clamp, so a stale or hand-edited `repeat-spread` carrying
     `debit: 0` went straight into the arithmetic. Neither path gets to hold its
     own copy of the bounds now; the markup's min/max are the same numbers, and
     a control that disagreed with this table would be the bug this fixes. */
  var SP_RANGE = {
    long:      { lo: -50, hi: 100, fallback: 0 },
    short:     { lo: -50, hi: 200, fallback: 8 },
    // Neither end is a spread: at 0% of width the structure is free, at 100%
    // you have paid the whole of what it can ever be worth.
    debit:     { lo: 1, hi: 99, fallback: 40 },
    contracts: { lo: 1, hi: 1000, fallback: 1, whole: true }
  };

  /* A value forced into its control's range. Not rounded unless the control
     says to: these step in halves, and a 2.5% strike offset that silently
     became 3% would be a lie on the table. Keys with no range — `on`, which is
     a checkbox — come back untouched. */
  function spClamp(key, value) {
    var r = SP_RANGE[key];
    if (!r) return value;
    var v = Number(value);
    if (value === "" || value === null || isNaN(v)) v = r.fallback;
    v = Math.min(r.hi, Math.max(r.lo, v));
    return r.whole ? Math.round(v) : v;
  }

  function spDeal() {
    return { dir: rp.dir, structure: sp.structure, long: sp.long, short: sp.short,
             debit: sp.debit, contracts: sp.contracts };
  }

  // Whether the current structure is one leg alone rather than a vertical.
  function spSingle() { return sp.structure === "single"; }

  function rpStore() {
    try { localStorage.setItem("repeat", JSON.stringify(rp)); } catch (e) { /* private mode */ }
  }

  function spStore() {
    try { localStorage.setItem("repeat-spread", JSON.stringify(sp)); } catch (e) { /* private */ }
  }

  function signed(v, digits) {
    if (!App.has(v) || isNaN(v)) return "—";
    return (v >= 0 ? "+" : "") + App.num(v, digits === undefined ? 1 : digits) + "%";
  }

  /* The target as a signed move on the price, which is the form every label
     wants — and the form that stays right when the target is zero or negative.

     It is allowed to be. An in-the-money vertical is already past its breakeven
     at today's price, so the honest question for one is not "how far did it
     travel" but "did it hold up": −3% over eight weeks asks how many years
     finished no worse than 3% down, which is exactly what that spread needs.
     Going down, the sign flips — a downside target of −3% is a level 3% *above*
     the entry that the name has to close under. */
  function rpMove() { return rp.dir === "up" ? rp.target : -rp.target; }

  function rpGoal() {
    var m = rpMove();
    return (m > 0 ? "+" : m < 0 ? "−" : "") + App.num(Math.abs(m), 1) + "%";
  }

  // "or better" for an upside trade, "or lower" for a downside one — the side
  // of the target a year has to finish on to count.
  function rpSide() { return rp.dir === "up" ? " or better" : " or lower"; }

  // The counting itself lives in assets/trial.js, on its own so it can be run
  // under node by tests/test_trial.py — what closing past the target, a miss, a
  // still-open year and a skipped one mean is the whole point of this tab, and
  // it was the one part of it nothing could check.
  function rpTrial(d, s, week) {
    if (week === undefined) return SpreadTrial.run(d, s, rp, rpAt);
    // A one-off run on a different buy week, for the sweep below. Copied rather
    // than assigned onto rp and put back: a throw in the middle of 53 of those
    // would leave the control and the state disagreeing.
    return SpreadTrial.run(d, s, { dir: rp.dir, week: week, hold: rp.hold,
                                   target: rp.target, years: rp.years }, rpAt);
  }

  // ---- rendering

  function rpTiles(t) {
    var up = rp.dir === "up";
    var goal = rpGoal();
    // A target at or behind the entry — an in-the-money thesis — starts on the
    // right side of the line, so touching it is close to automatic and carries
    // no information. Said out loud rather than left to look like a 100% record.
    var beyond = rp.target > 0;
    function tile(cls, k, v) {
      return '<div class="rule ' + cls + '"><span class="k">' + k + '</span><div class="v">' + v +
        "</div></div>";
    }
    var verdict = t.rate === null ? "fair" : t.rate >= 50 ? "cheap" : "rich";
    function years(n) {
      return n + " of " + t.decided + (t.decided === 1 ? " year (" : " years (") +
        App.num((n / t.decided) * 100, 0) + "%)";
    }
    var ended = t.decided ? years(t.hit) : "no year could be judged";
    var brushed = t.decided ? years(t.touched) : "no window has finished yet";

    return '<div class="rulebar">' +
      tile(verdict, "Closed " + goal + rpSide() + " — " + ended,
           t.decided
             ? "Where the " + rp.hold + "-week window actually closed, measured against the entry. "
               + "This is the verdict: " + goal + " over " + rp.hold + " weeks asks whether the "
               + "name is there at the end of week " + rp.hold + ", not whether it got there on "
               + "the way. A vertical settles against this close."
             : "Nothing in the window could be scored — widen the years, or pick a week the "
               + "history covers.") +
      tile("fair", "Touched it on the way — " + brushed,
           beyond
             ? "The looser test: the " + (up ? "high" : "low") + " reached " + goal +
               " at some point inside the window" +
               (t.median_weeks ? ", typically in week " + App.num(t.median_weeks, 0) + " of it" : "") +
               ". It says the price was there, not that you were still in the trade when it was — "
               + "so it only pays if you take profit early, and it is never the smaller number."
             : "With the target at or behind the entry, the trade opens on the right side of it, "
               + "so touching is close to automatic and says nothing. On an in-the-money thesis "
               + "like this one it is the verdict on the left that carries the whole answer.") +
      tile("fair", "Best it got — " + signed(t.median_best),
           "Median of the furthest each window travelled toward the target. Half the years did "
           + "better than this, half worse.") +
      tile("fair", "Worst it got — " + signed(t.median_worst),
           "Median of the furthest each window went the *other* way. This is the drawdown the "
           + "years that worked still put you through first.") +
      "</div>";
  }

  /* Ten years at a glance. The detail is in the table directly below — the
     tooltips here are a convenience, not the only copy of anything, because a
     tooltip is unreachable on a touch screen. The state is in a glyph as well
     as in the colour for the same reason it is in the table: colour on its own
     is not a channel everyone has (WCAG 1.4.1). */
  function rpStrip(t) {
    if (!t.rows.length) return "";
    var labels = { hit: "closed past the target", miss: "fell short", open: "still open",
                   skipped: "skipped" };
    var marks = { hit: "✓", miss: "✗", open: "•", skipped: "–" };
    return '<div class="yearstrip">' + t.rows.map(function (r) {
      var note = r.state === "skipped" ? (r.why || "no data")
        : r.state === "open" ? "ran " + r.ran + " of " + rp.hold + " weeks so far, " +
                               signed(r.open_pct) + " as it stands, best " + signed(r.best_pct) +
                               (r.touched ? ", target already touched" : "")
        : "entry " + App.money(r.entry) + ", target " + App.money(r.target) + ", closed " +
          signed(r.exit_pct) + ", best " + signed(r.best_pct) + ", worst " + signed(r.worst_pct) +
          (r.touched ? ", touched in week " + r.hit_in + " on the way" : ", never touched it");
      var say = r.year + " — " + labels[r.state] + ": " + note;
      // The percentage on the block is the one the colour is about: where the
      // window closed, or where an unfinished one stands so far.
      var shown = r.settled ? signed(r.exit_pct)
        : r.state === "open" ? signed(r.open_pct) : "—";
      return '<div class="yr ' + r.state + '" title="' + App.esc(say) + '" aria-label="' + App.esc(say) +
        '"><b>' + r.year + "</b><span>" + marks[r.state] + " " + shown + "</span></div>";
    }).join("") + "</div>";
  }

  function rpYearTable(t) {
    if (!t.rows.length) {
      return '<p class="empty">No year in this history has an ISO week ' + rp.week +
        " to buy in.</p>";
    }
    var labels = { hit: "Closed past", miss: "Fell short", open: "Still open", skipped: "Skipped" };
    var body = t.rows.map(function (r) {
      if (r.state === "skipped") {
        return '<tr class="dim"><td class="t">' + r.year + "</td><td>" + App.esc(r.start) +
          '</td><td colspan="6" class="faint">' + App.esc(r.why || "not enough history") +
          '</td><td class="skipped">Skipped</td></tr>';
      }
      // Exit and Touched are coloured independently, and a year that touched
      // and then closed back under the target is exactly why: the exit reads
      // red (you did not take it, and the exit is what the verdict and a
      // vertical both settle on) while Touched still reads that the profit was
      // there to take. Different claims, so different colours — Touched has its
      // own, off the buy/sell axis, rather than borrowing the verdict's green
      // and quietly saying the same word twice.
      var exit = r.settled
        ? '<td class="r ' + (r.closed_past ? "hit" : "miss") + '">' + signed(r.exit_pct) + "</td>"
        : '<td class="r faint">running</td>';
      var touched = '<td class="r' + (r.touched ? " touch" : "") + '">' +
        (r.touched ? "week " + r.hit_in : "—") + "</td>";
      // An unfinished window that has already touched is said out loud, because
      // it is the one row a reader might expect in a column it is not in.
      var verdict = r.state === "open"
        ? (r.touched ? "Touched, still open" : "Still open") + " (" + r.ran + "/" + rp.hold + "w)"
        : labels[r.state];
      return "<tr><td class=\"t\">" + r.year + "</td><td>" + App.esc(r.start) + "</td>" +
        '<td class="r" title="' + App.esc("the last close of the week beginning " + r.entry_week +
          " — you buy as week " + rp.week + " opens") + '">' + App.money(r.entry) + "</td>" +
        '<td class="r">' + App.money(r.target) + "</td>" + exit +
        '<td class="r">' + signed(r.best_pct) + "</td>" + touched +
        '<td class="r">' + signed(r.worst_pct) + "</td>" +
        '<td class="' + r.state + '">' + verdict + "</td></tr>";
    }).join("");

    // The bottom line: the two counts this whole tab exists to tell apart, added
    // up under the columns they came from. The tiles say it in prose; a reader
    // who has just scanned a column wants it at the foot of that column. Only
    // the judged years are in it — an open or skipped year is in neither count,
    // so the cell beside the total says how many were set aside rather than
    // letting the reader work it out from a total that does not match the rows.
    var unjudged = t.open + t.skipped;
    var foot = !t.decided ? "" : "<tfoot><tr>" +
      '<td class="t">' + t.decided + " judged year" + (t.decided === 1 ? "" : "s") + "</td>" +
      '<td class="faint">' + (unjudged ? unjudged + " set aside" : "") + "</td>" +
      "<td></td><td></td>" +
      '<td class="r ' + (t.rate >= 50 ? "hit" : "miss") + '">' + t.hit + " of " + t.decided +
        "</td>" +
      '<td class="r">' + signed(t.median_best) + "</td>" +
      '<td class="r' + (t.touched ? " touch" : "") + '">' + t.touched + " of " + t.decided +
        "</td>" +
      '<td class="r">' + signed(t.median_worst) + "</td>" +
      "<td>" + App.num(t.rate, 0) + "% closed · " + App.num(t.touch_rate, 0) + "% touched</td>" +
      "</tr></tfoot>";

    return '<div class="tablewrap"><table class="scan trial"><thead><tr>' +
      "<th>Year</th><th>Buy week</th><th class=\"r\">Entry</th><th class=\"r\">Target</th>" +
      '<th class="r">At exit</th><th class="r">Best</th><th class="r">Touched</th>' +
      '<th class="r">Worst</th><th>Result</th>' +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>";
  }

  /* The same settings run across every name — the reason to keep this on one
     screen is that a 60% record means nothing until you can see whether the
     other twenty-eight names did 30% or 80% on the same question. */
  function rpAllTable(d) {
    var floor = d.min_years || 3;
    var rows = d.series.map(function (s) {
      var t = rpTrial(d, s);
      return { ticker: s.ticker, hit: t.hit, decided: t.decided, rate: t.rate,
               touched: t.touched, best: t.median_best, worst: t.median_worst,
               thin: t.decided < floor };
    }).filter(function (r) { return r.decided > 0; });
    if (!rows.length) return "";

    var key = rpSort.key;
    rows.sort(function (a, b) {
      // Sorting by Name is a request for A–Z, so the floor does not apply: it
      // exists to stop a short history *topping a ranking*, and an alphabetical
      // list is not one. The greying still marks them.
      if (key === "ticker") return a.ticker.localeCompare(b.ticker) * rpSort.dir;
      // Everywhere else, names with too few judged years are reported but never
      // ranked — sorted by rate, two years at 100% would otherwise sit above ten
      // years at 70% and read as the best name on the screen. Same floor, same
      // reason, as the greyed months in the Seasonality view.
      if (a.thin !== b.thin) return a.thin ? 1 : -1;
      var x = a[key], y = b[key];
      x = App.has(x) ? x : -Infinity; y = App.has(y) ? y : -Infinity;
      // Equal rates fall back to how many years stand behind them.
      return (x === y ? a.decided - b.decided : x - y) * rpSort.dir;
    });

    function th(k, label, cls) {
      return App.sortableTh(k, label, cls || "",
        rpSort.key === k ? (rpSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    // The same green and red the year table uses, so one meaning carries down
    // both: the verdict columns at full strength, the two excursion columns
    // muted so they cannot outshout it. Green on Median best and red on Median
    // worst are about the *direction*, not the sign — going down, the best a
    // window got is a fall. A zero is left uncoloured; a green 0 would be
    // saying "good" about nothing having happened. The colour is never the only
    // channel — every cell it lands on is a number that already says it.
    var body = rows.map(function (r) {
      return '<tr class="srow' + (r.thin ? " thin" : "") +
        (r.ticker === rp.ticker ? " picked" : "") + '" data-ticker="' + App.esc(r.ticker) +
        '" tabindex="0" title="' + App.esc(r.thin
          ? r.ticker + " has only " + r.decided + " judged year" + (r.decided === 1 ? "" : "s") +
            " here — shown, but not ranked"
          : "show " + r.ticker + " above") + '">' +
        '<td class="t">' + App.esc(r.ticker) + "</td>" +
        '<td class="r">' + r.decided + "</td>" +
        '<td class="r' + (r.hit ? " hit" : "") + '">' + r.hit + "</td>" +
        '<td class="r' + (r.decided - r.hit ? " miss" : "") + '">' + (r.decided - r.hit) + "</td>" +
        '<td class="r ' + (r.rate >= 50 ? "hit" : "miss") + '"><b>' + App.num(r.rate, 0) + "%</b></td>" +
        '<td class="r' + (r.touched ? " touch" : "") + '">' +
          (r.decided ? App.num((r.touched / r.decided) * 100, 0) + "%" : "—") + "</td>" +
        '<td class="r soft-hit">' + signed(r.best) + "</td>" +
        '<td class="r soft-miss">' + signed(r.worst) + "</td></tr>";
    }).join("");

    // Pooled across every name shown, the short histories included: pooling has
    // none of the problem the ranking floor exists to stop — a name with two
    // judged years puts two years into the denominator, not a 100% record at the
    // top of a list. What it does have is the correlation problem, which gets
    // worse the bigger the number looks, so it is said under the table.
    var sum = rows.reduce(function (a, r) {
      a.decided += r.decided; a.hit += r.hit; a.touched += r.touched; return a;
    }, { decided: 0, hit: 0, touched: 0 });
    var pooled = sum.decided ? (sum.hit / sum.decided) * 100 : null;
    var pooledTouch = sum.decided ? (sum.touched / sum.decided) * 100 : null;
    var medianNote = "no total: a median of medians is not a median. The per-name figures are "
      + "in the column above.";
    var foot = "<tfoot><tr>" +
      '<td class="t">' + rows.length + " name" + (rows.length === 1 ? "" : "s") + "</td>" +
      '<td class="r">' + sum.decided + "</td>" +
      '<td class="r' + (sum.hit ? " hit" : "") + '">' + sum.hit + "</td>" +
      '<td class="r' + (sum.decided - sum.hit ? " miss" : "") + '">' + (sum.decided - sum.hit) +
        "</td>" +
      '<td class="r ' + (pooled >= 50 ? "hit" : "miss") + '">' + App.num(pooled, 0) + "%</td>" +
      '<td class="r' + (sum.touched ? " touch" : "") + '">' + App.num(pooledTouch, 0) + "%</td>" +
      '<td class="r faint" title="' + App.esc(medianNote) + '">—</td>' +
      '<td class="r faint" title="' + App.esc(medianNote) + '">—</td>' +
      "</tr></tfoot>";

    return "<h2>The same question, every name</h2>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Week ' + rp.week + ", " + rp.hold +
      " weeks, " + rpGoal() + rpSide() +
      " by the close of the last week, run across the whole screened list. Click a row to bring " +
      "that name up above. Names with fewer than " + floor + " judged years sit at the bottom, " +
      "greyed: they are reported, never ranked. Green is the target met at the close, red is not, " +
      "the blue column is the looser <i>touched</i> test that decides nothing, and the muted " +
      "pair on the right is how far each window travelled either way. And these " +
      "names move together, so " + rows.length + " of them agreeing is nearer one piece of " +
      "evidence than " + rows.length + ".</p>" +
      '<div class="tablewrap"><table class="scan rank"><thead><tr>' +
      th("ticker", "Name") + th("decided", "Years", "r") + th("hit", "Closed", "r") +
      '<th class="r">Fell short</th>' + th("rate", "Closed %", "r") +
      '<th class="r">Touched %</th>' + th("best", "Median best", "r") +
      th("worst", "Median worst", "r") +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>" +
      '<p class="faint" style="font-size:.83rem;margin:8px 0 0">' + sum.decided +
      " judged years stand behind that bottom line, and they are not " + sum.decided +
      " independent ones — these names move together, so a year that was good for the market was " +
      "good for most of the list at once. Read it as one broad answer to the question, not as " +
      App.esc(String(sum.decided)) + " separate ones.</p>";
  }

  // The rules this tab is read by, in the order they are printed, and the one
  // that belongs under the money tables instead — `strikes` is only about a
  // strike-settled payout, so it is noise on a tab where the money section is
  // switched off.
  var RP_RULES = ["entry", "window", "result", "touch", "incomplete", "prices"];
  var SP_RULES = ["strikes"];

  /* The bullets the payload has, and — separately — the names of the ones it
     does not. Both halves are returned because both get rendered: see
     rpMissing below for why the second is not simply dropped. */
  function rpRules(d, keys) {
    var r = (d && d.reference) || {};
    var out = { html: "", missing: [] };
    var have = [];
    for (var i = 0; i < keys.length; i++) {
      if (r[keys[i]]) have.push("<li>" + App.esc(r[keys[i]]) + "</li>");
      else out.missing.push(keys[i]);
    }
    out.html = have.join("");
    return out;
  }

  /* The payload is a shape this page does not understand.

     scan.json has always been checked for its major version and weekly.json
     never was, which is the more serious omission of the two: scan.json is
     mostly labelled values, and this one is arrays walked by position. Loud,
     and at the top, because nothing below it can be trusted to mean what it
     says. A missing *rule* is a different and much smaller thing — it is said
     at the foot of the tab instead, with the rules. */
  function rpStale(d) {
    var v = d && d.schema_version;
    var wrong = !v
      ? "It carries no schema version at all."
      : String(v).split(".")[0] !== App.SCHEMA_MAJOR
        ? "It declares schema " + v + ", and this page reads " + App.SCHEMA_MAJOR + ".x."
        : "";
    if (!wrong) return "";
    return '<div class="notice"><b>This history is not the shape this page was written for.</b> ' +
      App.esc(wrong) + " Everything below is still computed from the closes in it, but which column " +
      "is which rests on a layout that may have moved. A hard reload picks up the current file; " +
      "if that changes nothing, the scan that writes it has not run since the page did.</div>";
  }

  /* Which definitions this payload does not carry, named rather than dropped.

     `.filter(r[k])` is a nicety everywhere else on this page and a bug here.
     These bullets *are* the rules, they are renamed far more often than the
     schema version is bumped — `hit` and `finish` became `result` and `touch`
     one release ago — and a list printed two bullets short reads exactly like a
     complete one. Said quietly and in place, though: an additive rule that a
     day-old payload has not caught up with is a gap in the prose, not a reason
     to distrust the numbers. */
  function rpMissing(d) {
    var gone = rpRules(d, RP_RULES).missing.concat(rpRules(d, SP_RULES).missing);
    if (!gone.length) return "";
    var one = gone.length === 1;
    return '<p class="faint" style="font-size:.83rem;margin-top:10px">This history does not carry ' +
      (one ? "the rule " : "the rules ") + "<code>" + gone.map(App.esc).join("</code>, <code>") +
      "</code>, so " + (one ? "that definition is" : "those definitions are") + " absent from the " +
      "list above rather than inapplicable to it — it was written by an older scan than this " +
      "page. The next scan restores " + (one ? "it" : "them") + ".</p>";
  }

  function rpCaveats(d) {
    var rules = rpRules(d, RP_RULES).html;
    return '<div class="panelcard" style="margin-top:18px">' +
      "<h3>What this test does, exactly</h3>" +
      (rules ? "<ul>" + rules + "</ul>" : "") +
      '<p class="dim" style="font-size:.85rem;margin-top:10px"><b>And what it does not.</b> ' +
      "Ten years is ten observations, and this list is whoever passes the screen <i>today</i> — " +
      "the names that would have dragged a week's record down are the ones no longer here to be " +
      "measured. Nothing here knows about earnings dates, which is where a lot of week-shaped " +
      "behaviour comes from. Above all, <b>a stock finishing past your level is not the spread " +
      "paying out</b>: a debit vertical reaches its maximum only at expiry with the name still " +
      "past the short strike, and the Spreads tab is where that is priced. Read a closing rate " +
      "here as the first of those two conditions, not as a backtested return.</p>" +
      rpMissing(d) + "</div>";
  }

  // ---------------------------------------------- which week was the best one
  //
  // The slider asks you to pick one week out of fifty-three with nothing to go
  // on. This runs the trial on every one of them and paints the answer under
  // the control — a band per week, red through grey to green — and then names
  // the best one in words, because a colour is not a number and a strip of 53
  // of them is unreadable on a phone. The words are the accessible copy of it;
  // the strip is the thing you can see at a glance.
  //
  // Memoised on everything *except* the week, since the sweep does not depend on
  // which week is selected: without that, dragging the slider re-ran 53 trials
  // per pixel.
  var rpSweepCache = { key: null, weeks: [], best: null };

  function rpSweep(d, series) {
    var key = [rp.ticker, rp.dir, rp.hold, rp.target, rp.years].join("|");
    if (rpSweepCache.key === key) return rpSweepCache;

    var floor = d.min_years || 3;
    var weeks = [], best = null;
    for (var w = 1; w <= 53; w++) {
      var t = rpTrial(d, series, w);
      var cell = { week: w, rate: t.rate, decided: t.decided, hit: t.hit,
                   exit: t.median_exit, thin: t.decided < floor };
      weeks.push(cell);
      // A week the history barely covers is shown but never crowned — the same
      // floor the ranking uses, for the same reason. ISO week 53 falls in about
      // one year in six, so it would otherwise win the title on three lucky
      // years and send you to buy in the last week of December.
      if (cell.rate === null || cell.thin) continue;
      if (best === null || cell.rate > best.rate ||
          (cell.rate === best.rate && (cell.exit || 0) > (best.exit || 0))) best = cell;
    }
    // What the winner beat, which is the half that makes the winner mean
    // anything. The best of 53 tries is high by construction, so the strip also
    // carries the runner-up and the middle of the rankable weeks: a week that
    // tops the field by twenty points and a week that tops it by two are the
    // same crown and very different evidence.
    var ranked = weeks.filter(function (c) { return c.rate !== null && !c.thin; });
    var second = null;
    for (var j = 0; j < ranked.length; j++) {
      if (ranked[j] === best) continue;
      if (second === null || ranked[j].rate > second.rate) second = ranked[j];
    }
    var middle = SpreadTrial.median(ranked.map(function (c) { return c.rate; }));

    // How often the best of these weeks would be this good by luck alone.
    var chance = best ? SpreadTrial.bestByChance(ranked, 2000, 7) : { p: null };
    rpSweepCache = { key: key, weeks: weeks, best: best, second: second,
                     median: middle, ranked: ranked.length, chance: chance };
    return rpSweepCache;
  }

  /* Red at 0%, grey at 50%, green at 100% — the three colours the tab already
     means those things with. */
  function rpHeatColour(rate) {
    var stops = [[0, 240, 129, 111], [50, 110, 119, 129], [100, 63, 185, 80]];
    var lo = stops[0], hi = stops[stops.length - 1];
    for (var i = 0; i < stops.length - 1; i++) {
      if (rate >= stops[i][0] && rate <= stops[i + 1][0]) { lo = stops[i]; hi = stops[i + 1]; }
    }
    var span = hi[0] - lo[0];
    var f = span ? (rate - lo[0]) / span : 0;
    function mix(a, b) { return Math.round(a + (b - a) * f); }
    return "rgb(" + mix(lo[1], hi[1]) + "," + mix(lo[2], hi[2]) + "," + mix(lo[3], hi[3]) + ")";
  }

  function rpWeekWhen(d, week) {
    var want = "W" + (week < 10 ? "0" : "") + week;
    for (var i = d.weeks.length - 1; i >= 0; i--) {
      if (d.weeks[i].slice(5) !== want) continue;
      var when = new Date(d.starts[i] + "T00:00:00");
      if (isNaN(when.getTime())) return "";
      return when.toLocaleDateString(undefined, { day: "numeric", month: "short" });
    }
    return "";
  }

  function rpHeat(d, series) {
    var host = App.$("#rp-heat");
    if (!host) return;
    if (!series) { host.innerHTML = ""; return; }

    var sweep = rpSweep(d, series);
    var goal = rpGoal() + rpSide();
    // The field the best week won against, in words. There is a button here
    // that adopts the winner in one click, directly under a warning that the
    // best of 53 tries is a high bar to clear by luck — and a warning with no
    // number in it loses that argument to a button every time. This is the
    // number: how far ahead of the runner-up, and of the middle week, the crown
    // actually sits.
    var field = sweep.best && sweep.second
      ? "runner-up week " + sweep.second.week + " at " + App.num(sweep.second.rate, 0) +
        "%, middle of the " + sweep.ranked + " rankable weeks " + App.num(sweep.median, 0) + "%"
      : "";
    var say = sweep.best
      ? "How often each buy week closed " + goal + ", on these settings. Best is week " +
        sweep.best.week + " at " + App.num(sweep.best.rate, 0) + "%" +
        (field ? ", against a " + field + "." : ".")
      : "How often each buy week closed " + goal + ", on these settings.";

    var bars = sweep.weeks.map(function (c) {
      var cls = "hs";
      if (c.week === rp.week) cls += " now";
      if (sweep.best && c.week === sweep.best.week) cls += " top";
      if (c.rate === null) {
        return '<i class="' + cls + ' none" title="' + App.esc("week " + c.week + " — no judged year")
          + '"></i>';
      }
      var note = "week " + c.week + " — " + App.num(c.rate, 0) + "% closed " + goal + ", " +
        c.hit + " of " + c.decided + (c.thin ? " (too few years to rank)" : "");
      return '<i class="' + cls + (c.thin ? " thin" : "") + '" style="background:' +
        rpHeatColour(c.rate) + '" title="' + App.esc(note) + '"></i>';
    }).join("");

    // Below 5%, the best week is unlikely to be luck; above it, it is named as
    // the highest rather than the best, and the note says how often chance did
    // as well.
    var lucky = sweep.chance && App.has(sweep.chance.p) && sweep.chance.p >= 0.05;
    var pointer = "";
    if (sweep.best) {
      var when = rpWeekWhen(d, sweep.best.week);
      pointer = '<button type="button" class="bestweek" data-week="' + sweep.best.week +
        '" title="' + App.esc("set the slider to week " + sweep.best.week +
          (field ? " — the week that won, against a " + field : "")) + '">' +
        (lucky ? "Highest here" : "Best here") + ": <b>week " + sweep.best.week + "</b>" +
        (when ? " · w/c " + App.esc(when) : "") +
        " · " + App.num(sweep.best.rate, 0) + "% (" + sweep.best.hit + " of " + sweep.best.decided +
        ")</button>";
    }

    host.innerHTML = '<div class="heatbar" role="img" aria-label="' + App.esc(say) + '">' + bars +
      "</div>" + pointer +
      '<span class="heatnote">' + (sweep.best
        ? App.esc("best of 53 weeks tried" + (field ? " · " + field : "") +
              (sweep.chance && App.has(sweep.chance.p)
                ? " — with every week at the average rate (" + App.num(sweep.chance.pooled, 0) +
                  "%), luck alone produces a best week this good in " +
                  App.num(sweep.chance.p * 100, 0) + "% of runs" +
                  (lucky ? ", so no week stands out" : "")
                : " — and the best of 53 tries is a high bar to clear by luck"))
        : "no week here has enough judged years to rank") + "</span>";

    var jump = host.querySelector(".bestweek");
    if (jump) {
      jump.addEventListener("click", function () {
        rp.week = Number(this.dataset.week);
        App.$("#rp-week").value = rp.week;
        rpStore();
        rpWeekLabel(d);
        rpDraw();
      });
    }
  }

  // ------------------------------------------------- the money section
  //
  // The same years, priced as a debit vertical. Two tables, deliberately its
  // own: everything above this point is percentages of the stock and needs no
  // assumption beyond the closes, and everything below rests on a debit nobody
  // can look up. Keeping them apart is how a reader can tell which half is
  // measurement and which half is their own input.

  /* One name, year by year — the cash that left and the cash that came back. */
  function spYearTable(econ) {
    if (!econ.rows.length) {
      return '<p class="empty">No year here has a finished window to settle a spread against.</p>';
    }
    var single = spSingle();
    var lots = econ.lots > 1 ? " ×" + econ.lots : "";
    var body = econ.rows.map(function (r) {
      // The two ends a vertical can reach are named where they happen: "max"
      // and "expired worthless" read as outcomes where a bare number reads as
      // arithmetic. A single leg has no cap, so it is never "max".
      var note = r.maxed ? ' <span class="tag buy">max</span>'
        : r.worthless ? ' <span class="tag sell">worthless</span>' : "";
      return '<tr><td class="t">' + r.year + "</td>" +
        '<td class="r">' + App.money(r.entry) + "</td>" +
        '<td class="r">' + App.money(r.long) + (single ? "" : " / " + App.money(r.short)) + "</td>" +
        '<td class="r out">−' + App.cash(r.paid) + "</td>" +
        '<td class="r">' + App.money(r.exit) + " (" + signed(r.exit_pct) + ")</td>" +
        '<td class="r ' + (r.maxed ? "maxed" : r.worthless ? "zero" : "") + '">+' +
          App.cash(r.received) + note + "</td>" +
        '<td class="r net ' + (r.net > 0 ? "up" : r.net < 0 ? "down" : "") + '">' +
          App.cash(r.net) + "</td>" +
        '<td class="r">' + signed(r.roi, 0) + "</td></tr>";
    }).join("");

    var foot = "<tfoot><tr>" +
      '<td class="t">' + econ.years + " year" + (econ.years === 1 ? "" : "s") + lots + "</td>" +
      "<td></td><td></td>" +
      '<td class="r out">−' + App.cash(econ.paid) + "</td>" +
      "<td></td>" +
      '<td class="r">+' + App.cash(econ.received) + "</td>" +
      '<td class="r net ' + (econ.net > 0 ? "up" : econ.net < 0 ? "down" : "") + '">' +
        App.cash(econ.net) + "</td>" +
      '<td class="r">' + signed(econ.roi, 0) + "</td></tr></tfoot>";

    return '<div class="tablewrap"><table class="scan money"><thead><tr>' +
      '<th>Year</th><th class="r">Entry</th><th class="r">' +
      (single ? "Strike" : "Long / short") + "</th>" +
      '<th class="r">Cash out</th><th class="r">Stock at expiry</th>' +
      '<th class="r">Cash in</th><th class="r">Net</th><th class="r">Return</th>' +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>";
  }

  /* One `economics` run per name, memoised.

     spDraw() is called on every control change *and* on every sort click, and
     each call was re-running the whole trial for every name on the screen — a
     ranking that was already computed, thrown away to re-order one column of
     it. Keyed on what the rows are made of: the trial settings and the deal.
     Not the sort, which is applied to the rows afterwards, and not the picked
     name, which is a class on a row rather than a number in it. Same treatment
     rpSweep already gets, for the same reason, and it matters more here — the
     sweep runs one name and this runs the list. */
  var spAllCache = { key: null, rows: [] };

  function spAllRows(d) {
    var key = [rp.dir, rp.week, rp.hold, rp.target, rp.years,
               sp.structure, sp.long, sp.short, sp.debit, sp.contracts].join("|");
    if (spAllCache.key === key) return spAllCache.rows;

    var floor = d.min_years || 3;
    var deal = spDeal();
    var rows = [];
    for (var i = 0; i < d.series.length; i++) {
      var econ = SpreadTrial.economics(rpTrial(d, d.series[i]), deal);
      if (!econ.years) continue;
      rows.push({ ticker: d.series[i].ticker, years: econ.years, paid: econ.paid,
                  received: econ.received, net: econ.net, roi: econ.roi, won: econ.won,
                  breakeven: econ.breakeven, thin: econ.years < floor });
    }
    spAllCache = { key: key, rows: rows };
    return rows;
  }

  /* What the same structure would have done on every other name. */
  function spAllTable(d) {
    // Copied before sorting: the array behind it is the cache, and reordering
    // that in place would leave a later reader holding rows in whatever order
    // the last click on a header wanted them.
    var rows = spAllRows(d).slice();
    if (!rows.length) return "";

    var key = spSort.key;
    rows.sort(function (a, b) {
      if (key === "ticker") return a.ticker.localeCompare(b.ticker) * spSort.dir;
      // Same floor, same reason as the ranking above: a name with two judged
      // years is reported, never ranked. It matters more here, not less — the
      // biggest net on the screen could be one lucky year.
      if (a.thin !== b.thin) return a.thin ? 1 : -1;
      var x = App.has(a[key]) ? a[key] : -Infinity, y = App.has(b[key]) ? b[key] : -Infinity;
      return (x === y ? a.years - b.years : x - y) * spSort.dir;
    });

    function th(k, label, cls) {
      return App.sortableTh(k, label, cls || "",
        spSort.key === k ? (spSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    var sum = rows.reduce(function (a, r) {
      a.paid += r.paid; a.received += r.received; a.years += r.years; a.won += r.won;
      return a;
    }, { paid: 0, received: 0, years: 0, won: 0 });
    var net = sum.received - sum.paid;

    var body = rows.map(function (r) {
      return '<tr class="srow' + (r.thin ? " thin" : "") +
        (r.ticker === rp.ticker ? " picked" : "") + '" data-ticker="' + App.esc(r.ticker) +
        '" tabindex="0" title="' + App.esc(r.thin
          ? r.ticker + " has only " + r.years + " settled year" + (r.years === 1 ? "" : "s") +
            " here — shown, but not ranked"
          : "show " + r.ticker + " above") + '">' +
        '<td class="t">' + App.esc(r.ticker) + "</td>" +
        '<td class="r">' + r.years + "</td>" +
        '<td class="r">' + r.won + "</td>" +
        '<td class="r out">−' + App.cash(r.paid) + "</td>" +
        '<td class="r">+' + App.cash(r.received) + "</td>" +
        '<td class="r net ' + (r.net > 0 ? "up" : r.net < 0 ? "down" : "") + '">' +
          App.cash(r.net) + "</td>" +
        '<td class="r">' + signed(r.roi, 0) + "</td>" +
        '<td class="r">' + App.num(r.breakeven, 0) + "%</td></tr>";
    }).join("");

    var foot = "<tfoot><tr>" +
      '<td class="t">' + rows.length + " name" + (rows.length === 1 ? "" : "s") + "</td>" +
      '<td class="r">' + sum.years + "</td>" +
      '<td class="r">' + sum.won + "</td>" +
      '<td class="r out">−' + App.cash(sum.paid) + "</td>" +
      '<td class="r">+' + App.cash(sum.received) + "</td>" +
      '<td class="r net ' + (net > 0 ? "up" : net < 0 ? "down" : "") + '">' + App.cash(net) + "</td>" +
      '<td class="r">' + signed(sum.paid ? (net / sum.paid) * 100 : null, 0) + "</td>" +
      '<td class="r faint" title="' + App.esc("no total: each name breaks even at its own debit") +
        '">—</td></tr></tfoot>';

    var basis = spSingle() ? "entry price" : "width";
    return "<h3>The same structure, every name</h3>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Every name bought on the same ' +
      "rule and priced on the same assumption — so this column of nets is one assumption " +
      "repeated " + rows.length + " times, not " + rows.length + " pieces of evidence. " +
      "<b>Breakeven</b> is the " +
      "debit, as a share of " + basis + ", that would have left that name exactly square: " +
      "under it the run made money, over it it did not, and it is the one column here that " +
      "needs no view on what it cost. Click a row to bring that name up above.</p>" +
      '<div class="tablewrap"><table class="scan money rank"><thead><tr>' +
      th("ticker", "Name") + th("years", "Years", "r") + th("won", "Won", "r") +
      th("paid", "Cash out", "r") + th("received", "Cash in", "r") + th("net", "Net", "r") +
      th("roi", "Return", "r") + th("breakeven", "Breakeven", "r") +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>";
  }

  function spTiles(econ) {
    function tile(cls, k, v) {
      return '<div class="rule ' + cls + '"><span class="k">' + k + '</span><div class="v">' + v +
        "</div></div>";
    }
    var single = spSingle();
    var verdict = econ.net > 0 ? "cheap" : econ.net < 0 ? "rich" : "fair";
    var side = rp.dir === "up" ? "call" : "put";
    var structure = single
      ? side + ", " + App.num(sp.long, 1) + "% strike, held " + rp.hold + " weeks"
      : side + " debit spread, " + App.num(sp.long, 1) + "% / " + App.num(sp.short, 1) + "%, held " +
        rp.hold + " weeks";
    var basis = single ? "entry price" : "width";
    return '<div class="rulebar">' +
      tile(verdict,
           "Net over " + econ.years + " year" + (econ.years === 1 ? "" : "s") + " — " +
           App.cash(econ.net),
           App.cash(econ.received) + " came back against " + App.cash(econ.paid) + " paid out, on a " +
           structure + (econ.lots > 1 ? ", " + econ.lots + " contracts a year" : "") +
           ". Commission and slippage are not in it, and neither is the fact that a real debit "
           + "would not have been the same every year.") +
      tile("fair", "Return on the money risked — " + signed(econ.roi, 0),
           "Net divided by everything paid in. Not annualised, and not a portfolio return: the "
           + "cash is only at risk for " + rp.hold + " weeks of each year, and " +
           (single ? "a single leg" : "a debit vertical") + " can lose all of it.") +
      tile(econ.breakeven === null ? "fair" : sp.debit <= econ.breakeven ? "cheap" : "rich",
           "Breakeven debit — " + App.num(econ.breakeven, 0) + "% of " + basis,
           "Pay less than this and the run made money, more and it did not. This is the one "
           + "number here that does not rest on your assumption, so it is the one to take to a "
           + "live quote. You have set " + App.num(sp.debit, 0) + "%.") +
      tile("fair", "Won " + econ.won + " of " + econ.years +
           (!single && econ.maxed ? " · " + econ.maxed + " at max" : ""),
           econ.worthless + " expired worthless, which means the whole premium gone. A win "
           + "rate is not an edge until the sizes are in it — that is what the net on the left "
           + "is for.") +
      "</div>";
  }

  /* The one rule that belongs to the money tables rather than to the trial
     above them: these closes are dividend-adjusted and option strikes never
     are, so a window spanning an ex-dividend date travels slightly further here
     than the real price did against the real strike. Printed next to the payout
     it bends rather than in the caveat list at the foot of the tab — with the
     money section switched off, nothing on this page settles against a strike
     and the note is noise. */
  function spRules(d) {
    var rules = rpRules(d, SP_RULES).html;
    if (!rules) return "";
    return '<div class="panelcard" style="margin-top:18px">' +
      "<h3>And one thing these closes are not</h3><ul>" + rules + "</ul></div>";
  }

  function spDraw() {
    var host = App.$("#moneybody"), d = App.store.weekly, section = App.$("#spreadsection");
    if (section) section.hidden = !sp.on;
    if (!sp.on || !d || !host) return;

    var series = null;
    for (var i = 0; i < d.series.length; i++) {
      if (d.series[i].ticker === rp.ticker) { series = d.series[i]; break; }
    }
    if (!series) { host.innerHTML = ""; return; }

    var econ = SpreadTrial.economics(rpTrial(d, series), spDeal());
    if (econ.why) {
      var hint = econ.why.indexOf("short strike") >= 0
        ? " — the strike you sell is what caps the payout, so it has to sit further out than " +
          "the one you buy."
        : "";
      host.innerHTML = '<p class="empty">' + App.esc(econ.why) + hint + "</p>";
      return;
    }

    host.innerHTML = (econ.years ? spTiles(econ) : "") +
      "<h3>" + App.esc(rp.ticker) + ", year by year</h3>" +
      spYearTable(econ) + spAllTable(d) + spRules(d);

    // Scoped to table.money, and spSort is its own: the ranking above sorts on
    // keys this table does not have, and sharing one sort state made picking a
    // column in one table quietly scramble the other.
    App.wireSort(host.querySelectorAll("table.money thead th"), function (k) {
      if (spSort.key === k) spSort.dir = -spSort.dir;
      else { spSort.key = k; spSort.dir = k === "ticker" ? 1 : -1; }
      spDraw();
      var again = host.querySelector('table.money thead th[data-key="' + k + '"]');
      if (again) again.focus();
    });

    var picks = host.querySelectorAll("tr.srow[data-ticker]");
    for (var p = 0; p < picks.length; p++) {
      picks[p].addEventListener("click", function () { rpPick(this.dataset.ticker); });
      picks[p].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
          e.preventDefault();
          rpPick(this.dataset.ticker);
        }
      });
    }
  }

  function rpDraw() {
    var d = App.store.weekly, host = App.$("#repeatbody");
    if (!d) return;
    var series = null;
    for (var i = 0; i < d.series.length; i++) {
      if (d.series[i].ticker === rp.ticker) { series = d.series[i]; break; }
    }
    rpHeat(d, series);
    if (!series) {
      host.innerHTML = '<p class="empty">No weekly history for that name.</p>';
      return;
    }

    var t = rpTrial(d, series);
    var short = t.rows.length < t.asked
      ? '<div class="notice">Only ' + t.rows.length + " of the last " + t.asked +
        " years " + (t.rows.length === 1 ? "has" : "have") + " an ISO week " + rp.week +
        " — that week does not fall in every year, and the history only reaches so far back.</div>"
      : "";
    var pending = [];
    if (t.open) {
      // "Touched", not "past the target": a name can be past it in week three
      // and back under it by the week the window closes on, and the closing
      // week is the one that decides.
      var already = !t.touched_open ? ""
        : t.open === 1 ? " (it has touched the target, but has not closed yet)"
        : " (" + t.touched_open + " of them have touched the target)";
      pending.push(t.open + (t.open === 1 ? " year is" : " years are") + " still running" + already);
    }
    if (t.skipped) pending.push(t.skipped + " skipped for want of data");

    host.innerHTML = rpStale(d) + short + rpTiles(t) +
      (pending.length
        ? '<p class="faint" style="font-size:.83rem;margin:-12px 0 16px">' +
          App.esc(pending.join(" · ")) + " — counted in neither column.</p>"
        : "") +
      rpStrip(t) + rpYearTable(t) + rpAllTable(d) + rpCaveats(d);

    App.wireSort(host.querySelectorAll("table.scan thead th"), function (k) {
      if (rpSort.key === k) rpSort.dir = -rpSort.dir;
      else { rpSort.key = k; rpSort.dir = k === "ticker" ? 1 : -1; }
      rpDraw();
      var again = host.querySelector('table.scan thead th[data-key="' + k + '"]');
      if (again) again.focus();
    });

    var picks = host.querySelectorAll("tr.srow[data-ticker]");
    for (var p = 0; p < picks.length; p++) {
      picks[p].addEventListener("click", function () { rpPick(this.dataset.ticker); });
      picks[p].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
          e.preventDefault();
          rpPick(this.dataset.ticker);
        }
      });
    }

    // The money section reads the same trial, so it redraws whenever this does.
    spDraw();
  }

  function rpPick(ticker) {
    rp.ticker = ticker;
    App.$("#rp-ticker").value = ticker;
    rpStore();
    App.writeHash();
    rpDraw();
    App.$("#repeatcontrols").scrollIntoView({ block: "nearest" });
  }

  // The slider says "37"; this says which week of the year that is, using the
  // most recent one on the axis. A week number nobody can place on a calendar
  // is not a control, it is a number.
  function rpWeekLabel(d) {
    var label = "week " + rp.week;
    var want = "W" + (rp.week < 10 ? "0" : "") + rp.week;
    for (var i = d.weeks.length - 1; i >= 0; i--) {
      if (d.weeks[i].slice(5) === want) {
        var when = new Date(d.starts[i] + "T00:00:00");
        if (!isNaN(when.getTime())) {
          // The *ISO* year, not the Monday's calendar year. ISO 2021-W01 opens
          // on 4 January but 2020-W53 opens on 28 December — labelling that one
          // "in 2020" while the table calls the row 2020 is the only reading
          // where the two agree.
          label += " — w/c " + when.toLocaleDateString(undefined, { day: "numeric", month: "short" }) +
            " in " + d.weeks[i].slice(0, 4);
        }
        break;
      }
    }
    App.$("#rp-weeklabel").textContent = label;
  }

  function rpNum(el, lo, hi, fallback) {
    var v = Math.round(Number(el.value));
    if (isNaN(v)) v = fallback;
    return Math.min(hi, Math.max(lo, v));
  }

  // What the structure choice changes on the page besides the arithmetic: the
  // short strike only means something for a vertical, and the debit is a share
  // of a different number for each. Called on every structure change and once
  // at wire-up, so a restored "single" from localStorage renders as one too.
  function spApplyStructure() {
    var single = spSingle();
    var chips = document.querySelectorAll("#sp-structure button");
    for (var i = 0; i < chips.length; i++) {
      chips[i].setAttribute("aria-pressed", chips[i].dataset.structure === sp.structure
        ? "true" : "false");
    }
    var shortCtl = App.$("#sp-short-ctl");
    if (shortCtl) shortCtl.hidden = single;
    var label = App.$("#sp-debit-label");
    if (label) {
      label.textContent = single ? "Premium paid, % of entry price" : "Debit paid, % of width";
    }
  }

  // The money controls. Separate from wireRepeat's, because they redraw only
  // the money section — re-running the whole tab to change a contract count
  // would rebuild thirty names' worth of tables for nothing.
  function wireSpread() {
    // One handler shape for all four: read the field, clamp it through the
    // table above, store, redraw.
    function onNum(id, key) {
      App.$(id).addEventListener("input", function () {
        sp[key] = spClamp(key, this.value);
        spStore();
        spDraw();
      });
    }
    onNum("#sp-long", "long");
    onNum("#sp-short", "short");
    onNum("#sp-debit", "debit");
    onNum("#sp-contracts", "contracts");
    App.$("#sp-on").addEventListener("change", function () {
      sp.on = this.checked;
      spStore();
      spDraw();
      if (sp.on) App.$("#spreadcontrols").scrollIntoView({ block: "nearest", behavior: "smooth" });
    });

    var structures = document.querySelectorAll("#sp-structure button");
    for (var s = 0; s < structures.length; s++) {
      structures[s].addEventListener("click", function () {
        sp.structure = this.dataset.structure;
        spApplyStructure();
        spStore();
        spDraw();
      });
    }

    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("repeat-spread") || "null"); } catch (e) {
      saved = null;
    }
    if (saved) {
      // Through the same clamp the controls use. What comes back here is
      // whatever was in localStorage the last time any version of this page
      // wrote it — or whatever someone typed into devtools — so it is input,
      // not state, and it is treated as input. `structure` has no numeric
      // range to clamp through; anything other than "single" reads as the
      // "spread" default, which is also what a blob saved before this
      // structure existed carries.
      for (var key in sp) {
        if (!App.has(saved[key])) continue;
        sp[key] = key === "on" ? !!saved[key]
          : key === "structure" ? (saved[key] === "single" ? "single" : "spread")
          : spClamp(key, saved[key]);
      }
    }
    App.$("#sp-on").checked = !!sp.on;
    App.$("#sp-long").value = sp.long;
    App.$("#sp-short").value = sp.short;
    App.$("#sp-debit").value = sp.debit;
    App.$("#sp-contracts").value = sp.contracts;
    App.$("#spreadsection").hidden = !sp.on;
    spApplyStructure();
  }

  function renderRepeat() {
    App.load("weekly").then(function (d) {
      if (!rpAt) {
        rpAt = SpreadTrial.index(d);

        var select = App.$("#rp-ticker");
        select.innerHTML = d.series.map(function (s) {
          return '<option value="' + App.esc(s.ticker) + '">' + App.esc(s.ticker) + "</option>";
        }).join("");
        rpWeekLabel(d);
      }
      if (!d.series.length) {
        App.$("#repeatbody").innerHTML = '<p class="empty">The weekly history is empty — no name in ' +
          "this screen has the " + (d.min_weeks || 26) + " weeks the test needs.</p>";
        return;
      }
      // A remembered name that has since dropped out of the screen is not an
      // error; it just is not on this page any more. Checked on every render
      // rather than only the first, because a fragment can name a ticker before
      // the payload that would have vetted it has landed.
      var known = d.series.some(function (s) { return s.ticker === rp.ticker; });
      if (!known) rp.ticker = (d.series[0] || {}).ticker || "";
      App.$("#rp-ticker").value = rp.ticker;
      App.writeHash();
      rpDraw();
    }).catch(function (e) {
      App.$("#repeatbody").innerHTML = App.loadError(e, "weekly", "python run.py");
    });
  }

  function wireRepeat() {
    function onChange(fn) {
      return function () {
        fn(this);
        rpStore();
        App.writeHash();          // as on the Backtest tab: the name is a view
        if (App.store.weekly) { rpWeekLabel(App.store.weekly); rpDraw(); }
      };
    }
    App.$("#rp-ticker").addEventListener("change", onChange(function (el) { rp.ticker = el.value; }));
    App.$("#rp-week").addEventListener("input", onChange(function (el) {
      rp.week = rpNum(el, 1, 53, 37);
    }));
    App.$("#rp-hold").addEventListener("input", onChange(function (el) {
      rp.hold = rpNum(el, 1, 52, 8);
    }));
    App.$("#rp-years").addEventListener("input", onChange(function (el) {
      rp.years = rpNum(el, 2, 25, 10);
    }));
    App.$("#rp-target").addEventListener("input", onChange(function (el) {
      // Down to −95%, not up from 0.5%: a zero or negative target is the
      // in-the-money question ("did it hold up"), and it is a real one. The
      // floor is short of −100% only because the target price has to stay
      // above zero — see rpMove.
      var v = Number(el.value);
      rp.target = isNaN(v) ? 8 : Math.min(300, Math.max(-95, v));
    }));

    var dirs = document.querySelectorAll("#rp-dir button");
    for (var i = 0; i < dirs.length; i++) {
      dirs[i].addEventListener("click", function () {
        rp.dir = this.dataset.dir;
        for (var j = 0; j < dirs.length; j++) {
          dirs[j].setAttribute("aria-pressed", dirs[j].dataset.dir === rp.dir ? "true" : "false");
        }
        rpStore();
        if (App.store.weekly) rpDraw();
      });
    }

    // Whatever was set last time, so a setup survives a reload — the same
    // contract the tab strip has.
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("repeat") || "null"); } catch (e) { saved = null; }
    if (saved) {
      for (var key in rp) if (App.has(saved[key])) rp[key] = saved[key];
    }
    App.$("#rp-week").value = rp.week;
    App.$("#rp-hold").value = rp.hold;
    App.$("#rp-target").value = rp.target;
    App.$("#rp-years").value = rp.years;
    for (var k = 0; k < dirs.length; k++) {
      dirs[k].setAttribute("aria-pressed", dirs[k].dataset.dir === rp.dir ? "true" : "false");
    }
  }
})(window.SpreadApp = window.SpreadApp || {});
