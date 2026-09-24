/* Spread Scanner — frontend: the Spreads tab (the ≈13-month table, the near-term
 * one-direction table, and their shared detail panel).
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.renderSpreads = renderSpreads;
  App.wireSpreadViews = wireSpreadViews;

  // --------------------------------------------------------------- spreads
  //
  // The ~13-month view. Rows are one candidate each, flattened across tickers
  // so structures can be compared side by side; the per-ticker context (the
  // summary, and the caveats that apply to every candidate for that name)
  // opens in one panel below the table rather than repeating on every row.

  // "pick" is the default pseudo-sort: the rows the scanner points at first,
  // then by ticker. Clicking any header replaces it with a plain column sort.
  var SPREAD_SORT = { key: "pick", dir: 1 };
  var spreadFilters = { keys: new Set(), preferredOnly: false };

  /* Two views of the same table. "long" is the ≈13-month block each signal
     carries as `long_dated`; "near" is the bull and bear verticals on the near
     expiry, carried as `near_term`. Same row shape, so one table serves both. */
  var SPREAD_VIEWS = { long: "long_dated", near: "near_term" };
  var spreadView = "long";
  try {
    var savedSpreadView = localStorage.getItem("spreadview");
    if (SPREAD_VIEWS[savedSpreadView]) spreadView = savedSpreadView;
  } catch (e) { /* private mode */ }

  function showSpreadView(view) {
    if (!SPREAD_VIEWS[view]) view = "long";
    if (view !== spreadView) {
      // The structures differ between the views, so a structure filter from
      // one would hide everything in the other.
      spreadFilters.keys.clear();
      SPREAD_SORT = { key: "pick", dir: 1 };
    }
    spreadView = view;
    var buttons = document.querySelectorAll("#spreadviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed",
        buttons[i].dataset.spreadview === view ? "true" : "false");
    }
    var parts = document.querySelectorAll('#panel-spreads > [data-spreadview]');
    for (var j = 0; j < parts.length; j++) parts[j].hidden = parts[j].dataset.spreadview !== view;
    try { localStorage.setItem("spreadview", view); } catch (e) { /* private mode */ }
  }

  function wireSpreadViews() {
    showSpreadView(spreadView);
    var buttons = document.querySelectorAll("#spreadviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () {
        showSpreadView(this.dataset.spreadview);
        if (App.store.scan) renderSpreads(true);
      });
    }
  }


  function legSummary(plan) {
    var txt = (plan.legs || []).map(function (l) {
      if (l.right === "share") return "own 100";
      return (l.action === "buy" ? "+" : "−") + App.num(l.strike, 0) +
        String(l.right || "").charAt(0);
    }).join(" ");
    return App.esc(txt) + (App.multiExpiry(plan) ? ' <span class="faint">diag</span>' : "");
  }

  function rewardToRisk(plan) {
    if (!App.has(plan.max_profit) || !App.has(plan.max_loss) || !plan.max_loss) return null;
    return plan.max_profit / plan.max_loss;
  }

  // The same return, put on a yearly footing. Without it a 13-month spread at
  // 0.6x looks better than a monthly one at 0.3x, when the monthly trade earns
  // that 0.3x twelve times over the same capital. Simple (not compounded)
  // annualisation: it assumes you could repeat the trade, which is exactly the
  // assumption a reader comparing the two tabs is making.
  // Deliberately not defined for a structure whose legs expire on different
  // dates. Neither denominator is honest for a diagonal: over its own 378 days
  // the figure understates, because the maximum lands at the short leg's expiry
  // three weeks out; over those 21 days it wildly overstates, because it then
  // implies seventeen assignments a year at the same strike. On the 2026-09-04
  // scan the two readings for PG were 12% and 222%. The quantity the column
  // means — a return you could plausibly repeat — is simply not defined here:
  // the diagonal earns from rolling the short leg, and no roll is in max_profit.
  // `profit_horizon_dte` still ships, and now explains the blank rather than
  // producing a number.
  function annualisedReturn(plan) {
    if (App.multiExpiry(plan)) return null;
    var rr = rewardToRisk(plan);
    if (rr === null || !App.has(plan.dte) || !plan.dte) return null;
    return rr * 365 / plan.dte;
  }

  // One row per candidate, with the parent ticker's block carried along so the
  // detail panel can show that name's summary and caveats.
  function spreadRows() {
    var out = [];
    var field = SPREAD_VIEWS[spreadView];
    (App.store.scan.signals || []).forEach(function (s) {
      var ld = s[field];
      if (!ld) return;
      (ld.candidates || []).forEach(function (plan, i) {
        out.push({
          id: s.ticker + "-" + i,
          ticker: s.ticker, price: s.price, block: ld, plan: plan,
          preferred: ld.preferred === plan.key
        });
      });
    });
    return out;
  }

  var ALL_COLUMNS = [
    { k: "ticker", h: "Ticker", cls: "t",
      f: function (r) {
        return App.esc(r.ticker) + (r.preferred ? ' <span class="pick" title="What the ' +
          'scanner’s directional read points at for this name">pick</span>' : "");
      } },
    { k: "name", h: "Structure", v: function (r) { return r.plan.name; },
      f: function (r) { return App.esc(r.plan.name || r.plan.key); } },
    { k: "dte", h: "Expiry", v: function (r) { return r.plan.dte; },
      f: function (r) {
        return App.esc(r.plan.expiry || "—") +
          '<span class="dim"> · ' + App.num(r.plan.dte, 0) + "d</span>";
      } },
    { k: "legs", h: "Legs", cls: "mono", v: function (r) { return r.plan.key; },
      f: function (r) { return legSummary(r.plan); } },
    { k: "net", h: "Net", r: true, v: function (r) { return r.plan.net; },
      f: function (r) {
        if (!App.has(r.plan.net)) return "—";
        var d = r.plan.net > 0;
        return '<span class="net ' + (d ? "debit" : "credit") + '">' +
          (d ? "debit " : "credit ") + App.money(r.plan.net, 0) + "</span>";
      } },
    // A leg with no two-sided market leaves these null. That is "not priced",
    // which must not be shown as an uncapped win or an undefined loss.
    { k: "max_profit", h: "Max profit", r: true, v: function (r) { return r.plan.max_profit; },
      f: function (r) {
        if (App.has(r.plan.max_profit)) return App.money(r.plan.max_profit, 0);
        return App.has(r.plan.max_loss) ? "uncapped" : "—";
      } },
    { k: "max_loss", h: "Max loss", r: true, v: function (r) { return r.plan.max_loss; },
      f: function (r) {
        if (App.has(r.plan.max_loss)) return App.money(r.plan.max_loss, 0);
        return r.plan.risk === "undefined" ? "undefined" : "—";
      } },
    { k: "rr", h: "R : R", r: true, v: function (r) { return rewardToRisk(r.plan); },
      f: function (r) {
        var v = rewardToRisk(r.plan);
        return v === null ? "—" : App.num(v, 2) + "×";
      } },
    { k: "ror", h: "RoR / yr", r: true, v: function (r) { return annualisedReturn(r.plan); },
      f: function (r) {
        var v = annualisedReturn(r.plan);
        if (v !== null) {
          return '<span title="Max profit over max loss, put on a yearly footing so it ' +
            'compares with a monthly trade. Assumes the position could be repeated; it is not ' +
            'a forecast.">' + App.pct(v * 100, 0) + "</span>";
        }
        if (App.multiExpiry(r.plan)) {
          var horizon = App.has(r.plan.profit_horizon_dte)
            ? App.num(r.plan.profit_horizon_dte, 0) + " days"
            : "the short leg's expiry";
          return '<span class="dim" title="Not annualised: this trade\u2019s legs expire on ' +
            'different dates. Its maximum lands at ' + horizon + ', not at the ' +
            App.num(r.plan.dte, 0) + '-day expiry in the Expiry column, and it is a single ' +
            'assignment rather than something you repeat — the structure actually earns by ' +
            'rolling the short leg, which no figure here counts.">n/a</span>';
        }
        return "—";
      } },
    { k: "breakevens", h: "Breakeven", r: true,
      v: function (r) { return (r.plan.breakevens || [])[0]; },
      f: function (r) {
        var b = r.plan.breakevens || [];
        return b.length ? b.map(function (x) { return App.num(x, 2); }).join(" / ") : "—";
      } },
    { k: "pop", h: "POP", r: true, v: function (r) { return r.plan.pop; },
      f: function (r) { return App.has(r.plan.pop) ? App.pct(r.plan.pop * 100, 0) : "—"; } },
    { k: "size", h: "Size", r: true,
      v: function (r) { return (r.plan.sizing || {}).contracts; },
      f: function (r) { return App.sizeCell(r.plan.sizing); } }
  ];

  function spreadDetail(r) {
    var b = r.block;
    var head = '<div class="sd-head">' + App.esc(r.ticker) + " at " + App.num(r.price, 2) +
      " · " + App.esc(b.expiry) + " · " + App.num(b.dte, 0) + " days" +
      (spreadView === "near" && b.premium_state ? " · premium " + App.esc(b.premium_state) : "") +
      (App.has(b.iv_annual) ? " · ATM IV " + App.pct(b.iv_annual, 0) : "") +
      " · liquidity " + App.esc(b.liquidity || "unknown") +
      (App.has(b.atm_spread_pct) ? " (spread " + App.pct(b.atm_spread_pct, 0) + ")" : "") + "</div>";
    // The block summary is about the ticker's pick. On any other row it would
    // read as a description of the structure you just opened, which it is not.
    var sum = b.summary || "";
    if (b.preferred && !r.preferred) {
      var pick = (b.candidates || []).filter(function (c) { return c.key === b.preferred; })[0];
      sum = "Not the pick for " + r.ticker + " — the scanner points at the " +
        ((pick && pick.name) || b.preferred) + " here. This is one of the other structures " +
        "the chain supports.";
    }
    var body = [head, '<p class="sd-sum">' + App.esc(sum) + "</p>", App.legsTable(r.plan)];
    if (r.plan.playbook) {
      body.push('<div class="notes"><div class="t">What this trade is</div>' +
        '<p style="margin:0;font-size:.87rem;color:var(--text)">' + App.esc(r.plan.playbook) + "</p></div>");
    }
    body.push(App.manageBlock(r.plan));
    body.push(App.noteList("Watch out", (b.warnings || []).map(App.esc), "warns"));
    body.push(App.riskFormNote(r.plan));
    return '<div class="spread-detail">' + body.filter(Boolean).join("") + "</div>";
  }

  function renderSpreadFilters(rows) {
    // Labels come from each structure's own name — deriving them from the key
    // would print "Poor Mans Covered Call".
    var counts = {}, labels = {};
    rows.forEach(function (r) {
      counts[r.plan.key] = (counts[r.plan.key] || 0) + 1;
      labels[r.plan.key] = r.plan.name || r.plan.key;
    });
    var keys = Object.keys(counts).sort(function (a, b) {
      return labels[a].localeCompare(labels[b]);
    });
    var html = keys.map(function (k) {
      var on = spreadFilters.keys.size === 0 || spreadFilters.keys.has(k);
      return '<button class="chip" data-key="' + App.esc(k) + '" aria-pressed="' + (on ? "true" : "false") +
        '">' + App.esc(labels[k]) + '<span class="n">' + counts[k] + "</span></button>";
    }).join("");
    html += '<button class="chip pickonly" data-only="1" aria-pressed="' +
      (spreadFilters.preferredOnly ? "true" : "false") + '">Picks only</button>';
    App.$("#spreadfilters").innerHTML = html;

    var chips = document.querySelectorAll("#spreadfilters .chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].addEventListener("click", function () {
        if (this.dataset.only) {
          spreadFilters.preferredOnly = !spreadFilters.preferredOnly;
        } else {
          var k = this.dataset.key;
          if (spreadFilters.keys.has(k)) spreadFilters.keys.delete(k);
          else spreadFilters.keys.add(k);
          if (spreadFilters.keys.size === keys.length) spreadFilters.keys.clear();
        }
        renderSpreads(true);
      });
    }
  }

  function renderSpreads(force) {
    var host = App.$("#spreadtable");
    if (!force && host.dataset.done) return;
    host.dataset.done = "1";

    var all = spreadRows();
    if (spreadView === "near") return renderNear(host, all);
    var summary = App.store.scan.long_dated || {};
    App.$("#spreadmeta").innerHTML = all.length
      ? "<b>" + App.num(summary.candidates || all.length, 0) + "</b> long-dated spreads across <b>" +
        App.num(summary.tickers, 0) + "</b> names · expiries " +
        App.esc((summary.expiries || []).join(", ")) + " · target " +
        App.num(summary.target_days, 0) + " days · " + App.num(summary.preferred, 0) +
        " carry a directional pick"
      : "";

    if (!all.length) {
      host.innerHTML = '<p class="empty">No long-dated spreads in the last run.</p>' +
        '<p class="empty faint">Only the top-ranked names are priced. Of those, some have no ' +
        "listed expiry near 13 months — LEAPS exist mostly on large caps — and others have the " +
        "expiry but no usable quotes on it. A scan that runs outside US market hours reads a " +
        "chain with no bid, no ask and no open interest, which is not enough to price a spread " +
        "against. The scheduled run is half an hour after the close for that reason.</p>";
      App.$("#spreadfilters").innerHTML = "";
      return;
    }
    renderTable(host, all);
  }

  function renderNear(host, all) {
    var summary = App.store.scan.near_term || {};
    App.$("#spreadmeta").innerHTML = all.length
      ? "<b>" + App.num(summary.candidates || all.length, 0) + "</b> one-direction spreads across <b>" +
        App.num(summary.tickers, 0) + "</b> names · expiries " +
        App.esc((summary.expiries || []).join(", ")) + " · " + App.num(summary.preferred, 0) +
        " recommended by the Scanner"
      : "";
    if (!all.length) {
      host.innerHTML = '<p class="empty">No near-term directional spreads in the last run.</p>' +
        '<p class="empty faint">Only the top-ranked names are priced, and a name needs live ' +
        "quotes on its near expiry for a spread to be built. A scan published before this view " +
        "existed carries none — the next scheduled run fills it in.</p>";
      App.$("#spreadfilters").innerHTML = "";
      return;
    }
    renderTable(host, all);
  }

  function renderTable(host, all) {
    // A yearly return on a three-week trade is a four-figure percentage that
    // compares with nothing on this page, so the near view leaves it out.
    var SPREAD_COLUMNS = ALL_COLUMNS.filter(function (c) {
      return !(spreadView === "near" && c.k === "ror");
    });
    renderSpreadFilters(all);

    var rows = all.filter(function (r) {
      if (spreadFilters.preferredOnly && !r.preferred) return false;
      if (spreadFilters.keys.size && !spreadFilters.keys.has(r.plan.key)) return false;
      return true;
    });

    var col = SPREAD_COLUMNS.filter(function (c) { return c.k === SPREAD_SORT.key; })[0];
    var val = col ? (col.v || function (r) { return r[col.k]; }) : null;
    rows.sort(function (a, b) {
      if (!col) {
        if (a.preferred !== b.preferred) return a.preferred ? -1 : 1;
        return String(a.ticker).localeCompare(String(b.ticker));
      }
      var x = val(a), y = val(b);
      if (!App.has(x)) x = -Infinity;
      if (!App.has(y)) y = -Infinity;
      if (typeof x === "string" || typeof y === "string") {
        return String(x).localeCompare(String(y)) * SPREAD_SORT.dir;
      }
      return (x - y) * SPREAD_SORT.dir;
    });

    var head = SPREAD_COLUMNS.map(function (c) {
      var sort = c.k === SPREAD_SORT.key ? (SPREAD_SORT.dir === 1 ? "ascending" : "descending") : "none";
      return App.sortableTh(c.k, c.h, null, sort);
    }).join("");

    var body = rows.length ? rows.map(function (r) {
      return '<tr class="srow' + (r.preferred ? " picked" : "") + '" data-id="' + App.esc(r.id) +
        '" tabindex="0" aria-expanded="false">' +
        SPREAD_COLUMNS.map(function (c) {
          return '<td class="' + (c.cls || "") + (c.r ? " r" : "") + '">' + c.f(r) + "</td>";
        }).join("") + "</tr>";
    }).join("") : '<tr><td colspan="' + SPREAD_COLUMNS.length +
      '" class="empty">Nothing matches that filter.</td></tr>';

    // The detail panel lives *outside* the horizontally scrolling wrapper. Put
    // it in a row inside the table and eleven columns of table width scroll it
    // out of view, taking its prose with it.
    host.innerHTML = '<div class="tablewrap"><table class="scan spreads"><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>" +
      '<div id="spreaddetail" hidden></div>';

    // Scoped to this table's own header row: the detail panel below holds a
    // full legs table, and an unscoped query would wire its headers up to sort
    // the spreads table as well.
    App.wireSort(host.querySelectorAll("table.spreads > thead th"), function (k) {
      if (SPREAD_SORT.key === k) SPREAD_SORT.dir = -SPREAD_SORT.dir;
      else { SPREAD_SORT.key = k; SPREAD_SORT.dir = (k === "ticker" || k === "name") ? 1 : -1; }
      renderSpreads(true);
      var again = App.$('table.spreads > thead th[data-key="' + k + '"]', host);
      if (again) again.focus();
    });

    var panel = App.$("#spreaddetail", host);
    var byId = {};
    rows.forEach(function (r) { byId[r.id] = r; });

    function toggle(tr) {
      var open = tr.getAttribute("aria-expanded") === "true";
      var openRows = host.querySelectorAll('tr.srow[aria-expanded="true"]');
      for (var n = 0; n < openRows.length; n++) {
        openRows[n].setAttribute("aria-expanded", "false");
      }
      if (open) {
        panel.hidden = true;
        panel.innerHTML = "";
        return;
      }
      tr.setAttribute("aria-expanded", "true");
      // Built on demand: rendering every row's legs table up front is a lot of
      // DOM for panels nobody opens.
      panel.innerHTML = spreadDetail(byId[tr.dataset.id]);
      panel.hidden = false;
    }
    var srows = host.querySelectorAll("tr.srow");
    for (var j = 0; j < srows.length; j++) {
      srows[j].addEventListener("click", function () { toggle(this); });
      srows[j].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(this); }
      });
    }
  }
})(window.SpreadApp = window.SpreadApp || {});
