/* Spread Scanner — the frontend's pure rendering helpers.
 *
 * Every function here takes payload data and returns an HTML string; none of
 * them touches the DOM or the page's state. They are split out of the page scripts (assets/app/) for
 * one reason: the page is built by concatenating strings into innerHTML,
 * and the only thing standing between a payload field and the page's markup is
 * remembering to call esc() on it. Some of that payload is text scraped from
 * third-party pages. tests/test_render_js.py runs *this file* under node with
 * payloads whose every string is an HTML injection, and fails if any of it
 * reaches the output unescaped.
 *
 * Browser: exposes window.SpreadRender. Node: module.exports.
 */
(function (root, factory) {
  var api = factory();
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpreadRender = api;
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function has(v) { return v !== null && v !== undefined && v !== ""; }

  function num(v, digits, fallback) {
    if (!has(v) || isNaN(v)) return fallback === undefined ? "—" : fallback;
    return Number(v).toLocaleString(undefined, {
      minimumFractionDigits: digits || 0, maximumFractionDigits: digits || 0
    });
  }

  function money(v, digits) { return has(v) && !isNaN(v) ? "$" + num(Math.abs(v), digits === undefined ? 2 : digits) : "—"; }

  // money() drops the sign, which is right for a price and wrong for a P&L.
  function cash(v, digits) {
    if (!has(v) || isNaN(v)) return "—";
    return (v < 0 ? "−$" : "$") + num(Math.abs(v), digits === undefined ? 0 : digits);
  }

  function pct(v, digits) { return has(v) && !isNaN(v) ? num(v, digits === undefined ? 1 : digits) + "%" : "—"; }

  function multiExpiry(plan) {
    var seen = {};
    (plan.legs || []).forEach(function (l) { if (l.expiry) seen[l.expiry] = 1; });
    return Object.keys(seen).length > 1;
  }

  function sizeCell(sizing) {
    if (!sizing || !has(sizing.contracts)) return "—";
    if (sizing.over_budget) {
      return '<span class="warncell" title="' + esc(sizing.note || "") + '">over budget</span>';
    }
    return '<span title="' + esc(sizing.note || "") + '">' + num(sizing.contracts, 0) + "×</span>";
  }

  function legsTable(plan) {
    if (!plan.legs || !plan.legs.length) return "";
    // A diagonal's legs sit in different expiries, so the single expiry in the
    // header would be wrong for one of them. Show it per leg when they differ.
    var mixed = multiExpiry(plan);
    var rows = plan.legs.map(function (l) {
      var side = String(l.action || "").toLowerCase();
      var what = l.right === "share"
        ? num(l.qty, 0) + " shares"
        : num(l.qty, 0) + "× " + num(l.strike, 2) + " " + esc(l.right);
      return "<tr>" +
        '<td class="side ' + esc(side) + '">' + esc(side) + "</td>" +
        "<td>" + what + (mixed && l.expiry ? ' <span class="dim">' + esc(l.expiry) + "</span>" : "") + "</td>" +
        // A mid off the last trade is not a price anyone is quoting now.
        '<td class="r">' + (has(l.mid) ? money(l.mid) : "—") +
          (l.mid_source === "last"
            ? ' <span class="warncell" title="No live bid/ask — this is the last traded price">last</span>'
            : "") + "</td>" +
        '<td class="r dim">' + (has(l.bid) && has(l.ask) ? money(l.bid) + " / " + money(l.ask) : "—") + "</td>" +
        '<td class="r dim">' + (has(l.iv) ? pct(l.iv, 0) : "—") + "</td>" +
        '<td class="r dim">' + (has(l.open_interest) ? num(l.open_interest, 0) : "—") + "</td>" +
        "</tr>";
    }).join("");

    var netTxt = "—", netCls = "";
    if (has(plan.net)) {
      netCls = plan.net > 0 ? "debit" : "credit";
      netTxt = (plan.net > 0 ? "Debit " : "Credit ") + money(plan.net);
    }
    // `net` is the planned fill, between the mid (best case) and the natural
    // price (worst). Show both ends so the spread being crossed is visible.
    var fillTxt = has(plan.net_mid) && has(plan.net_natural) && plan.net_mid !== plan.net_natural
      ? '<span class="dim" title="' + esc(plan.fill_basis || "") + '"> · mid ' + money(plan.net_mid) +
        ", natural " + money(plan.net_natural) + "</span>"
      : "";
    var risk = [
      // "Uncapped" is a property of the payoff, not of missing data: a long
      // straddle has no max profit but still has a max loss (the debit). A plan
      // whose legs could not be priced has neither, and must not claim uncapped.
      ["Max profit", has(plan.max_profit) ? money(plan.max_profit, 0) : (has(plan.max_loss) ? "uncapped" : "—")],
      ["Max loss", has(plan.max_loss) ? money(plan.max_loss, 0) : (plan.risk === "undefined" ? "undefined" : "—")],
      ["Breakeven", plan.breakevens && plan.breakevens.length
        ? plan.breakevens.map(function (b) { return num(b, 2); }).join(" / ") : "—"],
      ["Prob. of profit at expiry", has(plan.pop)
        ? '<span title="' + esc(plan.pop_basis || "") + '">' + pct(plan.pop * 100, 0) + "</span>" : "—"],
      ["Credit / width", has(plan.credit_to_width) ? pct(plan.credit_to_width * 100, 0) : "—"],
      ["Size", sizeCell(plan.sizing)]
    ].map(function (kv) {
      return "<div><span class=\"k\">" + esc(kv[0]) + "</span><span class=\"v\">" + kv[1] + "</span></div>";
    }).join("");

    return '<div class="order">' +
      '<div class="order-head"><span class="t">The order</span>' +
      '<span class="exp">' + (mixed ? "two expiries" : esc(plan.expiry || "")) +
      (has(plan.dte) ? " · " + num(plan.dte, 0) + " DTE" : "") + "</span>" +
      '<span class="net ' + netCls + '">' + netTxt + " per spread</span>" + fillTxt + "</div>" +
      '<table class="legs"><thead><tr><th>Side</th><th>Contract</th>' +
      '<th class="r">Mid</th><th class="r">Bid / Ask</th><th class="r">IV</th><th class="r">OI</th>' +
      "</tr></thead><tbody>" + rows + "</tbody></table>" +
      '<div class="riskrow">' + risk + "</div></div>";
  }

  function noteList(title, items, cls) {
    if (!items || !items.length) return "";
    return '<div class="notes ' + esc(cls || "") + '"><div class="t">' + esc(title) + "</div><ul>" +
      items.map(function (t) { return "<li>" + t + "</li>"; }).join("") + "</ul></div>";
  }

  function manageBlock(plan) {
    var m = plan.manage || {};
    var rows = [["Target", m.profit_target], ["Stop", m.stop], ["Time", m.time_stop]]
      .filter(function (r) { return r[1]; })
      .map(function (r) {
        return '<div class="row"><span>' + esc(r[0]) + "</span><span>" + esc(r[1]) + "</span></div>";
      }).join("");
    if (!rows) return "";
    return '<div class="notes"><div class="t">How to manage it</div><div class="manage">' + rows + "</div></div>";
  }

  function altBlock(alts) {
    if (!alts || !alts.length) return "";
    var body = alts.map(function (a) {
      var netTxt = has(a.net) ? (a.net > 0 ? "debit " : "credit ") + money(a.net) : "not priced";
      var legs = (a.legs || []).map(function (l) {
        return l.right === "share" ? "own shares"
          : l.action + " " + num(l.strike, 2) + " " + l.right;
      }).join(", ");
      return '<div class="alt"><div class="n">' + esc(a.name) + "</div>" +
        '<div class="d">' + esc(legs) + " — " + netTxt +
        (has(a.max_loss) ? " · max loss " + money(a.max_loss, 0) : "") +
        (has(a.pop) ? " · POP at expiry " + pct(a.pop * 100, 0) : "") + "</div>" +
        '<div class="d">' + esc(a.playbook || a.thesis || "") + "</div></div>";
    }).join("");
    return '<details class="alts"><summary>Other ways to express this (' + alts.length + ")</summary>" +
      body + "</details>";
  }

  function riskFormNote(plan) {
    var rf = plan.risk_form;
    if (!rf || !rf.note) return "";
    return '<div class="riskform"><b>What secures it (' +
      esc(String(rf.tier || "").replace(/_/g, " ")) + ")</b> — " + esc(rf.note) + "</div>";
  }

  // `longDays` labels the second band column: the longer realized-vol window
  // the options layer compares implied vol against. Older payloads lack it.
  function statsTable(rows, labelHead, longDays) {
    var longCol = rows.some(function (b) { return has(b.broke_long_band_pct); });
    return '<table class="stats"><thead><tr><th>' + esc(labelHead) + "</th>" +
      '<th class="r">bars</th><th class="r">avg |move|</th><th class="r">expand</th>' +
      '<th class="r">broke own band</th>' +
      (longCol ? '<th class="r">broke ' + esc(longDays || 60) + "d band</th>" : "") +
      "</tr></thead><tbody>" +
      rows.map(function (b) {
        return "<tr><td>" + esc(b.label) + "</td>" +
          '<td class="r">' + num(b.bars, 0) + "</td>" +
          '<td class="r">' + pct(b.avg_abs_move_pct) + "</td>" +
          '<td class="r">' + (has(b.expansion) ? num(b.expansion, 2) + "×" : "—") + "</td>" +
          '<td class="r">' + pct(b.broke_band_pct, 0) + "</td>" +
          (longCol ? '<td class="r">' + pct(b.broke_long_band_pct, 0) + "</td>" : "") +
          "</tr>";
      }).join("") + "</tbody></table>";
  }

  // The test the trades have to pass: did the move beat what the option market
  // charged? Reads the implied-vol history the scan logs every day, so it says
  // how far along that history is until enough of it has matured.
  function impliedSection(imp) {
    if (!imp) return "";
    var head = "<h3>Against implied volatility</h3>";
    if (!imp.ok) {
      return head + '<p class="empty">' + esc(imp.note || "Not enough history yet.") +
        (imp.logged_rows ? " " + num(imp.logged_rows, 0) + " readings logged since " +
          esc(imp.first_date) + "." : "") + "</p>";
    }
    var b = imp.buckets;
    var rows = ["coiled", "calm", "cheap", "rich", "coiled_cheap"].map(function (k) {
      var r = b[k];
      return "<tr><td>" + esc(r.label) + '</td><td class="r">' + num(r.n, 0) + "</td>" +
        '<td class="r">' + pct(r.beat_implied_pct, 0) + "</td>" +
        '<td class="r">' + (has(r.avg_straddle_return_pct)
          ? (r.avg_straddle_return_pct > 0 ? "+" : "") + pct(r.avg_straddle_return_pct, 0) : "—") +
        "</td></tr>";
    }).join("");
    return head + "<p>" + esc(imp.text) + "</p>" +
      '<table class="stats"><thead><tr><th>Readings</th><th class="r">n</th>' +
      '<th class="r">moved more than implied</th><th class="r">model straddle return</th>' +
      "</tr></thead><tbody>" + rows + "</tbody></table>";
  }

  // Does the lean or the squeeze release call direction better than the base
  // rate? Only reads that do are traded on (strategy.direction_evidence).
  function directionSection(dir) {
    if (!dir || !dir.reads) return "";
    var keys = ["lean_bullish", "lean_bearish", "fired_bullish", "fired_bearish"];
    var rows = keys.filter(function (k) { return dir.reads[k]; }).map(function (k) {
      var r = dir.reads[k], ci = r.ci95_pts || [];
      return "<tr><td>" + esc(r.label) + '</td><td class="r">' + num(r.n, 0) + "</td>" +
        '<td class="r">' + pct(r.hit_pct, 0) + '</td><td class="r">' + pct(r.base_pct, 0) + "</td>" +
        '<td class="r">' + (has(r.edge_pts) ? (r.edge_pts > 0 ? "+" : "") + num(r.edge_pts, 0) : "—") +
        (has(ci[0]) ? ' <span class="dim">(' + num(ci[0], 0) + " to " + num(ci[1], 0) + ")</span>" : "") +
        '</td><td class="r">' + (r.proven ? "traded on" : "not traded on") + "</td></tr>";
    }).join("");
    return "<h3>Does the direction read work?</h3><p>" + esc(dir.text) + "</p>" +
      '<table class="stats"><thead><tr><th>Read</th><th class="r">n</th>' +
      '<th class="r">went that way</th><th class="r">base rate</th>' +
      '<th class="r">edge, pts (95% CI)</th><th class="r"></th></tr></thead><tbody>' +
      rows + "</tbody></table>";
  }

  return { directionSection: directionSection, esc: esc, has: has, num: num, money: money, cash: cash, pct: pct,
           multiExpiry: multiExpiry, sizeCell: sizeCell, legsTable: legsTable,
           noteList: noteList, manageBlock: manageBlock, altBlock: altBlock,
           riskFormNote: riskFormNote, statsTable: statsTable, impliedSection: impliedSection };
});
