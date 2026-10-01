/* Spread Scanner — the Regime tab's pure helpers.
 *
 * data/regime.json -> HTML strings, SVG strings and the numbers the charts are
 * drawn from. Nothing here touches the DOM or the page's state, for the same
 * reason render.js is its own file: tests/test_regime_js.py runs *this file*
 * under node, with payloads whose strings are HTML injections and with
 * synthetic curves whose drawdowns are known.
 *
 * The page computes no result of its own. Every statistic, verdict and table
 * comes from the payload, which `python -m regime_backtest.run --json` writes
 * from the same functions as report.md. The one derived quantity is the
 * drawdown curve drawn from the weekly equity points, and the page labels it
 * as weekly: the exact daily max drawdown is in the table beside it.
 *
 * Browser: exposes window.SpreadRegime (needs window.SpreadRender loaded
 * first). Node: module.exports, requiring ./render.js itself.
 */
(function (root, factory) {
  var R = (typeof module !== "undefined" && module.exports) ? require("./render.js") : root.SpreadRender;
  var api = factory(R);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpreadRegime = api;
})(typeof self !== "undefined" ? self : this, function (R) {
  "use strict";

  var esc = R.esc, has = R.has, num = R.num;

  // The schema this page was written against (regime_backtest/export.py).
  var SCHEMA = 1;

  // Fixed categorical order, checked against the page's dark surface with the
  // palette validator (lightness band, chroma, colour-blind separation,
  // contrast). A rule keeps its colour on every chart; buy-and-hold is the
  // grey reference, not a series competing for a hue.
  var COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#9085e9", "#e66767"];
  var BENCH_COLOR = "#9aa5b1";

  function colorFor(index) { return index === 0 ? BENCH_COLOR : COLORS[(index - 1) % COLORS.length]; }

  // Fractions in the payload (0.103 = 10.3%), percent strings on the page.
  function fpct(v, digits) { return has(v) && !isNaN(v) ? num(v * 100, digits === undefined ? 1 : digits) + "%" : "—"; }
  function signed(v, digits) {
    if (!has(v) || isNaN(v)) return "—";
    return (v > 0 ? "+" : "") + fpct(v, digits);
  }
  function tone(v) { return !has(v) || isNaN(v) ? "" : v < 0 ? " down" : v > 0 ? " up" : ""; }

  function schemaOk(d) { return !!d && d.regime_schema === SCHEMA; }

  // The cost keys the payload carries, in order, as the switch shows them.
  function costKeys(d) {
    return (d.costs || []).map(function (c) { return String(Number(c)); });
  }

  // ------------------------------------------------------------- tables

  var SUMMARY_COLS = [
    ["CAGR", function (m) { return fpct(m.cagr); }],
    ["Vol", function (m) { return fpct(m.vol); }],
    ["Sharpe", function (m) { return num(m.sharpe, 2); }],
    ["Sortino", function (m) { return num(m.sortino, 2); }],
    ["Max DD", function (m) { return fpct(m.max_dd); }],
    ["Calmar", function (m) { return num(m.calmar, 2); }],
    ["Worst 12m", function (m) { return fpct(m.worst_12m); }],
    ["% invested", function (m) { return fpct(m.pct_invested, 0); }],
    ["Switches", function (m) { return num(m.switches, 0); }],
    ["Avg hold (days)", function (m) { return num(m.avg_hold_days, 0); }]
  ];

  function swatch(i) {
    return '<span class="rg-swatch" style="background:' + colorFor(i) + '" aria-hidden="true"></span>';
  }

  function table(head, rows, firstLeft) {
    return '<div class="tablewrap"><table class="stats rg-table"><thead><tr>' +
      head.map(function (h, i) {
        return "<th" + (i === 0 && firstLeft !== false ? "" : ' class="r"') + ">" + esc(h) + "</th>";
      }).join("") + "</tr></thead><tbody>" + rows.join("") + "</tbody></table></div>";
  }

  // `order` maps a rule name to its colour index, so a row's swatch matches its line.
  function summaryTable(rows, order, extra) {
    extra = extra || [];
    var head = ["Strategy", "Period"].concat(SUMMARY_COLS.map(function (c) { return c[0]; }))
      .concat(extra.map(function (c) { return c[0]; }));
    return table(head, rows.map(function (m) {
      var i = order && has(order[m.rule]) ? order[m.rule] : null;
      return "<tr><td>" + (i === null ? "" : swatch(i)) + esc(m.rule) + '</td><td class="r dim">' +
        esc(m.start) + " → " + esc(m.end) + "</td>" +
        SUMMARY_COLS.concat(extra).map(function (c) { return '<td class="r">' + c[1](m) + "</td>"; }).join("") +
        "</tr>";
    }));
  }

  function longestTable(rows, order) {
    return summaryTable(rows, order, [
      ["B&H Sharpe", function (m) { return num(m.bh_sharpe, 2); }],
      ["B&H CAGR", function (m) { return fpct(m.bh_cagr); }],
      ["B&H Max DD", function (m) { return fpct(m.bh_max_dd); }]
    ]);
  }

  // Sharpe and CAGR at every cost side by side: what the switch shows one at a time.
  function costTable(d) {
    var keys = costKeys(d);
    var names = (d.summary[keys[0]] || []).map(function (m) { return m.rule; });
    var head = ["Strategy"];
    keys.forEach(function (k) { head.push("CAGR @" + k + "bp", "Sharpe @" + k + "bp"); });
    return table(head, names.map(function (n, i) {
      return "<tr><td>" + esc(n) + "</td>" + keys.map(function (k) {
        var m = (d.summary[k] || [])[i] || {};
        return '<td class="r">' + fpct(m.cagr) + '</td><td class="r">' + num(m.sharpe, 2) + "</td>";
      }).join("") + "</tr>";
    }));
  }

  function crisisTable(c) {
    var head = ["Strategy"].concat(c.windows);
    return table(head, c.rows.map(function (r) {
      return "<tr><td>" + esc(r.rule) + "</td>" + r.cells.map(function (w) {
        return '<td class="r"><span class="rg-ret' + tone(w.ret) + '">' + fpct(w.ret) + "</span>" +
          '<br><span class="faint">DD ' + fpct(w.max_dd) + "</span></td>";
      }).join("") + "</tr>";
    }));
  }

  // Diverging around buy-and-hold's Sharpe for the same period: blue better,
  // orange worse, grey equal. Alpha carries the size, the value is printed in
  // every cell, so the colour is never the only way to read it.
  function heatStyle(v, bench, span) {
    if (!has(v) || isNaN(v)) return "";
    var t = Math.max(-1, Math.min(1, (v - bench) / (span || 1)));
    var rgb = t >= 0 ? "57,135,229" : "217,89,38";
    return "background:rgba(" + rgb + "," + (0.12 + 0.6 * Math.abs(t)).toFixed(2) + ")";
  }

  function heatSpan(panel) {
    var span = 0.05;
    panel.grid.forEach(function (row) {
      row.forEach(function (v) { if (has(v)) span = Math.max(span, Math.abs(v - panel.bh_sharpe)); });
    });
    return span;
  }

  function heatmap(panel, rowLabels, colLabels, mark) {
    var span = heatSpan(panel);
    return '<div class="rg-heat"><h4>' + esc(panel.title) + '</h4><p class="faint">' + esc(panel.start) +
      " → " + esc(panel.end) + " · buy-and-hold Sharpe " + num(panel.bh_sharpe, 2) + "</p>" +
      '<table class="stats rg-heattable"><thead><tr><th></th>' +
      colLabels.map(function (c) { return '<th class="r">' + esc(c) + "</th>"; }).join("") +
      "</tr></thead><tbody>" + panel.grid.map(function (row, r) {
        return "<tr><th>" + esc(rowLabels[r]) + "</th>" + row.map(function (v, c) {
          var isMark = mark && mark[0] === r && mark[1] === c;
          return '<td class="r' + (isMark ? " rg-mark" : "") + '" style="' + heatStyle(v, panel.bh_sharpe, span) +
            '"' + (isMark ? ' title="the headline setting"' : "") + ">" + num(v, 2) + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>";
  }

  // Which cell is the fixed default setting the headline tables use.
  function defaultCell(sweep) {
    var r = (sweep.exits || []).indexOf(sweep.default[0]);
    var c = (sweep.reentries || []).indexOf(sweep.default[1]);
    return r >= 0 && c >= 0 ? [r, c] : null;
  }

  function eventTable(rows) {
    if (!rows || !rows.length) return '<p class="empty">No events.</p>';
    return table(["Signal date", "Below peak", "1m", "3m", "6m", "12m", "Max DD before recovery",
                  "Trading days to prior peak"], rows.map(function (r) {
      return "<tr><td>" + esc(r.date) + '</td><td class="r">' + fpct(r.below_prior_peak) + "</td>" +
        ["1m", "3m", "6m", "12m"].map(function (k) {
          return '<td class="r"><span class="rg-ret' + tone(r[k]) + '">' + fpct(r[k]) + "</span></td>";
        }).join("") +
        '<td class="r">' + fpct(r.max_dd_before_recovery) + '</td><td class="r">' +
        (has(r.days_to_recover) ? num(r.days_to_recover, 0) : "not yet") + "</td></tr>";
    }));
  }

  function peakTroughTable(rows) {
    if (!rows || !rows.length) return "";
    return table(["Signal", "Days to peak", "Peak", "Days to trough", "Trough", "Peak→trough",
                  "Full 3y window"], rows.map(function (r) {
      return "<tr><td>" + esc(r.date) + '</td><td class="r">' + num(r.days_to_peak, 0) + '</td><td class="r">' +
        esc(r.peak_date) + '</td><td class="r">' + num(r.days_to_trough, 0) + '</td><td class="r">' +
        esc(r.trough_date) + '</td><td class="r">' + fpct(r.peak_to_trough) + '</td><td class="r">' +
        (r.window_complete ? "yes" : "no — data ends first") + "</td></tr>";
    }));
  }

  // ------------------------------------------------------------- verdicts

  // A headline that opens "Beats" is the only one that earns the green box;
  // everything else — within noise, about equal, worse — is the amber one.
  function verdictTone(headline) { return /^Beats\b/.test(String(headline || "")) ? "good" : "bad"; }

  function bottomLine(d, order) {
    return '<ul class="rg-bottom">' + d.verdicts.map(function (v) {
      return "<li>" + swatch(order[v.rule]) + "<b>" + esc(v.rule) + ":</b> " + esc(v.headline) + "</li>";
    }).join("") + "</ul>";
  }

  function verdictDetails(d) {
    return d.verdicts.map(function (v) {
      return '<details class="rg-verdict"><summary><b>' + esc(v.rule) + "</b></summary>" +
        '<div class="verdict ' + verdictTone(v.headline) + '">' + esc(v.headline) + "</div>" +
        (v.note ? '<p class="rg-note">' + esc(v.note) + "</p>" : "") +
        "<ul>" + (v.lines || []).map(function (l) { return "<li>" + esc(l) + "</li>"; }).join("") + "</ul></details>";
    }).join("");
  }

  // ------------------------------------------------------------- charts

  // Drawdown from the running peak, starting capital counted as a peak — the
  // same convention as metrics.drawdown_series.
  function drawdowns(equity) {
    var peak = 1, out = [];
    for (var i = 0; i < equity.length; i++) {
      var v = equity[i];
      if (!has(v)) { out.push(null); continue; }
      if (v > peak) peak = v;
      out.push(v / peak - 1);
    }
    return out;
  }

  function niceLogTicks(lo, hi) {
    var ticks = [], steps = [1, 2, 5];
    for (var e = Math.floor(Math.log10(lo)); e <= Math.ceil(Math.log10(hi)); e++) {
      for (var s = 0; s < steps.length; s++) {
        var t = steps[s] * Math.pow(10, e);
        if (t >= lo * 0.999 && t <= hi * 1.001) ticks.push(t);
      }
    }
    return ticks.length > 7 ? ticks.filter(function (t) { return /^1/.test(String(t)); }) : ticks;
  }

  // Drawdown axis: steps of 10% (20% past -60%), from 0 down to the floor.
  function linearTicks(lo) {
    var step = lo < -0.6 ? 0.2 : 0.1;
    var out = [];
    for (var t = 0; t >= lo - 1e-9; t -= step) out.push(Math.round(t * 100) / 100);
    return out;
  }

  // Label positions (sorted by y) moved apart until neighbours are `gap` apart,
  // kept inside [top, bottom]. Two passes: push down, then pull back up.
  function spreadLabels(items, gap, top, bottom) {
    for (var i = 1; i < items.length; i++) {
      if (items[i].y - items[i - 1].y < gap) items[i].y = items[i - 1].y + gap;
    }
    if (items.length && items[items.length - 1].y > bottom) items[items.length - 1].y = bottom;
    for (var j = items.length - 2; j >= 0; j--) {
      if (items[j + 1].y - items[j].y < gap) items[j].y = items[j + 1].y - gap;
    }
    if (items.length && items[0].y < top) items[0].y = top;
    return items;
  }

  /* Layout for a line chart: the scales, and an SVG string. `series` is
     [{name, values, color, width}], all on `dates`. `log` puts the y axis on a
     log scale (growth of $1); otherwise it is linear with 0 at the top
     (drawdowns). The tab adds the hover layer on top of this. */
  function lineChart(dates, series, opt) {
    opt = opt || {};
    var W = opt.width || 900, H = opt.height || 320;
    var padL = 52, padR = opt.padRight || 56, padT = 12, padB = 26;
    var pw = W - padL - padR, ph = H - padT - padB;
    var n = dates.length;
    var lo = Infinity, hi = -Infinity;
    series.forEach(function (s) {
      s.values.forEach(function (v) { if (has(v) && (!opt.log || v > 0)) { lo = Math.min(lo, v); hi = Math.max(hi, v); } });
    });
    if (!isFinite(lo)) { lo = 0; hi = 1; }
    if (!opt.log) {
      // Drawdowns: 0 at the top, the floor rounded down to a whole tick step.
      var step = lo < -0.6 ? 0.2 : 0.1;
      hi = 0;
      lo = Math.min(-step, Math.floor(lo / step - 1e-9) * step);
    }
    var f = opt.log ? Math.log : function (v) { return v; };
    var flo = f(lo), fhi = f(hi);
    if (flo === fhi) { flo -= 1; fhi += 1; }
    function X(i) { return padL + (n > 1 ? i / (n - 1) : 0) * pw; }
    function Y(v) { return padT + (1 - (f(v) - flo) / (fhi - flo)) * ph; }

    var grid = [];
    var ticks = opt.log ? niceLogTicks(lo, hi) : linearTicks(lo);
    ticks.forEach(function (t) {
      var y = Y(t).toFixed(1);
      grid.push('<line x1="' + padL + '" x2="' + (W - padR) + '" y1="' + y + '" y2="' + y + '" class="rg-grid"/>' +
        '<text x="' + (padL - 6) + '" y="' + y + '" class="rg-axis" text-anchor="end" dominant-baseline="middle">' +
        (opt.log ? esc(num(t, t < 10 ? 1 : 0)) + "×" : esc(num(Math.abs(t) < 1e-9 ? 0 : t * 100, 0)) + "%") +
        "</text>");
    });
    // One x label per few years, on the first week of the year.
    var lastYear = null, years = [];
    dates.forEach(function (d, i) {
      var y = String(d).slice(0, 4);
      if (y !== lastYear) { years.push([i, y]); lastYear = y; }
    });
    var every = Math.max(1, Math.ceil(years.length / Math.max(3, Math.floor(pw / 110))));
    years.forEach(function (p, k) {
      if (k % every) return;
      grid.push('<text x="' + X(p[0]).toFixed(1) + '" y="' + (H - 6) + '" class="rg-axis" text-anchor="middle">' +
        esc(p[1]) + "</text>");
    });

    var paths = series.map(function (s) {
      var d = "", pen = false;
      s.values.forEach(function (v, i) {
        if (!has(v) || (opt.log && v <= 0)) { pen = false; return; }
        d += (pen ? "L" : "M") + X(i).toFixed(1) + "," + Y(v).toFixed(1);
        pen = true;
      });
      var fill = s.fill ? '<path d="' + d + "L" + X(n - 1).toFixed(1) + "," + Y(0).toFixed(1) + "L" + X(0).toFixed(1) +
        "," + Y(0).toFixed(1) + 'Z" fill="' + s.fill + '" stroke="none"/>' : "";
      return fill + (s.fill && s.lineless ? "" : '<path d="' + d + '" fill="none" stroke="' + s.color +
        '" stroke-width="' + (s.width || 2) + '" stroke-linejoin="round"/>');
    });
    // Direct labels at the right edge for each line's last value, pushed apart
    // so lines that finish close together do not print on top of each other.
    var labels = [];
    if (opt.endLabels) {
      var ends = series.map(function (s) {
        var v = s.values[s.values.length - 1];
        return has(v) ? { v: v, y: Y(v), color: s.color } : null;
      }).filter(Boolean).sort(function (a, b) { return a.y - b.y; });
      spreadLabels(ends, 13, padT, padT + ph);
      labels = ends.map(function (e) {
        return '<text x="' + (W - padR + 6) + '" y="' + e.y.toFixed(1) + '" class="rg-endlabel" fill="' + e.color +
          '" dominant-baseline="middle">' + esc(num(e.v, 1)) + "×</text>";
      });
    }

    var svg = '<svg class="rg-chart" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img"' +
      (opt.label ? ' aria-label="' + esc(opt.label) + '"' : "") + ">" + grid.join("") + paths.join("") +
      labels.join("") + '<line class="rg-cross" x1="0" x2="0" y1="' + padT + '" y2="' + (padT + ph) +
      '" visibility="hidden"/></svg>';
    return { svg: svg, W: W, padL: padL, padR: padR, n: n,
             // The week under a horizontal position in viewBox units.
             indexAt: function (x) {
               if (n < 2) return 0;
               return Math.max(0, Math.min(n - 1, Math.round((x - padL) / pw * (n - 1))));
             },
             xAt: X };
  }

  function tipHtml(date, rows, fmt) {
    return "<b>" + esc(date) + "</b>" + rows.map(function (r) {
      return '<div class="rg-tiprow"><span class="rg-swatch" style="background:' + r.color + '"></span>' +
        esc(r.name) + ' <span class="r">' + fmt(r.value) + "</span></div>";
    }).join("");
  }

  return {
    SCHEMA: SCHEMA, COLORS: COLORS, BENCH_COLOR: BENCH_COLOR, colorFor: colorFor,
    schemaOk: schemaOk, costKeys: costKeys, fpct: fpct, signed: signed,
    summaryTable: summaryTable, longestTable: longestTable, costTable: costTable, crisisTable: crisisTable,
    heatmap: heatmap, heatStyle: heatStyle, defaultCell: defaultCell,
    eventTable: eventTable, peakTroughTable: peakTroughTable,
    verdictTone: verdictTone, bottomLine: bottomLine, verdictDetails: verdictDetails,
    drawdowns: drawdowns, lineChart: lineChart, niceLogTicks: niceLogTicks, linearTicks: linearTicks,
    spreadLabels: spreadLabels, tipHtml: tipHtml
  };
});
