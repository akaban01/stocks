/* Spread Scanner — frontend: the Scanner tab (the sortable ranked table).
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.renderScanner = renderScanner;

  // -------------------------------------------------------------- scanner

  var SORT = { key: "rank", dir: 1 };

  var COLUMNS = [
    { k: "rank", h: "#", f: function (s) { return App.num(s.rank, 0); }, r: true },
    // The ticker is a link into the Backtest tab on that name. An ordinary
    // anchor, because the fragment handler above already knows what to do with
    // one — no click handler, and it works from a middle-click or a copied
    // address like any other link on the page.
    { k: "ticker", h: "Ticker", cls: "t", f: function (s) {
        return '<a class="totest" href="#backtest#' + App.esc(s.ticker) + '" title="' +
          App.esc("backtest a rule on " + s.ticker) + '">' + App.esc(s.ticker) + "</a>";
      } },
    { k: "price", h: "Price", f: function (s) { return App.num(s.price, 2); }, r: true },
    { k: "score", h: "Score", r: true, f: function (s) {
        var hue = 8 + (Number(s.score) / 100) * 132;
        return '<span class="pill" style="background:hsl(' + hue.toFixed(0) + ' 70% 40%)">' +
          App.num(s.score, 0) + "</span>";
      } },
    { k: "action", h: "Do", f: function (s) {
        var a = (s.recommendation || {}).action || "NO_DATA";
        var meta = App.ref("actions." + a) || {};
        return '<span class="tag ' + App.tone(a) + '">' + App.esc(meta.verb || "—") + "</span>";
      } },
    { k: "strategy", h: "Strategy", f: function (s) {
        return App.esc(((s.recommendation || {}).plan || {}).name || "—");
      } },
    { k: "iv_rank", h: "IV rank", r: true, v: function (s) { return (s.options || {}).iv_rank; },
      f: function (s) { return App.has((s.options || {}).iv_rank) ? App.num(s.options.iv_rank, 0) : "—"; } },
    { k: "premium_score", h: "Premium", r: true, v: function (s) { return (s.options || {}).premium_score; },
      f: function (s) {
        var o = s.options; if (!o) return "—";
        return '<span class="tag ' + (o.premium_state === "cheap" ? "buy" : o.premium_state === "rich" ? "sell" : "neutral") +
          '">' + App.num(o.premium_score, 0) + "</span>";
      } },
    { k: "implied_move_pct", h: "Implied", r: true, v: function (s) { return (s.options || {}).implied_move_pct; },
      f: function (s) { return App.has((s.options || {}).implied_move_pct) ? App.pct(s.options.implied_move_pct) : "—"; } },
    { k: "em_pct", h: "Realized", r: true, f: function (s) { return App.pct(s.em_pct); } },
    { k: "squeeze", h: "Squeeze", v: function (s) { return s.squeeze_fired ? 999 : (s.squeeze_on ? s.squeeze_days : -1); },
      f: function (s) {
        if (s.squeeze_fired) {
          return '<span class="fired">fired ' + ({ up: "▲", down: "▼" }[s.fired_dir] || "") + "</span>";
        }
        return s.squeeze_on ? "locked " + App.num(s.squeeze_days, 0) + "d" : "—";
      } },
    { k: "down_1sigma", h: "Down 1σ", r: true, f: function (s) { return App.num(s.down_1sigma, 2); } },
    { k: "up_1sigma", h: "Up 1σ", r: true, f: function (s) { return App.num(s.up_1sigma, 2); } },
    { k: "lean", h: "Lean", f: function (s) { return App.esc(s.lean || "—"); } },
    { k: "hv_annual", h: "HV%", r: true, f: function (s) { return App.num(s.hv_annual, 0); } },
    { k: "earnings_in_days", h: "Earnings", r: true, f: function (s) {
        if (!App.has(s.earnings_in_days) || s.earnings_in_days < 0) return "—";
        var win = Math.round((s.horizon_days || 10) * 1.4);
        var txt = App.num(s.earnings_in_days, 0) + "d";
        return s.earnings_in_days <= win ? '<span class="warncell">' + txt + "</span>" : txt;
      } },
    { k: "screen", h: "Screen",
      v: function (s) {
        var c = (s.screen || {}).compliant;
        return c === false ? 0 : c === true ? 2 : 1;      // worst sorts first
      },
      f: function (s) {
        var sc = s.screen;
        if (!sc) return '<span class="dim">—</span>';
        if (sc.compliant === true) return '<span class="dim">ok</span>';
        return '<span class="flagtext" title="' + App.esc((sc.reasons || []).join("; ")) + '">' +
          (sc.compliant === false ? "fails" : "?") + "</span>";
      } },
    { k: "debt_ratio", h: "Debt%", r: true, f: function (s) { return App.has(s.debt_ratio) ? App.pct(s.debt_ratio * 100, 0) : "—"; } },
    { k: "cash_ratio", h: "Cash%", r: true, f: function (s) { return App.has(s.cash_ratio) ? App.pct(s.cash_ratio * 100, 0) : "—"; } }
  ];

  function renderScanner() {
    var sigs = (App.store.scan.signals || []).slice();
    if (!sigs.length) {
      App.$("#scantable").innerHTML = '<p class="empty">No signals in the last run.</p>';
      return;
    }
    var col = COLUMNS.filter(function (c) { return c.k === SORT.key; })[0] || COLUMNS[0];
    var val = col.v || function (s) { return s[col.k]; };
    sigs.sort(function (a, b) {
      var x = val(a), y = val(b);
      if (x === null || x === undefined || x === "") x = -Infinity;
      if (y === null || y === undefined || y === "") y = -Infinity;
      if (typeof x === "string" || typeof y === "string") {
        return String(x).localeCompare(String(y)) * SORT.dir;
      }
      return (x - y) * SORT.dir;
    });

    var head = COLUMNS.map(function (c) {
      var sort = c.k === SORT.key ? (SORT.dir === 1 ? "ascending" : "descending") : "none";
      return App.sortableTh(c.k, c.h, null, sort);
    }).join("");
    var body = sigs.map(function (s) {
      return "<tr>" + COLUMNS.map(function (c) {
        return '<td class="' + (c.cls || "") + (c.r ? " r" : "") + '">' + c.f(s) + "</td>";
      }).join("") + "</tr>";
    }).join("");

    App.$("#scantable").innerHTML = '<div class="tablewrap"><table class="scan"><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>";

    App.wireSort(document.querySelectorAll("#scantable th"), function (k) {
      if (SORT.key === k) SORT.dir = -SORT.dir;
      else { SORT.key = k; SORT.dir = (k === "rank" || k === "ticker") ? 1 : -1; }
      renderScanner();
      // Keep the caller where they were: re-rendering replaced the node they
      // were standing on, and focus would otherwise fall back to the document.
      var again = App.$('#scantable th[data-key="' + k + '"]');
      if (again) again.focus();
    });
  }
})(window.SpreadApp = window.SpreadApp || {});
