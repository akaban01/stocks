/* Spread Scanner — frontend: the Charts tab (price cards and the Seasonality view).
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.renderCharts = renderCharts;
  App.heatStyle = heatStyle;
  App.showBacktestView = showBacktestView;
  App.wireBacktestViews = wireBacktestViews;
  App.showChartView = showChartView;

  // --------------------------------------------------------------- charts

  function sparkline(dates, closes, w, h) {
    var n = closes.length;
    if (n < 2) return '<p class="faint">not enough history</p>';
    w = w || 320; h = h || 116;
    var padL = 6, padR = 6, padT = 8, padB = 18;
    var pw = w - padL - padR, ph = h - padT - padB, baseY = padT + ph;
    var lo = Math.min.apply(null, closes), hi = Math.max.apply(null, closes);
    if (hi <= lo) hi = lo + 1;
    function X(i) { return padL + (i / (n - 1)) * pw; }
    function Y(v) { return padT + (1 - (v - lo) / (hi - lo)) * ph; }

    var pts = closes.map(function (v, i) { return X(i).toFixed(1) + "," + Y(v).toFixed(1); }).join(" ");
    var up = closes[n - 1] >= closes[0];
    var color = App.theme(up ? "--up" : "--down");
    var area = "M " + X(0).toFixed(1) + "," + baseY.toFixed(1) + " L " +
      pts.split(" ").join(" L ") + " L " + X(n - 1).toFixed(1) + "," + baseY.toFixed(1) + " Z";

    // Every year boundary gets a rule; the labels are thinned to whatever fits,
    // so a ten-year window reads as cleanly as a two-year one.
    var LABEL_GAP = 26;
    var lastLabel = X(0);
    var grid = ['<text x="' + (X(0) + 2).toFixed(1) + '" y="' + (h - 5) + '" class="yr">' +
      dates[0].slice(0, 4) + "</text>"];
    for (var k = 1; k < n; k++) {
      if (dates[k].slice(0, 4) !== dates[k - 1].slice(0, 4)) {
        var x = X(k).toFixed(1);
        grid.push('<line x1="' + x + '" y1="' + padT + '" x2="' + x + '" y2="' + baseY.toFixed(1) + '" class="gl"/>');
        if (X(k) - lastLabel >= LABEL_GAP) {
          lastLabel = X(k);
          grid.push('<text x="' + (X(k) + 2).toFixed(1) + '" y="' + (h - 5) + '" class="yr">' +
            dates[k].slice(0, 4) + "</text>");
        }
      }
    }
    return '<svg class="spark" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="xMidYMid meet" ' +
      'role="img" aria-label="price history">' +
      '<line x1="' + padL + '" y1="' + baseY.toFixed(1) + '" x2="' + (w - padR) + '" y2="' + baseY.toFixed(1) + '" class="ax"/>' +
      grid.join("") +
      '<path d="' + area + '" fill="' + color + '" fill-opacity="0.12"/>' +
      '<polyline points="' + pts + '" fill="none" stroke="' + color + '" stroke-width="1.6" ' +
      'stroke-linejoin="round" stroke-linecap="round"/>' +
      '<circle cx="' + X(n - 1).toFixed(1) + '" cy="' + Y(closes[n - 1]).toFixed(1) + '" r="2.6" fill="' + color + '"/>' +
      "</svg>";
  }

  function chg(v, suffix) {
    if (!App.has(v)) return '<span class="chg neut">—</span>';
    return '<span class="chg ' + (v >= 0 ? "up" : "down") + '">' + (v >= 0 ? "+" : "") +
      App.num(v, 1) + "%" + (suffix ? " " + App.esc(suffix) : "") + "</span>";
  }

  function renderCharts() {
    var host = App.$("#chartgrid");
    if (host.dataset.done) return;
    App.load("charts").then(function (d) {
      host.dataset.done = "1";
      App.$("#chartmeta").textContent = d.count + " tickers · " +
        ((d.window || {}).start || "?") + " → " + ((d.window || {}).end || "?") +
        (d.period ? " · " + d.period + " window" : "");
      host.innerHTML = (d.series || []).length
        ? d.series.map(function (s) {
            return '<div class="chart-card"><div class="chart-head">' +
              '<span class="tkr">' + App.esc(s.ticker) + "</span>" +
              '<span class="px">' + App.num(s.last, 2) + "</span>" +
              chg(s.change_1y_pct, "1y") + "</div>" +
              sparkline(s.dates, s.closes) +
              '<div class="chart-foot">range <b>' + App.num(s.low, 2) + " – " + App.num(s.high, 2) +
              "</b> · window " + (App.has(s.change_window_pct)
                ? (s.change_window_pct >= 0 ? "+" : "") + App.num(s.change_window_pct, 1) + "%" : "—") +
              "</div></div>";
          }).join("")
        : '<p class="empty">No price history available.</p>';
      renderSeasonality(d);
    }).catch(function (e) {
      host.innerHTML = App.loadError(e, "charts", "python run.py");
      App.$("#seasonbody").innerHTML = App.loadError(e, "charts", "python run.py");
      App.$("#chartmeta").textContent = "";
      App.$("#seasonmeta").textContent = "";
    });
  }

  // ---------------------------------------------------------- seasonality
  //
  // The same closes, cut by calendar month. charts.json ships one row per month
  // per ticker plus the pooled row, so nothing here computes returns — it draws
  // what the backend already grouped, and it refuses to draw a month the
  // backend flagged as too thin to rank.

  var MONTH_INITIALS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];
  var MONTH_NAMES = ["January", "February", "March", "April", "May", "June", "July",
                     "August", "September", "October", "November", "December"];
  // The --up / --down tokens, as rgb triples so cell fills can be faded.
  // Read once per render off the stylesheet rather than restated here: these
  // are --up, --down and --wait, and hardcoding them meant a palette change
  // moved the page and left every chart on the old colours.
  var UP_RGB = App.themeRgb("--up"), DOWN_RGB = App.themeRgb("--down"), THIN_RGB = App.themeRgb("--wait");

  var seasonSort = { key: "ticker", dir: 1 };

  function monthRow(seas, month) {
    return seas && seas.months ? seas.months[month - 1] : null;
  }
  // Whether a month is too thin to lean on — the same test the backend ranks by.
  // A pooled row's `years` spans every name concatenated, so one long history
  // can carry it; `ticker_years.median` is the typical name's, and that is what
  // decides both the ranking and the grey.
  function thinMonth(row, minYears) {
    if (!row || row.avg_pct === null) return true;
    return (row.ticker_years ? row.ticker_years.median : row.years) < minYears;
  }

  /* A twelve-bar chart hanging off a baseline — the average move (baseline 0)
     or the hit rate (baseline 50%). One measure per chart: the two never share
     an axis. Months with too little history are drawn grey and unlabelled. */
  function monthChart(rows, opt) {
    var W = 480, H = 196, padL = 40, padR = 12, padT = 14, padB = 30;
    var pw = W - padL - padR, ph = H - padT - padB;
    var base = opt.baseline || 0;
    var vals = rows.map(opt.value).filter(function (v) { return v !== null && !isNaN(v); });
    if (!vals.length) return '<p class="empty">Not enough history to group by month.</p>';

    var lo = Math.min.apply(null, vals.concat([base]));
    var hi = Math.max.apply(null, vals.concat([base]));
    var pad = (hi - lo) * 0.18 || 1;
    lo -= pad; hi += pad;
    if (opt.clamp) { lo = Math.max(lo, opt.clamp[0]); hi = Math.min(hi, opt.clamp[1]); }

    function Y(v) { return padT + (1 - (v - lo) / (hi - lo)) * ph; }
    var y0 = Y(base), slot = pw / 12, bw = Math.min(28, slot * 0.6);

    var parts = [];
    // Recessive frame: the baseline is the only solid rule, top and bottom are ticks.
    parts.push('<line x1="' + padL + '" y1="' + y0.toFixed(1) + '" x2="' + (W - padR) +
               '" y2="' + y0.toFixed(1) + '" class="ax"/>');
    [[hi, Y(hi)], [base, y0], [lo, Y(lo)]].forEach(function (t) {
      parts.push('<text x="' + (padL - 6) + '" y="' + (t[1] + 3.5).toFixed(1) +
                 '" class="ylab">' + App.esc(opt.fmt(t[0])) + "</text>");
    });

    rows.forEach(function (r, i) {
      var v = opt.value(r), x = padL + slot * i + (slot - bw) / 2;
      var mid = padL + slot * (i + 0.5);
      parts.push('<text x="' + mid.toFixed(1) + '" y="' + (H - 10) + '" class="xlab">' +
                 MONTH_INITIALS[i] + "</text>");
      if (v === null || isNaN(v)) return;

      var grey = thinMonth(r, opt.minYears);
      var up = v >= base;
      var y = up ? Y(v) : y0, h = Math.max(1.5, Math.abs(Y(v) - y0));
      parts.push('<rect x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + bw.toFixed(1) +
                 '" height="' + h.toFixed(1) + '" rx="2" fill="rgb(' +
                 (grey ? THIN_RGB : up ? UP_RGB : DOWN_RGB) + ')" fill-opacity="' +
                 (grey ? "0.35" : "0.85") + '"><title>' + App.esc(opt.title(r)) + "</title></rect>");

      // Direct-label the extremes only; the table below carries every number.
      if (!grey && (r.month === opt.best || r.month === opt.worst)) {
        parts.push('<text x="' + mid.toFixed(1) + '" y="' + (up ? y - 5 : y + h + 11).toFixed(1) +
                   '" class="blab">' + App.esc(opt.fmt(v)) + "</text>");
      }
    });

    return '<svg class="monthchart" viewBox="0 0 ' + W + " " + H + '" ' +
      'preserveAspectRatio="xMidYMid meet" role="img" aria-label="' + App.esc(opt.aria) + '">' +
      parts.join("") + "</svg>";
  }

  function seasonPanel(title, sub, svg) {
    return '<div class="panelcard"><h3>' + App.esc(title) + "</h3>" +
      '<p class="faint" style="font-size:.8rem;margin:-4px 0 8px">' + sub + "</p>" + svg + "</div>";
  }

  function heatStyle(v, scale) {
    if (v === null || v === undefined || isNaN(v)) return "";
    var t = Math.min(1, Math.abs(v) / scale);
    return "background:rgba(" + (v >= 0 ? UP_RGB : DOWN_RGB) + "," + (0.07 + 0.5 * t).toFixed(3) + ")";
  }

  /* One robust scale for every cell, so a single blow-up month cannot wash the
     whole grid out: the 90th percentile of |average|, never below 1%. */
  function heatScale(series) {
    var mags = [];
    series.forEach(function (s) {
      ((s.seasonality || {}).months || []).forEach(function (m) {
        if (m.avg_pct !== null) mags.push(Math.abs(m.avg_pct));
      });
    });
    if (!mags.length) return 1;
    mags.sort(function (a, b) { return a - b; });
    return Math.max(1, mags[Math.floor(mags.length * 0.9)] || mags[mags.length - 1]);
  }

  function monthBadge(month, cls) {
    return month ? '<span class="tag ' + cls + '">' + MONTH_NAMES[month - 1].slice(0, 3) + "</span>"
                 : '<span class="faint">—</span>';
  }

  // Sentinel data-ticker for the pooled row's cells, so a click handler can
  // tell "every name" apart from an actual ticker without guessing at names.
  var SEASON_ALL = "__ALL__";

  // Callers pass a series that has a seasonality block: seasonHeat filters on it
  // and the pooled row is only built when the payload carries one.
  function heatRow(name, tickerId, seas, scale, minYears, cls) {
    var cells = seas.months.map(function (m) {
      if (m.avg_pct === null) return '<td class="r faint">—</td>';
      var weak = thinMonth(m, minYears);
      var behind = m.ticker_years
        ? m.n + " name-months, " + m.ticker_years.median + " years for the typical name" +
          (m.ticker_years.min < m.ticker_years.median ? " (fewest " + m.ticker_years.min + ")" : "")
        : m.n + " observation" + (m.n === 1 ? "" : "s") + " over " + m.years +
          " year" + (m.years === 1 ? "" : "s");
      var tip = MONTH_NAMES[m.month - 1] + ": average " + (m.avg_pct >= 0 ? "+" : "") +
        App.num(m.avg_pct, 2) + "%, median " + (m.median_pct >= 0 ? "+" : "") + App.num(m.median_pct, 2) +
        "%, up " + App.num(m.win_rate_pct, 0) + "% of the time, " + behind +
        (weak ? " — too few to rank" : "") + " — click for every year.";
      return '<td class="r seasoncell' + (weak ? " faint" : "") + '" tabindex="0" role="button" ' +
        'aria-pressed="false" data-ticker="' + App.esc(tickerId) + '" data-month="' + m.month +
        '" title="' + App.esc(tip) + '" style="' + (weak ? "" : heatStyle(m.avg_pct, scale)) + '">' +
        (m.avg_pct >= 0 ? "+" : "") + App.num(m.avg_pct, 1) + "</td>";
    }).join("");
    var span = seas.years.start + "–" + seas.years.end + ", " + seas.observations +
      " whole months measured";
    return '<tr class="' + cls + '"><td class="t">' + App.esc(name) + "</td>" +
      '<td class="r faint" title="' + App.esc(span) + '">' + seas.years.count + "</td>" + cells +
      "<td>" + monthBadge(seas.best_month, "buy") + "</td>" +
      "<td>" + monthBadge(seas.worst_month, "sell") + "</td></tr>";
  }

  function seasonHeat(d, minYears) {
    var series = (d.series || []).filter(function (s) { return s.seasonality; });
    if (!series.length) return "";
    var scale = heatScale(series);

    var rows = series.slice();
    var key = seasonSort.key;
    rows.sort(function (a, b) {
      if (key === "ticker") return a.ticker.localeCompare(b.ticker) * seasonSort.dir;
      if (key === "years") return (a.seasonality.years.count - b.seasonality.years.count) * seasonSort.dir;
      var x = monthRow(a.seasonality, key), y = monthRow(b.seasonality, key);
      x = x && x.avg_pct !== null ? x.avg_pct : -Infinity;
      y = y && y.avg_pct !== null ? y.avg_pct : -Infinity;
      return (x - y) * seasonSort.dir;
    });

    function th(k, label, cls) {
      var sort = String(seasonSort.key) === String(k)
        ? (seasonSort.dir === 1 ? "ascending" : "descending") : "none";
      return App.sortableTh(k, label, cls || "", sort);
    }
    var head = th("ticker", "Name") + th("years", "Yrs", "r") +
      MONTH_NAMES.map(function (n, i) { return th(i + 1, n.slice(0, 3), "r"); }).join("") +
      "<th>Best</th><th>Worst</th>";

    var body = (d.seasonality
      ? heatRow("All " + d.seasonality.tickers + " names", SEASON_ALL, d.seasonality, scale, minYears, "pool")
      : "") + rows.map(function (s) {
        return heatRow(s.ticker, s.ticker, s.seasonality, scale, minYears, "");
      }).join("");

    return '<h2>Every name, month by month</h2>' +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Average return in each calendar month, ' +
      'in percent. Hover a cell for the median, the hit rate and how many years stand behind it; ' +
      'click a cell to see every year behind it, for that name or every name; click a month header ' +
      "to rank the names by it. Greyed cells rest on fewer than " + minYears +
      " years — for the pooled row, fewer than that for the typical name — and are never named " +
      "best or worst.</p>" +
      '<div class="tablewrap"><table class="scan heat"><thead><tr>' + head +
      "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      '<div id="seasondetail" hidden></div>';
  }

  // The panel a click on a heat cell opens: every year behind that cell, for
  // the clicked name or (data-ticker === SEASON_ALL) pooled across every name.
  // A <select> lets the same panel flip between one name and all of them
  // without re-clicking the grid — the month stays fixed, only the name view
  // changes.
  function seasonDetail(d, ticker, month) {
    var isAll = ticker === SEASON_ALL;
    var seas = isAll ? d.seasonality
      : ((d.series || []).filter(function (s) { return s.ticker === ticker; })[0] || {}).seasonality;
    var row = monthRow(seas, month);
    if (!row) return "";

    var named = (d.series || []).filter(function (s) {
      return monthRow(s.seasonality, month);
    }).map(function (s) { return s.ticker; }).sort();
    var options = '<option value="' + SEASON_ALL + '"' + (isAll ? " selected" : "") + '>All names</option>' +
      named.map(function (t) {
        return '<option value="' + App.esc(t) + '"' + (t === ticker ? " selected" : "") + '>' + App.esc(t) + "</option>";
      }).join("");

    var behind = row.ticker_years
      ? row.n + " name-months, " + row.ticker_years.median + " years for the typical name"
      : row.n + " observation" + (row.n === 1 ? "" : "s") + " over " + row.years +
        " year" + (row.years === 1 ? "" : "s");
    var stat = (row.avg_pct >= 0 ? "+" : "") + App.num(row.avg_pct, 2) + "% average, " +
      (row.median_pct >= 0 ? "+" : "") + App.num(row.median_pct, 2) + "% median, up " +
      App.num(row.win_rate_pct, 0) + "% of the time — " + behind + ".";

    function pctCell(v) {
      return '<td class="r ' + (v >= 0 ? "up" : "down") + '">' + (v >= 0 ? "+" : "") + App.num(v, 2) + "</td>";
    }
    var byYear = row.by_year || [];
    var head = isAll ? "<tr><th>Year</th><th>Name</th><th class=\"r\">Return %</th></tr>"
                     : "<tr><th>Year</th><th class=\"r\">Return %</th></tr>";
    var body = byYear.length ? byYear.map(function (e) {
      return "<tr><td class=\"r faint\">" + e.year + "</td>" +
        (isAll ? "<td>" + App.esc(e.ticker) + "</td>" : "") + pctCell(e.pct) + "</tr>";
    }).join("") : '<tr><td colspan="' + (isAll ? 3 : 2) + '" class="empty">No years yet.</td></tr>';

    return '<div class="panelcard seasondetail">' +
      "<h3>" + App.esc(MONTH_NAMES[month - 1]) + " — " +
      App.esc(isAll ? "All " + d.seasonality.tickers + " names" : ticker) + "</h3>" +
      '<p class="faint" style="font-size:.8rem;margin:-4px 0 8px">' + App.esc(stat) + "</p>" +
      '<label class="faint" style="font-size:.8rem;display:block;margin-bottom:8px">Show ' +
      '<select class="seasondetail-pick">' + options + "</select></label>" +
      '<div class="tablewrap"><table class="scan"><thead>' + head + "</thead><tbody>" +
      body + "</tbody></table></div></div>";
  }

  function monthYears(row) {
    return row.ticker_years ? row.ticker_years.median : row.years;
  }

  // The pooled year span can be carried by one long history, so the headline
  // quotes the median name's instead — the number the ranking actually gates on.
  function typicalYears(pooled) {
    var years = (pooled.months || []).map(function (m) {
      return m.ticker_years ? m.ticker_years.median : null;
    }).filter(App.has).sort(function (a, b) { return a - b; });
    return years.length ? years[Math.floor((years.length - 1) / 2)] : pooled.years.count;
  }

  function seasonHeadline(pooled, minYears) {
    function tile(row, cls, label) {
      if (!row) {
        // Two reasons a month is not named: too little history to rank (the
        // gate is years per name, not months), or ranked and the extreme did
        // not pass the calendar-shuffle test (seasonality.extreme_p).
        var p = cls === "cheap" ? pooled.best_p : pooled.worst_p;
        var enough = typicalYears(pooled) >= minYears;
        return '<div class="rule"><span class="k">' + label + '</span><div class="v">' +
          (enough && App.has(p)
            ? "No month stands out from chance: with the calendar months shuffled, a " +
              (cls === "cheap" ? "best" : "worst") + " month this extreme turns up in " +
              App.num(p * 100, 0) + "% of histories, so none is named."
            : "No month yet has " + minYears +
              " years behind the typical name, so none is called best or worst.") +
          "</div></div>";
      }
      return '<div class="rule ' + cls + '"><span class="k">' + label + " — " +
        MONTH_NAMES[row.month - 1] + "</span><div class=\"v\">" +
        (row.avg_pct >= 0 ? "+" : "") + App.num(row.avg_pct, 2) + "% on average · higher in " +
        App.num(row.win_rate_pct, 0) + "% of them · " + row.n + " name-months, " +
        (row.ticker_years ? row.ticker_years.median + " years per name" : row.years + " years") +
        "</div></div>";
    }
    var best = pooled ? monthRow(pooled, pooled.best_month) : null;
    var worst = pooled ? monthRow(pooled, pooled.worst_month) : null;
    return '<div class="rulebar">' + tile(best, "cheap", "Best month") +
      tile(worst, "rich", "Worst month") +
      '<div class="rule"><span class="k">How thin is this?</span><div class="v">' +
      (pooled ? "Each month is one reading per name per year. The window spans " +
        pooled.years.count + " years; the typical name has " + typicalYears(pooled) +
        " of them, and a month needs " + minYears + " to be ranked at all."
              : "No pooled history available.") + "</div></div></div>";
  }

  function renderSeasonality(d) {
    var host = App.$("#seasonbody");
    var pooled = d.seasonality;
    var minYears = (pooled && pooled.min_years) || 3;

    if (!pooled) {
      App.$("#seasonmeta").textContent = "";
      host.innerHTML = pooled === undefined
        ? '<p class="empty">This scan was written before the seasonality view existed — the next ' +
          'run adds it.<br><span class="faint">Locally: <code>python run.py</code></span></p>'
        : '<p class="empty">The window holds too few whole months to group by calendar month. ' +
          "A longer <code>charts.history_period</code> fixes it.</p>";
      return;
    }

    App.$("#seasonmeta").textContent = pooled.tickers + " names · " + pooled.years.start + "–" +
      pooled.years.end + " · " + pooled.observations + " whole months measured";

    var rows = pooled.months;
    var avgChart = monthChart(rows, {
      value: function (r) { return r.avg_pct; },
      baseline: 0, best: pooled.best_month, worst: pooled.worst_month, minYears: minYears,
      fmt: function (v) { return (v >= 0 ? "+" : "") + App.num(v, 1) + "%"; },
      title: function (r) {
        return MONTH_NAMES[r.month - 1] + ": " + (r.avg_pct >= 0 ? "+" : "") + App.num(r.avg_pct, 2) +
          "% average, " + (r.median_pct >= 0 ? "+" : "") + App.num(r.median_pct, 2) + "% median (" +
          r.n + " name-months, " + monthYears(r) + " years per name)";
      },
      aria: "Average return by calendar month across every screened name"
    });
    var winChart = monthChart(rows, {
      value: function (r) { return r.win_rate_pct; },
      baseline: 50, clamp: [0, 100], best: pooled.best_month, worst: pooled.worst_month,
      minYears: minYears,
      fmt: function (v) { return App.num(v, 0) + "%"; },
      title: function (r) {
        return MONTH_NAMES[r.month - 1] + ": higher in " + App.num(r.win_rate_pct, 0) + "% of " +
          r.n + " name-months (" + monthYears(r) + " years per name)";
      },
      aria: "Share of months that closed higher, by calendar month"
    });

    host.innerHTML = seasonHeadline(pooled, minYears) +
      '<div class="cols">' +
      seasonPanel("Average move", "Every name, every year, averaged per month. Bars above the line " +
                  "are months the basket gained.", avgChart) +
      seasonPanel("How often it worked", "The same months by hit rate — the line is a coin flip. A " +
                  "big average built on one good year sits near it.", winChart) +
      "</div>" +
      seasonHeat(d, minYears) +
      '<p class="faint" style="font-size:.82rem;margin-top:14px">' +
      "<b>Read this as a tendency with wide error bars, not an edge.</b> The largest bias is that " +
      "this basket is whatever passes the screen <i>today</i>: every month below is measured on " +
      "the survivors, and the names that would have dragged a month down are the ones no longer " +
      "here to be measured. On top of that, ten years is only ten Januaries, and these names move " +
      "together, so the pooled row is nearer ten years of evidence than ten years times thirty " +
      "names. Only whole months count — a part-month at either end is dropped rather than " +
      "annualised. Prices are split- and dividend-adjusted, but nothing here knows about earnings " +
      "dates or index rebalances, which is where a lot of month-shaped behaviour comes from.</p>";

    App.wireSort(document.querySelectorAll("#seasonbody table.heat th"), function (k) {
      if (String(seasonSort.key) === String(k)) seasonSort.dir = -seasonSort.dir;
      else { seasonSort.key = k; seasonSort.dir = k === "ticker" ? 1 : -1; }
      renderSeasonality(App.store.charts);
      var again = App.$('#seasonbody table.heat th[data-key="' + k + '"]');
      if (again) again.focus();
    });

    // A click on a month cell opens the year-by-year panel below the table —
    // one name or every name, picked from the <select> the panel carries.
    var detail = App.$("#seasondetail", host);
    var openCell = null;
    function showDetail(ticker, month) {
      detail.innerHTML = seasonDetail(d, ticker, month);
      detail.hidden = false;
      var pick = App.$(".seasondetail-pick", detail);
      if (pick) pick.addEventListener("change", function () { showDetail(this.value, month); });
    }
    function toggleCell(cell) {
      var pressed = host.querySelectorAll('td.seasoncell[aria-pressed="true"]');
      for (var i = 0; i < pressed.length; i++) pressed[i].setAttribute("aria-pressed", "false");
      if (cell === openCell) {
        openCell = null;
        detail.hidden = true;
        detail.innerHTML = "";
        return;
      }
      cell.setAttribute("aria-pressed", "true");
      openCell = cell;
      showDetail(cell.dataset.ticker, +cell.dataset.month);
    }
    var seasonCells = host.querySelectorAll("td.seasoncell");
    for (var c = 0; c < seasonCells.length; c++) {
      seasonCells[c].addEventListener("click", function () { toggleCell(this); });
      seasonCells[c].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggleCell(this); }
      });
    }
  }

  /* Which of the Backtest tab's four views is on screen.

     Each view is drawn only while it is the one showing — the sweep is forty
     runs and the search is three hundred per name, and paying for all of them
     on every keystroke is what the opt-in toggles were avoiding before. The
     draw functions are still called unconditionally from `btDraw`; they check
     this and return. */
  function showBacktestView(view) {
    if (App.BACKTEST_VIEWS.indexOf(view) === -1) view = "rules";
    App.btView = view;
    var buttons = document.querySelectorAll("#btviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed",
        buttons[i].dataset.btview === view ? "true" : "false");
    }
    var panes = document.querySelectorAll('.panel[data-tab="backtest"] > div[data-btview]');
    for (var j = 0; j < panes.length; j++) panes[j].hidden = panes[j].dataset.btview !== view;
    try { localStorage.setItem("backtestview", view); } catch (e) { /* private mode */ }
    // The view that just appeared may never have been drawn, or may be stale
    // from before a dial moved while it was hidden.
    App.btSweepDraw();
    App.bsDraw();
    App.bmDraw();
    App.writeHash();
  }

  function wireBacktestViews() {
    var buttons = document.querySelectorAll("#btviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () { showBacktestView(this.dataset.btview); });
    }
  }

  function showChartView(view) {
    if (App.CHART_VIEWS.indexOf(view) === -1) view = "prices";
    App.curView = view;
    var buttons = document.querySelectorAll("#chartviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].dataset.view === view ? "true" : "false");
    }
    var panes = document.querySelectorAll('.panel[data-tab="charts"] > div[data-view]');
    for (var j = 0; j < panes.length; j++) panes[j].hidden = panes[j].dataset.view !== view;
    try { localStorage.setItem("chartview", view); } catch (e) { /* private mode */ }
    App.writeHash();
  }
})(window.SpreadApp = window.SpreadApp || {});
