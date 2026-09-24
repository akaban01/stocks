/* Spread Scanner — frontend: the What to do tab (strategy cards, filters, summary).
 * One of the files in public/assets/app/ (see core.js for how they fit together). */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.renderPlaybook = renderPlaybook;

  // ------------------------------------------------- playbook (the main view)

  function ivStrip(sig) {
    var o = sig.options;
    if (!o) {
      return '<div class="ivstrip"><div class="ivcell"><span class="k">Volatility read</span>' +
        '<span class="s">No option chain was priced for this name in this run, so there is no ' +
        'cheap-or-rich call to make.</span></div></div>';
    }
    var state = o.premium_state || "fair";
    function cell(k, v, sub, cls, meter) {
      return '<div class="ivcell"><span class="k">' + App.esc(k) + '</span>' +
        '<span class="v ' + (cls || "") + '">' + v + "</span>" +
        (sub ? '<span class="s">' + sub + "</span>" : "") +
        (meter !== undefined && meter !== null
          ? '<span class="meter"><i class="' + (cls || "") + '" style="width:' +
            Math.max(0, Math.min(100, meter)) + '%"></i></span>'
          : "") + "</div>";
    }
    var out = [];
    out.push(cell("IV rank", App.has(o.iv_rank) ? App.num(o.iv_rank, 0) : "—",
      (App.has(o.iv_percentile) ? App.num(o.iv_percentile, 0) + "th pctile" : "no history") +
        (o.iv_rank_basis === "implied" ? " · vs past IV" : App.has(o.iv_rank) ? " · vs realized vol" : ""),
      state, o.iv_rank));
    out.push(cell("Premium", App.num(o.premium_score, 0) + "/100",
      App.esc((App.ref("premium_states." + state) || {}).label || state), state, o.premium_score));
    out.push(cell("Implied move", App.pct(o.implied_move_pct),
      "realized " + App.pct(o.hist_move_pct)));
    out.push(cell("IV vs HV", App.has(o.iv_hv_ratio) ? App.num(o.iv_hv_ratio, 2) + "×" : "—",
      App.has(o.vrp) ? (o.vrp > 0 ? "+" : "") + App.num(o.vrp, 1) + " vol pts" : ""));
    out.push(cell("Term", App.esc(o.term_structure || "—"),
      App.has(o.term_slope) ? (o.term_slope > 0 ? "+" : "") + App.pct(o.term_slope * 100) + " front→back" : ""));
    out.push(cell("Skew", App.has(o.skew) ? (o.skew > 0 ? "+" : "") + App.num(o.skew, 1) : "—",
      App.esc(String(o.skew_label || "").replace("_", " "))));
    out.push(cell("Liquidity", App.esc(o.liquidity || "—"),
      App.has(o.atm_spread_pct) ? "ATM spread " + App.pct(o.atm_spread_pct, 0) : ""));
    return '<div class="ivstrip">' + out.join("") + "</div>";
  }







  // The compliance verdict, where a reader will actually see it. `filter` mode
  // never publishes a failing name, so this is silent unless `annotate` is on.
  // Three states, not two. `false` is checked and failed; `null` is the screen
  // ran and produced no verdict for this name. "Not checked" and "checked and
  // passed" are different claims and only one of them is safe to imply.
  function screenBadge(sig) {
    var sc = sig.screen;
    if (!sc || sc.compliant === true) return "";
    var label = sc.compliant === false ? "Fails screen" : "Not screened";
    return '<span class="badge flag" title="' +
      App.esc((sc.reasons || []).join("; ")) + '">' + label + "</span>";
  }

  function screenNote(sig) {
    var sc = sig.screen;
    if (!sc || sc.compliant === true) return "";
    var title = sc.compliant === false
      ? "Did not pass the compliance screen"
      : "The compliance screen returned no verdict for this name";
    return App.noteList(title, (sc.reasons || []).map(App.esc), "warns");
  }

  function card(sig) {
    var rec = sig.recommendation || {};
    var plan = rec.plan || {};
    var t = App.tone(rec.action);
    var actionMeta = App.ref("actions." + rec.action) || {};
    var conf = App.has(rec.confidence) ? Math.round(rec.confidence * 100) : null;

    var head = '<div class="card-head">' +
      '<span class="rank">#' + App.num(sig.rank, 0) + "</span>" +
      '<span class="tkr"><a class="totest" href="#backtest#' + App.esc(sig.ticker) + '" title="' +
        App.esc("backtest a rule on " + sig.ticker) + '">' + App.esc(sig.ticker) + "</a></span>" +
      '<span class="px">' + App.num(sig.price, 2) + "</span>" +
      '<span class="badge ' + t + '">' + App.esc(actionMeta.label || rec.action || "—") + "</span>" +
      screenBadge(sig) +
      '<span class="strat">' + App.esc(plan.name || "") + "</span>" +
      (conf === null ? "" :
        '<span class="conf">confidence ' + conf + '%<span class="conf-bar">' +
        '<i style="width:' + conf + '%"></i></span></span>') +
      "</div>";

    var body = [];
    body.push(screenNote(sig));
    if (plan.thesis) body.push('<p class="prose">' + App.esc(plan.thesis) + "</p>");
    body.push(ivStrip(sig));
    body.push(App.legsTable(plan));
    if (plan.playbook) {
      body.push('<div class="notes"><div class="t">What this trade is</div>' +
        '<p class="prose sm">' + App.esc(plan.playbook) + "</p></div>");
    }
    body.push(App.noteList("Why", (rec.why || []).map(App.esc)));
    body.push(App.manageBlock(plan));
    body.push(App.noteList("Watch out", (rec.warnings || []).map(App.esc), "warns"));
    body.push(App.noteList("Do not", (rec.avoid || []).map(function (a) {
      return "<b>" + App.esc(a.name) + "</b> — " + App.esc(a.reason);
    }), "avoid"));
    body.push(App.altBlock(rec.alternatives));
    body.push(App.riskFormNote(plan));

    return '<article class="card ' + t + '" data-ticker="' + App.esc(sig.ticker) +
      '" data-action="' + App.esc(rec.action || "NO_DATA") + '">' +
      head + '<div class="card-body">' + body.filter(Boolean).join("") + "</div></article>";
  }

  function renderFilters() {
    var counts = App.store.scan.counts || {};
    var order = ["BUY_PREMIUM", "SELL_PREMIUM", "NEUTRAL_INCOME", "STAND_ASIDE", "NO_DATA"];
    var html = order.filter(function (a) { return counts[a]; }).map(function (a) {
      var meta = App.ref("actions." + a) || {};
      var on = App.filters.actions.size === 0 || App.filters.actions.has(a);
      return '<button class="chip ' + App.tone(a) + '" data-action="' + a + '" aria-pressed="' +
        (on ? "true" : "false") + '">' + App.esc(meta.label || a) +
        '<span class="n">' + counts[a] + "</span></button>";
    }).join("");
    html += '<input class="search" type="search" placeholder="Filter ticker…" ' +
      'value="' + App.esc(App.filters.query) + '" aria-label="Filter by ticker">';
    App.$("#filters").innerHTML = html;

    var chips = document.querySelectorAll("#filters .chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].addEventListener("click", function () {
        var a = this.dataset.action;
        if (App.filters.actions.has(a)) App.filters.actions.delete(a);
        else App.filters.actions.add(a);
        if (App.filters.actions.size === order.length) App.filters.actions.clear();
        renderFilters();
        renderCards();
      });
    }
    App.$("#filters .search").addEventListener("input", function () {
      App.filters.query = this.value.trim().toUpperCase();
      renderCards();
    });
  }

  function renderCards() {
    var sigs = (App.store.scan.signals || []).filter(function (s) {
      var action = (s.recommendation || {}).action || "NO_DATA";
      if (App.filters.actions.size && !App.filters.actions.has(action)) return false;
      // Substring, not prefix: typing "VDA" should find NVDA.
      if (App.filters.query && String(s.ticker).indexOf(App.filters.query) === -1) return false;
      return true;
    });
    // Actionable names first, then by how much the inputs agree, then by score.
    var weight = { BUY_PREMIUM: 0, SELL_PREMIUM: 0, NEUTRAL_INCOME: 1, STAND_ASIDE: 2, NO_DATA: 3 };
    sigs.sort(function (a, b) {
      var ra = a.recommendation || {}, rb = b.recommendation || {};
      var wa = weight[ra.action] === undefined ? 3 : weight[ra.action];
      var wb = weight[rb.action] === undefined ? 3 : weight[rb.action];
      if (wa !== wb) return wa - wb;
      var ca = ra.confidence || 0, cb = rb.confidence || 0;
      if (cb !== ca) return cb - ca;
      return (b.score || 0) - (a.score || 0);
    });
    App.$("#cards").innerHTML = sigs.length
      ? sigs.map(card).join("")
      : '<p class="empty">Nothing matches that filter.</p>';
  }

  function renderPlaybook() {
    var d = App.store.scan;
    App.$("#updated").textContent = App.localTime(d.generated_at, d.generated_at_utc);
    App.$("#updated").title = d.generated_at || "";
    App.$("#horizon").textContent = d.horizon_days + " trading days";
    App.$("#scanned").textContent = ((d.universe || {}).scanned || (d.signals || []).length) + " screened tickers";

    // A universe that fell back to the config watchlist still produces a full,
    // valid scan — it is just not scanning what the page implies it is. That
    // difference was previously visible only in the workflow log.
    var uniFallback = (d.universe || {}).fallback;
    App.$("#universe-warning").hidden = !uniFallback;
    if (uniFallback) App.$("#universe-warning").textContent = uniFallback;

    // In `annotate` mode the screen reports and keeps going, so the page can be
    // showing names that failed it. Say it once at the top, and flag them
    // individually on their own cards and rows.
    var screen = d.screen || {};
    var flagged = screen.flagged || [];
    var unknown = screen.unknown || [];
    App.$("#screen-warning").hidden = !(flagged.length || unknown.length);
    if (flagged.length || unknown.length) {
      var parts = [];
      if (flagged.length) {
        parts.push("<b>" + flagged.length + " name" + (flagged.length === 1 ? "" : "s") +
          " on this page did not pass the compliance screen</b> — " +
          flagged.map(App.esc).join(", ") +
          ". The screen is running in <code>annotate</code> mode, which reports a " +
          "failure instead of dropping the name.");
      }
      if (unknown.length) {
        parts.push("<b>" + unknown.length + " name" + (unknown.length === 1 ? "" : "s") +
          " could not be screened this run</b> — " + unknown.map(App.esc).join(", ") +
          ". Treat those as unverified rather than as having passed.");
      }
      parts.push("Each one is marked on its card.");
      App.$("#screen-warning").innerHTML = parts.join(" ");
    }

    var w = d.weights || {};
    App.$("#weights").textContent = w.values && w.values.compression !== undefined
      ? "compression " + Math.round(w.values.compression * 100) + "% · vol-room " +
        Math.round(w.values.vol_room * 100) + "% · squeeze " + Math.round(w.values.squeeze * 100) +
        "% (" + (w.source || "default") + (w.as_of ? " " + w.as_of : "") + ")"
      : "";

    var states = App.ref("premium_states", {});
    App.$("#rulebar").innerHTML = ["cheap", "fair", "rich"].map(function (k) {
      var s = states[k] || {};
      return '<div class="rule ' + k + '"><div class="k">' + App.esc(s.rule || k) + "</div>" +
        '<div class="v">' + App.esc(s.detail || "") + "</div></div>";
    }).join("");

    var counts = d.counts || {};
    var actionable = (counts.BUY_PREMIUM || 0) + (counts.SELL_PREMIUM || 0) + (counts.NEUTRAL_INCOME || 0);

    // "Mispriced" and "affordable" are different questions and the headline used
    // to answer only the first while sounding like it answered the second. On a
    // day when every candidate risks more than the budget, "4 names have a trade
    // worth placing" was contradicted by the very cards it was introducing —
    // each one sized at zero contracts. Both numbers now get said out loud.
    var fits = 0, cheapest = null, budget = null;
    (d.signals || []).forEach(function (s) {
      var sizing = ((s.recommendation || {}).plan || {}).sizing;
      if (!sizing || !App.ACTIONABLE[(s.recommendation || {}).action]) return;
      if (budget === null && App.has(sizing.risk_budget)) budget = sizing.risk_budget;
      if (sizing.contracts > 0) fits++;
      else if (App.has(sizing.risk_per_spread) &&
               (cheapest === null || sizing.risk_per_spread < cheapest)) {
        cheapest = sizing.risk_per_spread;
      }
    });

    var lead = "<b>" + actionable + "</b> of " + (d.signals || []).length +
               " screened names are mispriced today";
    var withBudget = budget === null ? "your risk budget" : "your " + App.money(budget, 0) + " risk budget";
    App.$("#summary").innerHTML = !actionable
      ? "No name is mispriced enough today to pay for a position. That is a result, not a gap in the data — " +
        "the cards below show what was read and why each was passed over."
      : fits
        ? lead + ", and <b>" + fits + "</b> " + (fits === 1 ? "fits" : "fit") + " " + withBudget +
          ". Each card below is the whole instruction: the exact legs, what it costs, what it can " +
          "lose, and when to be out."
        : lead + ", but <b>none</b> fit " + withBudget +
          (cheapest === null ? "" : " — the cheapest single spread risks " + App.money(cheapest, 0)) +
          ". The cards below show each trade and what it would cost.";
    // Whether straddles and strangles were on the menu this run, and the
    // evidence that decided it. Withheld is the default until the record says
    // buying premium on the setup pays.
    if (d.portfolio && d.portfolio.capped && d.portfolio.capped.length) {
      App.$("#summary").innerHTML += ' <span class="dim">The ' + App.money(d.portfolio.cap, 0) +
        " cap on total risk across today's trades cut " + App.esc(d.portfolio.capped.join(", ")) +
        " — these names move together, so their risks add up.</span>";
    }
    // Direction reads the evidence did not back are not traded on (see the
    // "Does it work?" tab); say so once rather than on every card.
    if (d.direction && d.direction.proven) {
      var unproven = Object.keys(d.direction.proven).filter(function (k) { return !d.direction.proven[k]; });
      if (!Object.keys(d.direction.proven).length || unproven.length === Object.keys(d.direction.proven).length) {
        App.$("#summary").innerHTML += ' <span class="dim">Directional trades are withheld: ' +
          App.esc(d.direction.text) + "</span>";
      }
    }
    if (d.long_vol && d.long_vol.supported === false) {
      App.$("#summary").innerHTML += ' <span class="dim">Straddles and strangles are withheld: ' +
        App.esc(d.long_vol.text) + "</span>";
    }

    if (!(d.signals || []).length) {
      App.$("#cards").innerHTML = '<p class="empty">No signals in the last run — no tickers returned usable data.</p>';
      App.$("#filters").innerHTML = "";
      return;
    }
    renderFilters();
    renderCards();

    var dis = d.disclaimer || {};
    App.$("#disclaimer").innerHTML = ["general", "risk", "method"]
      .filter(function (k) { return dis[k]; })
      .map(function (k) { return "<p>" + App.esc(dis[k]) + "</p>"; }).join("");
  }
})(window.SpreadApp = window.SpreadApp || {});
