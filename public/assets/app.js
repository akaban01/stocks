/* Halal Spread Scanner — frontend.
 *
 * The backend writes JSON and nothing else; everything you see is rendered
 * here from data/scan.json, data/charts.json, data/backtest.json and
 * data/calibration.json.
 *
 * The trading copy — action labels, premium-state rules, the strategy playbook,
 * the glossary — is NOT hardcoded below. It ships inside scan.json under
 * `reference`, so an explanation can never drift from the field it explains.
 */
(function () {
  "use strict";

  var DATA_DIR = "data/";
  var store = { scan: null, charts: null, backtest: null, calibration: null };
  var filters = { actions: new Set(), query: "" };

  // ------------------------------------------------------------- utilities

  function $(sel, root) { return (root || document).querySelector(sel); }
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
  function pct(v, digits) { return has(v) && !isNaN(v) ? num(v, digits === undefined ? 1 : digits) + "%" : "—"; }

  function tone(action) {
    return ({ BUY_PREMIUM: "buy", SELL_PREMIUM: "sell", NEUTRAL_INCOME: "neutral",
              STAND_ASIDE: "wait", NO_DATA: "none" })[action] || "none";
  }
  function ref(path, fallback) {
    var node = store.scan && store.scan.reference;
    var parts = path.split(".");
    for (var i = 0; i < parts.length && node; i++) node = node[parts[i]];
    return node === undefined || node === null ? fallback : node;
  }

  function localTime(iso, fallback) {
    var d = new Date(iso);
    if (isNaN(d.getTime())) return fallback || iso || "—";
    var tz = "";
    try { tz = Intl.DateTimeFormat().resolvedOptions().timeZone || ""; } catch (e) { tz = ""; }
    var txt = d.toLocaleString(undefined, {
      year: "numeric", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"
    });
    return tz ? txt + " (" + tz + ")" : txt;
  }

  function load(name) {
    if (store[name]) return Promise.resolve(store[name]);
    return fetch(DATA_DIR + name + ".json", { cache: "no-cache" })
      .then(function (r) {
        if (r.status === 404) {
          var missing = new Error("data/" + name + ".json has not been generated yet.");
          missing.missing = true;
          throw missing;
        }
        if (!r.ok) throw new Error(r.status + " " + r.statusText);
        return r.json();
      })
      .then(function (json) { store[name] = json; return json; });
  }

  function loadError(e, name, cmd) {
    if (e && e.missing) {
      return '<p class="empty">Not generated yet — the next scan writes <code>data/' +
        name + '.json</code>.<br><span class="faint">Locally: <code>' + esc(cmd) + "</code></span></p>";
    }
    return '<p class="empty">Could not load ' + name + ".json — " + esc(e.message) + "</p>";
  }

  // ------------------------------------------------------------ tab wiring

  function showTab(name) {
    var buttons = document.querySelectorAll(".tabs button");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].dataset.tab === name;
      buttons[i].setAttribute("aria-selected", on ? "true" : "false");
    }
    var panels = document.querySelectorAll(".panel");
    for (var j = 0; j < panels.length; j++) panels[j].hidden = panels[j].dataset.tab !== name;
    try { localStorage.setItem("tab", name); } catch (e) { /* private mode */ }
    if (name === "charts") renderCharts();
    if (name === "validation") renderValidation();
    if (name === "reference") renderReference();
  }

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
      return '<div class="ivcell"><span class="k">' + esc(k) + '</span>' +
        '<span class="v ' + (cls || "") + '">' + v + "</span>" +
        (sub ? '<span class="s">' + sub + "</span>" : "") +
        (meter !== undefined && meter !== null
          ? '<span class="meter"><i class="' + (cls || "") + '" style="width:' +
            Math.max(0, Math.min(100, meter)) + '%"></i></span>'
          : "") + "</div>";
    }
    var out = [];
    out.push(cell("IV rank", has(o.iv_rank) ? num(o.iv_rank, 0) : "—",
      has(o.iv_percentile) ? num(o.iv_percentile, 0) + "th pctile" : "no history",
      state, o.iv_rank));
    out.push(cell("Premium", num(o.premium_score, 0) + "/100",
      esc((ref("premium_states." + state) || {}).label || state), state, o.premium_score));
    out.push(cell("Implied move", pct(o.implied_move_pct),
      "realized " + pct(o.hist_move_pct)));
    out.push(cell("IV vs HV", has(o.iv_hv_ratio) ? num(o.iv_hv_ratio, 2) + "×" : "—",
      has(o.vrp) ? (o.vrp > 0 ? "+" : "") + num(o.vrp, 1) + " vol pts" : ""));
    out.push(cell("Term", esc(o.term_structure || "—"),
      has(o.term_slope) ? (o.term_slope > 0 ? "+" : "") + pct(o.term_slope * 100) + " front→back" : ""));
    out.push(cell("Skew", has(o.skew) ? (o.skew > 0 ? "+" : "") + num(o.skew, 1) : "—",
      esc(String(o.skew_label || "").replace("_", " "))));
    out.push(cell("Liquidity", esc(o.liquidity || "—"),
      has(o.atm_spread_pct) ? "ATM spread " + pct(o.atm_spread_pct, 0) : ""));
    return '<div class="ivstrip">' + out.join("") + "</div>";
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
    var rows = plan.legs.map(function (l) {
      var side = String(l.action || "").toLowerCase();
      var what = l.right === "share"
        ? num(l.qty, 0) + " shares"
        : num(l.qty, 0) + "× " + num(l.strike, 2) + " " + esc(l.right);
      return "<tr>" +
        '<td class="side ' + esc(side) + '">' + esc(side) + "</td>" +
        "<td>" + what + "</td>" +
        '<td class="r">' + (has(l.mid) ? money(l.mid) : "—") + "</td>" +
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
    var risk = [
      ["Max profit", has(plan.max_profit) ? money(plan.max_profit, 0) : (plan.net !== null && plan.max_profit === null ? "uncapped" : "—")],
      ["Max loss", has(plan.max_loss) ? money(plan.max_loss, 0) : (plan.risk === "undefined" ? "undefined" : "—")],
      ["Breakeven", plan.breakevens && plan.breakevens.length
        ? plan.breakevens.map(function (b) { return num(b, 2); }).join(" / ") : "—"],
      ["Prob. of profit", has(plan.pop) ? pct(plan.pop * 100, 0) : "—"],
      ["Credit / width", has(plan.credit_to_width) ? pct(plan.credit_to_width * 100, 0) : "—"],
      ["Size", sizeCell(plan.sizing)]
    ].map(function (kv) {
      return "<div><span class=\"k\">" + esc(kv[0]) + "</span><span class=\"v\">" + kv[1] + "</span></div>";
    }).join("");

    return '<div class="order">' +
      '<div class="order-head"><span class="t">The order</span>' +
      '<span class="exp">' + esc(plan.expiry || "") +
      (has(plan.dte) ? " · " + num(plan.dte, 0) + " DTE" : "") + "</span>" +
      '<span class="net ' + netCls + '">' + netTxt + " per spread</span></div>" +
      '<table class="legs"><thead><tr><th>Side</th><th>Contract</th>' +
      '<th class="r">Mid</th><th class="r">Bid / Ask</th><th class="r">IV</th><th class="r">OI</th>' +
      "</tr></thead><tbody>" + rows + "</tbody></table>" +
      '<div class="riskrow">' + risk + "</div></div>";
  }

  function noteList(title, items, cls) {
    if (!items || !items.length) return "";
    return '<div class="notes ' + (cls || "") + '"><div class="t">' + esc(title) + "</div><ul>" +
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
        (has(a.pop) ? " · POP " + pct(a.pop * 100, 0) : "") + "</div>" +
        '<div class="d">' + esc(a.playbook || a.thesis || "") + "</div></div>";
    }).join("");
    return '<details class="alts"><summary>Other ways to express this (' + alts.length + ")</summary>" +
      body + "</details>";
  }

  function card(sig) {
    var rec = sig.recommendation || {};
    var plan = rec.plan || {};
    var t = tone(rec.action);
    var actionMeta = ref("actions." + rec.action) || {};
    var conf = has(rec.confidence) ? Math.round(rec.confidence * 100) : null;

    var head = '<div class="card-head">' +
      '<span class="rank">#' + num(sig.rank, 0) + "</span>" +
      '<span class="tkr">' + esc(sig.ticker) + "</span>" +
      '<span class="px">' + num(sig.price, 2) + "</span>" +
      '<span class="badge ' + t + '">' + esc(actionMeta.label || rec.action || "—") + "</span>" +
      '<span class="strat">' + esc(plan.name || "") + "</span>" +
      (conf === null ? "" :
        '<span class="conf">confidence ' + conf + '%<span class="conf-bar">' +
        '<i style="width:' + conf + '%"></i></span></span>') +
      "</div>";

    var body = [];
    if (plan.thesis) body.push('<p style="margin:0;font-size:.93rem;color:#c9d1d9">' + esc(plan.thesis) + "</p>");
    body.push(ivStrip(sig));
    body.push(legsTable(plan));
    if (plan.playbook) {
      body.push('<div class="notes"><div class="t">What this trade is</div>' +
        '<p style="margin:0;font-size:.87rem;color:#c9d1d9">' + esc(plan.playbook) + "</p></div>");
    }
    body.push(noteList("Why", (rec.why || []).map(esc)));
    body.push(manageBlock(plan));
    body.push(noteList("Watch out", (rec.warnings || []).map(esc), "warns"));
    body.push(noteList("Do not", (rec.avoid || []).map(function (a) {
      return "<b>" + esc(a.name) + "</b> — " + esc(a.reason);
    }), "avoid"));
    body.push(altBlock(rec.alternatives));
    if (plan.compliance && plan.compliance.note) {
      body.push('<div class="compliance"><b>Shariah note (' +
        esc(String(plan.compliance.tier || "").replace(/_/g, " ")) + ")</b> — " +
        esc(plan.compliance.note) + "</div>");
    }

    return '<article class="card ' + t + '" data-ticker="' + esc(sig.ticker) +
      '" data-action="' + esc(rec.action || "NO_DATA") + '">' +
      head + '<div class="card-body">' + body.filter(Boolean).join("") + "</div></article>";
  }

  function renderFilters() {
    var counts = store.scan.counts || {};
    var order = ["BUY_PREMIUM", "SELL_PREMIUM", "NEUTRAL_INCOME", "STAND_ASIDE", "NO_DATA"];
    var html = order.filter(function (a) { return counts[a]; }).map(function (a) {
      var meta = ref("actions." + a) || {};
      var on = filters.actions.size === 0 || filters.actions.has(a);
      return '<button class="chip ' + tone(a) + '" data-action="' + a + '" aria-pressed="' +
        (on ? "true" : "false") + '">' + esc(meta.label || a) +
        '<span class="n">' + counts[a] + "</span></button>";
    }).join("");
    html += '<input class="search" type="search" placeholder="Filter ticker…" ' +
      'value="' + esc(filters.query) + '" aria-label="Filter by ticker">';
    $("#filters").innerHTML = html;

    var chips = document.querySelectorAll("#filters .chip");
    for (var i = 0; i < chips.length; i++) {
      chips[i].addEventListener("click", function () {
        var a = this.dataset.action;
        if (filters.actions.has(a)) filters.actions.delete(a);
        else filters.actions.add(a);
        if (filters.actions.size === order.length) filters.actions.clear();
        renderFilters();
        renderCards();
      });
    }
    $("#filters .search").addEventListener("input", function () {
      filters.query = this.value.trim().toUpperCase();
      renderCards();
    });
  }

  function renderCards() {
    var sigs = (store.scan.signals || []).filter(function (s) {
      var action = (s.recommendation || {}).action || "NO_DATA";
      if (filters.actions.size && !filters.actions.has(action)) return false;
      if (filters.query && String(s.ticker).indexOf(filters.query) !== 0) return false;
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
    $("#cards").innerHTML = sigs.length
      ? sigs.map(card).join("")
      : '<p class="empty">Nothing matches that filter.</p>';
  }

  function renderPlaybook() {
    var d = store.scan;
    $("#updated").textContent = localTime(d.generated_at, d.generated_at_utc);
    $("#updated").title = d.generated_at || "";
    $("#horizon").textContent = d.horizon_days + " trading days";
    $("#scanned").textContent = ((d.universe || {}).scanned || (d.signals || []).length) + " screened tickers";

    var w = d.weights || {};
    $("#weights").textContent = w.values && w.values.compression !== undefined
      ? "compression " + Math.round(w.values.compression * 100) + "% · vol-room " +
        Math.round(w.values.vol_room * 100) + "% · squeeze " + Math.round(w.values.squeeze * 100) +
        "% (" + (w.source || "default") + (w.as_of ? " " + w.as_of : "") + ")"
      : "";

    var states = ref("premium_states", {});
    $("#rulebar").innerHTML = ["cheap", "fair", "rich"].map(function (k) {
      var s = states[k] || {};
      return '<div class="rule ' + k + '"><div class="k">' + esc(s.rule || k) + "</div>" +
        '<div class="v">' + esc(s.detail || "") + "</div></div>";
    }).join("");

    var counts = d.counts || {};
    var actionable = (counts.BUY_PREMIUM || 0) + (counts.SELL_PREMIUM || 0) + (counts.NEUTRAL_INCOME || 0);
    $("#summary").innerHTML = actionable
      ? "<b>" + actionable + "</b> of " + (d.signals || []).length +
        " screened names have a trade worth placing today. Each card below is the whole instruction: " +
        "the exact legs, what it costs, what it can lose, and when to be out."
      : "No name is mispriced enough today to pay for a position. That is a result, not a gap in the data — " +
        "the cards below show what was read and why each was passed over.";

    if (!(d.signals || []).length) {
      $("#cards").innerHTML = '<p class="empty">No signals in the last run — no tickers returned usable data.</p>';
      $("#filters").innerHTML = "";
      return;
    }
    renderFilters();
    renderCards();

    var dis = d.disclaimer || {};
    $("#disclaimer").innerHTML = ["general", "compliance", "method"]
      .filter(function (k) { return dis[k]; })
      .map(function (k) { return "<p>" + esc(dis[k]) + "</p>"; }).join("");
  }

  // -------------------------------------------------------------- scanner

  var SORT = { key: "rank", dir: 1 };

  var COLUMNS = [
    { k: "rank", h: "#", f: function (s) { return num(s.rank, 0); }, r: true },
    { k: "ticker", h: "Ticker", f: function (s) { return esc(s.ticker); }, cls: "t" },
    { k: "price", h: "Price", f: function (s) { return num(s.price, 2); }, r: true },
    { k: "score", h: "Score", r: true, f: function (s) {
        var hue = 8 + (Number(s.score) / 100) * 132;
        return '<span class="pill" style="background:hsl(' + hue.toFixed(0) + ' 70% 40%)">' +
          num(s.score, 0) + "</span>";
      } },
    { k: "action", h: "Do", f: function (s) {
        var a = (s.recommendation || {}).action || "NO_DATA";
        var meta = ref("actions." + a) || {};
        return '<span class="tag ' + tone(a) + '">' + esc(meta.verb || "—") + "</span>";
      } },
    { k: "strategy", h: "Strategy", f: function (s) {
        return esc(((s.recommendation || {}).plan || {}).name || "—");
      } },
    { k: "iv_rank", h: "IV rank", r: true, v: function (s) { return (s.options || {}).iv_rank; },
      f: function (s) { return has((s.options || {}).iv_rank) ? num(s.options.iv_rank, 0) : "—"; } },
    { k: "premium_score", h: "Premium", r: true, v: function (s) { return (s.options || {}).premium_score; },
      f: function (s) {
        var o = s.options; if (!o) return "—";
        return '<span class="tag ' + (o.premium_state === "cheap" ? "buy" : o.premium_state === "rich" ? "sell" : "neutral") +
          '">' + num(o.premium_score, 0) + "</span>";
      } },
    { k: "implied_move_pct", h: "Implied", r: true, v: function (s) { return (s.options || {}).implied_move_pct; },
      f: function (s) { return has((s.options || {}).implied_move_pct) ? pct(s.options.implied_move_pct) : "—"; } },
    { k: "em_pct", h: "Realized", r: true, f: function (s) { return pct(s.em_pct); } },
    { k: "squeeze", h: "Squeeze", v: function (s) { return s.squeeze_fired ? 999 : (s.squeeze_on ? s.squeeze_days : -1); },
      f: function (s) {
        if (s.squeeze_fired) {
          return '<span class="fired">fired ' + ({ up: "▲", down: "▼" }[s.fired_dir] || "") + "</span>";
        }
        return s.squeeze_on ? "locked " + num(s.squeeze_days, 0) + "d" : "—";
      } },
    { k: "down_1sigma", h: "Down 1σ", r: true, f: function (s) { return num(s.down_1sigma, 2); } },
    { k: "up_1sigma", h: "Up 1σ", r: true, f: function (s) { return num(s.up_1sigma, 2); } },
    { k: "lean", h: "Lean", f: function (s) { return esc(s.lean || "—"); } },
    { k: "hv_annual", h: "HV%", r: true, f: function (s) { return num(s.hv_annual, 0); } },
    { k: "earnings_in_days", h: "Earnings", r: true, f: function (s) {
        if (!has(s.earnings_in_days) || s.earnings_in_days < 0) return "—";
        var win = Math.round((s.horizon_days || 10) * 1.4);
        var txt = num(s.earnings_in_days, 0) + "d";
        return s.earnings_in_days <= win ? '<span class="warncell">' + txt + "</span>" : txt;
      } },
    { k: "debt_ratio", h: "Debt%", r: true, f: function (s) { return has(s.debt_ratio) ? pct(s.debt_ratio * 100, 0) : "—"; } },
    { k: "cash_ratio", h: "Cash%", r: true, f: function (s) { return has(s.cash_ratio) ? pct(s.cash_ratio * 100, 0) : "—"; } }
  ];

  function renderScanner() {
    var sigs = (store.scan.signals || []).slice();
    if (!sigs.length) {
      $("#scantable").innerHTML = '<p class="empty">No signals in the last run.</p>';
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
      return '<th data-key="' + c.k + '" aria-sort="' + sort + '">' + esc(c.h) + "</th>";
    }).join("");
    var body = sigs.map(function (s) {
      return "<tr>" + COLUMNS.map(function (c) {
        return '<td class="' + (c.cls || "") + (c.r ? " r" : "") + '">' + c.f(s) + "</td>";
      }).join("") + "</tr>";
    }).join("");

    $("#scantable").innerHTML = '<div class="tablewrap"><table class="scan"><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>";

    var ths = document.querySelectorAll("#scantable th");
    for (var i = 0; i < ths.length; i++) {
      ths[i].addEventListener("click", function () {
        var k = this.dataset.key;
        if (SORT.key === k) SORT.dir = -SORT.dir;
        else { SORT.key = k; SORT.dir = (k === "rank" || k === "ticker") ? 1 : -1; }
        renderScanner();
      });
    }
  }

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
    var color = up ? "#5fd07a" : "#f0816f";
    var area = "M " + X(0).toFixed(1) + "," + baseY.toFixed(1) + " L " +
      pts.split(" ").join(" L ") + " L " + X(n - 1).toFixed(1) + "," + baseY.toFixed(1) + " Z";

    var grid = ['<text x="' + (X(0) + 2).toFixed(1) + '" y="' + (h - 5) + '" class="yr">' +
      dates[0].slice(0, 4) + "</text>"];
    for (var k = 1; k < n; k++) {
      if (dates[k].slice(0, 4) !== dates[k - 1].slice(0, 4)) {
        var x = X(k).toFixed(1);
        grid.push('<line x1="' + x + '" y1="' + padT + '" x2="' + x + '" y2="' + baseY.toFixed(1) + '" class="gl"/>');
        grid.push('<text x="' + (X(k) + 2).toFixed(1) + '" y="' + (h - 5) + '" class="yr">' +
          dates[k].slice(0, 4) + "</text>");
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
    if (!has(v)) return '<span class="chg neut">—</span>';
    return '<span class="chg ' + (v >= 0 ? "up" : "down") + '">' + (v >= 0 ? "+" : "") +
      num(v, 1) + "%" + (suffix ? " " + esc(suffix) : "") + "</span>";
  }

  function renderCharts() {
    var host = $("#chartgrid");
    if (host.dataset.done) return;
    load("charts").then(function (d) {
      host.dataset.done = "1";
      $("#chartmeta").textContent = d.count + " tickers · " +
        ((d.window || {}).start || "?") + " → " + ((d.window || {}).end || "?") +
        (d.period ? " · " + d.period + " window" : "");
      host.innerHTML = (d.series || []).length
        ? d.series.map(function (s) {
            return '<div class="chart-card"><div class="chart-head">' +
              '<span class="tkr">' + esc(s.ticker) + "</span>" +
              '<span class="px">' + num(s.last, 2) + "</span>" +
              chg(s.change_1y_pct, "1y") + "</div>" +
              sparkline(s.dates, s.closes) +
              '<div class="chart-foot">range <b>' + num(s.low, 2) + " – " + num(s.high, 2) +
              "</b> · window " + (has(s.change_window_pct)
                ? (s.change_window_pct >= 0 ? "+" : "") + num(s.change_window_pct, 1) + "%" : "—") +
              "</div></div>";
          }).join("")
        : '<p class="empty">No price history available.</p>';
    }).catch(function (e) {
      host.innerHTML = loadError(e, "charts", "python run.py");
      $("#chartmeta").textContent = "";
    });
  }

  // ----------------------------------------------------------- validation

  function statsTable(rows, labelHead) {
    return '<table class="stats"><thead><tr><th>' + esc(labelHead) + "</th>" +
      '<th class="r">bars</th><th class="r">avg |move|</th><th class="r">expand</th>' +
      '<th class="r">broke band</th></tr></thead><tbody>' +
      rows.map(function (b) {
        return "<tr><td>" + esc(b.label) + "</td>" +
          '<td class="r">' + num(b.bars, 0) + "</td>" +
          '<td class="r">' + pct(b.avg_abs_move_pct) + "</td>" +
          '<td class="r">' + (has(b.expansion) ? num(b.expansion, 2) + "×" : "—") + "</td>" +
          '<td class="r">' + pct(b.broke_band_pct, 0) + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function renderValidation() {
    var host = $("#validation-body");
    if (host.dataset.done) return;
    host.dataset.done = "1";

    load("backtest").then(function (d) {
      if (!d.ok) { $("#backtest").innerHTML = '<p class="empty">' + esc(d.note || "No backtest yet.") + "</p>"; return; }
      var b = d.buckets, s = d.squeeze;
      $("#backtest").innerHTML =
        '<p class="dim" style="font-size:.85rem">' + esc(d.universe) + " tickers · " + esc(d.history_years) +
        "y history · horizon " + esc(d.horizon_days) + " trading days · " + num(d.bars, 0) + " signal-bars</p>" +
        '<div class="panelcard"><p>' + esc(d.explainer) + "</p></div>" +
        "<h3 style=\"margin-top:18px\">By Setup Score</h3>" +
        statsTable([b.high, b.mid, b.low], "Score bucket") +
        '<div class="verdict ' + (d.verdict.holds ? "good" : "bad") + '">' + esc(d.verdict.text) + "</div>" +
        "<h3>Squeeze on vs off</h3>" + statsTable([s.on, s.off], "State") +
        "<h3>Expected-move calibration</h3>" +
        "<p>Realized moves landed inside the ±1σ band <b>" + pct(d.coverage_pct, 0) +
        "</b> of the time against a theoretical 68%. " +
        (d.coverage_ok ? "The bands are well calibrated." : "The bands look mis-calibrated — consider tuning <code>vol_lookback</code>.") +
        "</p><p class=\"faint\" style=\"font-size:.82rem\">" + esc(d.caveat) + "</p>";
    }).catch(function (e) {
      $("#backtest").innerHTML = loadError(e, "backtest", "python backtest.py --years 5");
    });

    load("calibration").then(function (d) {
      if (!d.ok) { $("#calibration").innerHTML = '<p class="empty">' + esc(d.note || "Not calibrated yet.") + "</p>"; return; }
      var sep = d.separation;
      $("#calibration").innerHTML =
        '<div class="panelcard"><p>' + esc(d.method) + "</p></div>" +
        '<table class="stats" style="margin-top:14px"><thead><tr><th>Weights (from the train split)</th>' +
        '<th class="r">score ≥ 60</th><th class="r">score &lt; 30</th><th class="r">separation</th>' +
        "</tr></thead><tbody>" +
        ["heuristic", "calibrated"].map(function (k) {
          var r = sep[k];
          return "<tr><td>" + esc(k) + " (" +
            Object.keys(r.weights).map(function (w) { return Math.round(r.weights[w] * 100); }).join("/") +
            ')</td><td class="r">' + pct(r.high_break_pct, 0) + '</td><td class="r">' +
            pct(r.low_break_pct, 0) + '</td><td class="r">' +
            (r.separation_pts >= 0 ? "+" : "") + num(r.separation_pts, 0) + " pts</td></tr>";
        }).join("") + "</tbody></table>" +
        '<div class="verdict ' + (d.verdict.holds ? "good" : "bad") + '">' + esc(d.verdict.text) + "</div>";
    }).catch(function (e) {
      $("#calibration").innerHTML = loadError(e, "calibration", "python calibrate.py --years 5");
    });
  }

  // ------------------------------------------------------------ reference

  function renderReference() {
    var host = $("#glossary");
    if (host.dataset.done) return;
    host.dataset.done = "1";
    var g = ref("glossary", {});
    var titles = {
      score: "Setup Score", iv_rank: "IV Rank", iv_percentile: "IV Percentile",
      premium_score: "Premium score", iv_hv_ratio: "IV / HV", vrp: "Volatility risk premium",
      implied_move_pct: "Implied move", hist_move_pct: "Realized (historical) move",
      term_structure: "Term structure", skew: "Skew", liquidity: "Liquidity",
      pop: "Probability of profit", credit_to_width: "Credit to width",
      em_pct: "Expected move (±1σ)", squeeze: "TTM squeeze", lean: "Lean",
      earnings: "Earnings", debt_cash_ratio: "Debt % / Cash %"
    };
    host.innerHTML = "<dl class=\"glossary\">" + Object.keys(g).map(function (k) {
      return "<dt>" + esc(titles[k] || k) + "</dt><dd>" + esc(g[k]) + "</dd>";
    }).join("") + "</dl>";

    var play = ref("playbook", {});
    $("#playbook").innerHTML = "<dl class=\"glossary\">" + Object.keys(play).map(function (k) {
      return "<dt>" + esc(k.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); })) +
        "</dt><dd>" + esc(play[k]) + "</dd>";
    }).join("") + "</dl>";
  }

  // ------------------------------------------------------------------ boot

  function boot() {
    var buttons = document.querySelectorAll(".tabs button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () { showTab(this.dataset.tab); });
    }

    load("scan").then(function (d) {
      if (!d || !d.schema_version) throw new Error("scan.json is missing or malformed");
      if (d.schema_version.split(".")[0] !== "2") {
        $("#schema-warning").hidden = false;
        $("#schema-warning").textContent =
          "This page expects scan schema 2.x but the data says " + d.schema_version +
          ". Some fields may not render.";
      }
      $("#loading").hidden = true;
      $("#app").hidden = false;
      renderPlaybook();
      renderScanner();
      var saved = null;
      try { saved = localStorage.getItem("tab"); } catch (e) { saved = null; }
      showTab(saved || "playbook");
    }).catch(function (e) {
      $("#loading").innerHTML =
        '<p class="empty">Could not load <code>data/scan.json</code> — ' + esc(e.message) + ".</p>" +
        '<p class="empty faint">The scan runs each weekday after the US close and writes it. ' +
        "Locally, run <code>python run.py</code> and serve this folder over HTTP " +
        "(<code>python -m http.server --directory public</code>) — <code>file://</code> blocks fetch.</p>";
    });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
