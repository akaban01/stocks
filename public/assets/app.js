/* Spread Scanner — frontend.
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
  var store = { scan: null, charts: null, weekly: null, backtest: null, calibration: null };
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
  // money() drops the sign, which is right for a price and wrong for a P&L.
  function cash(v, digits) {
    if (!has(v) || isNaN(v)) return "—";
    return (v < 0 ? "−$" : "$") + num(Math.abs(v), digits === undefined ? 0 : digits);
  }
  function pct(v, digits) { return has(v) && !isNaN(v) ? num(v, digits === undefined ? 1 : digits) + "%" : "—"; }

  // The actions that mean "there is a trade here" — as opposed to standing
  // aside or having no chain to read. The headline counts these and the sizing
  // tally filters by them, so they are named once rather than twice.
  var ACTIONABLE = { BUY_PREMIUM: true, SELL_PREMIUM: true, NEUTRAL_INCOME: true };

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

  // Sortable table headers. Three tables render them and all three were
  // mouse-only: no tabindex, no key handler, so the columns simply could not be
  // sorted from a keyboard. The spread *rows* got this right, which is what
  // made the omission easy to miss.
  function sortableTh(key, label, cls, ariaSort) {
    return '<th data-key="' + esc(key) + '" tabindex="0"' +
      (cls ? ' class="' + cls + '"' : "") +
      ' aria-sort="' + (ariaSort || "none") + '">' + esc(label) + "</th>";
  }

  function wireSort(nodes, handler) {
    for (var i = 0; i < nodes.length; i++) {
      if (!nodes[i].dataset.key) continue;         // not a sortable column
      nodes[i].addEventListener("click", function () { handler(this.dataset.key); });
      nodes[i].addEventListener("keydown", function (e) {
        if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
          e.preventDefault();
          handler(this.dataset.key);
        }
      });
    }
  }

  // The palette lives in styles.css and is read from there. Duplicating the
  // hex values in here meant a theme change moved the page and left the charts
  // behind — the one place the two halves could silently disagree.
  var THEME_FALLBACK = { "--up": "#5fd07a", "--down": "#f0816f", "--wait": "#8b949e",
                         "--text": "#e6edf3", "--line": "#30363d", "--text-dim": "#9aa5b1" };
  function theme(name) {
    var v = "";
    try {
      v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    } catch (e) { v = ""; }
    return v || THEME_FALLBACK[name] || "currentColor";
  }

  // "r,g,b" for the places that need an alpha over the same colour — the month
  // heat map cells and the sparkline fill. Same source of truth, one conversion.
  function themeRgb(name) {
    var hex = theme(name).replace("#", "");
    if (hex.length === 3) hex = hex[0] + hex[0] + hex[1] + hex[1] + hex[2] + hex[2];
    if (!/^[0-9a-f]{6}$/i.test(hex)) return "128,128,128";
    return [0, 2, 4].map(function (i) { return parseInt(hex.slice(i, i + 2), 16); }).join(",");
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

  // Keyed on the *promise*, not just the result: two clicks on a tab before
  // its payload lands used to start two downloads of the same file, which on
  // weekly.json is 300KB twice.
  var inflight = {};

  function load(name) {
    if (store[name]) return Promise.resolve(store[name]);
    if (inflight[name]) return inflight[name];
    inflight[name] = fetch(DATA_DIR + name + ".json", { cache: "no-cache" })
      .then(function (r) {
        if (r.status === 404) {
          var missing = new Error("data/" + name + ".json has not been generated yet.");
          missing.missing = true;
          throw missing;
        }
        if (!r.ok) throw new Error(r.status + " " + r.statusText);
        return r.json();
      })
      .then(function (json) { store[name] = json; return json; })
      .finally(function () { delete inflight[name]; });
    return inflight[name];
  }

  function loadError(e, name, cmd) {
    if (e && e.missing) {
      return '<p class="empty">Not generated yet — the next scan writes <code>data/' +
        name + '.json</code>.<br><span class="faint">Locally: <code>' + esc(cmd) + "</code></span></p>";
    }
    return '<p class="empty">Could not load ' + name + ".json — " + esc(e.message) + "</p>";
  }

  // ------------------------------------------------------------ tab wiring

  // The visible state of this page is one tab, plus — on Charts — one sub-view.
  // Both live in the URL fragment, so any view can be linked to and shared:
  //
  //     …/#spreads             the Spreads tab
  //     …/#charts#seasonality  Charts, showing the month tables
  //
  // Two segments rather than a query string because a fragment never leaves the
  // browser, which is the only option on Pages: there is no server to read one.
  var TAB_NAMES = ["playbook", "spreads", "scanner", "charts", "repeat", "validation",
                   "reference"];
  var CHART_VIEWS = ["prices", "seasonality"];
  var curTab = "playbook";
  var curView = "prices";
  // Set while the page is putting itself into a state it was *handed* — during
  // boot, and while applying an incoming fragment — so that those moves do not
  // write the fragment back over the one they are reading.
  var hashLock = false;

  function parseHash() {
    var raw = String(location.hash || "").replace(/^#+/, "");
    if (!raw) return null;
    var parts = raw.split("#");
    var out = {};
    for (var i = 0; i < parts.length; i++) {
      var seg = parts[i];
      try { seg = decodeURIComponent(seg); } catch (e) { /* leave it as typed */ }
      seg = seg.trim().toLowerCase();
      if (!out.tab && TAB_NAMES.indexOf(seg) !== -1) out.tab = seg;
      else if (!out.view && CHART_VIEWS.indexOf(seg) !== -1) out.view = seg;
    }
    // A bare "#seasonality" can only mean one tab, so read it as that tab.
    if (out.view && !out.tab) out.tab = "charts";
    return out.tab ? out : null;
  }

  function writeHash() {
    if (hashLock) return;
    var want = "#" + curTab + (curTab === "charts" ? "#" + curView : "");
    if (location.hash === want) return;
    // replaceState, not pushState: the tab strip moves on arrow keys, and one
    // history entry per keystroke would bury whatever the reader arrived from.
    try {
      history.replaceState(null, "", location.pathname + location.search + want);
    } catch (e) {
      location.hash = want;   // history is refused on file://; this still works
    }
  }

  // Someone edited the address bar, or followed a link into the page they are
  // already on. Either way the fragment is now the instruction.
  function applyHash() {
    var want = parseHash();
    if (want) {
      hashLock = true;
      if (want.view) showChartView(want.view);
      showTab(want.tab);
      hashLock = false;
    }
    // Unconditional, so the address bar never keeps a fragment that is not what
    // is on screen: "#Charts" becomes "#charts#prices", and a fragment naming
    // nothing is replaced by the tab it failed to move away from.
    writeHash();
  }

  function showTab(name) {
    // Both a stale localStorage value and a hand-typed fragment land here, so
    // an unknown name has to mean the first tab rather than six hidden panels.
    if (TAB_NAMES.indexOf(name) === -1) name = "playbook";
    curTab = name;
    var buttons = document.querySelectorAll(".tabs button");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].dataset.tab === name;
      buttons[i].setAttribute("aria-selected", on ? "true" : "false");
      // Roving tabindex: one tab stop for the whole strip, arrows move within
      // it. Six separate tab stops in front of the content is the thing this
      // pattern exists to avoid.
      buttons[i].tabIndex = on ? 0 : -1;
    }
    var panels = document.querySelectorAll(".panel");
    for (var j = 0; j < panels.length; j++) panels[j].hidden = panels[j].dataset.tab !== name;
    try { localStorage.setItem("tab", name); } catch (e) { /* private mode */ }
    if (name === "spreads") renderSpreads();
    if (name === "charts") renderCharts();
    if (name === "repeat") renderRepeat();
    if (name === "validation") renderValidation();
    if (name === "reference") renderReference();
    writeHash();
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
      // "Uncapped" is a property of the payoff, not of missing data: a long
      // straddle has no max profit but still has a max loss (the debit). A plan
      // whose legs could not be priced has neither, and must not claim uncapped.
      ["Max profit", has(plan.max_profit) ? money(plan.max_profit, 0) : (has(plan.max_loss) ? "uncapped" : "—")],
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
      '<span class="exp">' + (mixed ? "two expiries" : esc(plan.expiry || "")) +
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

  function riskFormNote(plan) {
    var rf = plan.risk_form;
    if (!rf || !rf.note) return "";
    return '<div class="riskform"><b>What secures it (' +
      esc(String(rf.tier || "").replace(/_/g, " ")) + ")</b> — " + esc(rf.note) + "</div>";
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
      esc((sc.reasons || []).join("; ")) + '">' + label + "</span>";
  }

  function screenNote(sig) {
    var sc = sig.screen;
    if (!sc || sc.compliant === true) return "";
    var title = sc.compliant === false
      ? "Did not pass the compliance screen"
      : "The compliance screen returned no verdict for this name";
    return noteList(title, (sc.reasons || []).map(esc), "warns");
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
      screenBadge(sig) +
      '<span class="strat">' + esc(plan.name || "") + "</span>" +
      (conf === null ? "" :
        '<span class="conf">confidence ' + conf + '%<span class="conf-bar">' +
        '<i style="width:' + conf + '%"></i></span></span>') +
      "</div>";

    var body = [];
    body.push(screenNote(sig));
    if (plan.thesis) body.push('<p class="prose">' + esc(plan.thesis) + "</p>");
    body.push(ivStrip(sig));
    body.push(legsTable(plan));
    if (plan.playbook) {
      body.push('<div class="notes"><div class="t">What this trade is</div>' +
        '<p class="prose sm">' + esc(plan.playbook) + "</p></div>");
    }
    body.push(noteList("Why", (rec.why || []).map(esc)));
    body.push(manageBlock(plan));
    body.push(noteList("Watch out", (rec.warnings || []).map(esc), "warns"));
    body.push(noteList("Do not", (rec.avoid || []).map(function (a) {
      return "<b>" + esc(a.name) + "</b> — " + esc(a.reason);
    }), "avoid"));
    body.push(altBlock(rec.alternatives));
    body.push(riskFormNote(plan));

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
      // Substring, not prefix: typing "VDA" should find NVDA.
      if (filters.query && String(s.ticker).indexOf(filters.query) === -1) return false;
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

    // A universe that fell back to the config watchlist still produces a full,
    // valid scan — it is just not scanning what the page implies it is. That
    // difference was previously visible only in the workflow log.
    var uniFallback = (d.universe || {}).fallback;
    $("#universe-warning").hidden = !uniFallback;
    if (uniFallback) $("#universe-warning").textContent = uniFallback;

    // In `annotate` mode the screen reports and keeps going, so the page can be
    // showing names that failed it. Say it once at the top, and flag them
    // individually on their own cards and rows.
    var screen = d.screen || {};
    var flagged = screen.flagged || [];
    var unknown = screen.unknown || [];
    $("#screen-warning").hidden = !(flagged.length || unknown.length);
    if (flagged.length || unknown.length) {
      var parts = [];
      if (flagged.length) {
        parts.push("<b>" + flagged.length + " name" + (flagged.length === 1 ? "" : "s") +
          " on this page did not pass the compliance screen</b> — " +
          flagged.map(esc).join(", ") +
          ". The screen is running in <code>annotate</code> mode, which reports a " +
          "failure instead of dropping the name.");
      }
      if (unknown.length) {
        parts.push("<b>" + unknown.length + " name" + (unknown.length === 1 ? "" : "s") +
          " could not be screened this run</b> — " + unknown.map(esc).join(", ") +
          ". Treat those as unverified rather than as having passed.");
      }
      parts.push("Each one is marked on its card.");
      $("#screen-warning").innerHTML = parts.join(" ");
    }

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

    // "Mispriced" and "affordable" are different questions and the headline used
    // to answer only the first while sounding like it answered the second. On a
    // day when every candidate risks more than the budget, "4 names have a trade
    // worth placing" was contradicted by the very cards it was introducing —
    // each one sized at zero contracts. Both numbers now get said out loud.
    var fits = 0, cheapest = null, budget = null;
    (d.signals || []).forEach(function (s) {
      var sizing = ((s.recommendation || {}).plan || {}).sizing;
      if (!sizing || !ACTIONABLE[(s.recommendation || {}).action]) return;
      if (budget === null && has(sizing.risk_budget)) budget = sizing.risk_budget;
      if (sizing.contracts > 0) fits++;
      else if (has(sizing.risk_per_spread) &&
               (cheapest === null || sizing.risk_per_spread < cheapest)) {
        cheapest = sizing.risk_per_spread;
      }
    });

    var lead = "<b>" + actionable + "</b> of " + (d.signals || []).length +
               " screened names are mispriced today";
    var withBudget = budget === null ? "your risk budget" : "your " + money(budget, 0) + " risk budget";
    $("#summary").innerHTML = !actionable
      ? "No name is mispriced enough today to pay for a position. That is a result, not a gap in the data — " +
        "the cards below show what was read and why each was passed over."
      : fits
        ? lead + ", and <b>" + fits + "</b> " + (fits === 1 ? "fits" : "fit") + " " + withBudget +
          ". Each card below is the whole instruction: the exact legs, what it costs, what it can " +
          "lose, and when to be out."
        : lead + ", but <b>none</b> fit " + withBudget +
          (cheapest === null ? "" : " — the cheapest single spread risks " + money(cheapest, 0)) +
          ". The cards below show each trade and what it would cost.";

    if (!(d.signals || []).length) {
      $("#cards").innerHTML = '<p class="empty">No signals in the last run — no tickers returned usable data.</p>';
      $("#filters").innerHTML = "";
      return;
    }
    renderFilters();
    renderCards();

    var dis = d.disclaimer || {};
    $("#disclaimer").innerHTML = ["general", "risk", "method"]
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
    { k: "screen", h: "Screen",
      v: function (s) {
        var c = (s.screen || {}).compliant;
        return c === false ? 0 : c === true ? 2 : 1;      // worst sorts first
      },
      f: function (s) {
        var sc = s.screen;
        if (!sc) return '<span class="dim">—</span>';
        if (sc.compliant === true) return '<span class="dim">ok</span>';
        return '<span class="flagtext" title="' + esc((sc.reasons || []).join("; ")) + '">' +
          (sc.compliant === false ? "fails" : "?") + "</span>";
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
      return sortableTh(c.k, c.h, null, sort);
    }).join("");
    var body = sigs.map(function (s) {
      return "<tr>" + COLUMNS.map(function (c) {
        return '<td class="' + (c.cls || "") + (c.r ? " r" : "") + '">' + c.f(s) + "</td>";
      }).join("") + "</tr>";
    }).join("");

    $("#scantable").innerHTML = '<div class="tablewrap"><table class="scan"><thead><tr>' +
      head + "</tr></thead><tbody>" + body + "</tbody></table></div>";

    wireSort(document.querySelectorAll("#scantable th"), function (k) {
      if (SORT.key === k) SORT.dir = -SORT.dir;
      else { SORT.key = k; SORT.dir = (k === "rank" || k === "ticker") ? 1 : -1; }
      renderScanner();
      // Keep the caller where they were: re-rendering replaced the node they
      // were standing on, and focus would otherwise fall back to the document.
      var again = $('#scantable th[data-key="' + k + '"]');
      if (again) again.focus();
    });
  }


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

  function multiExpiry(plan) {
    var seen = {};
    (plan.legs || []).forEach(function (l) { if (l.expiry) seen[l.expiry] = 1; });
    return Object.keys(seen).length > 1;
  }

  function legSummary(plan) {
    var txt = (plan.legs || []).map(function (l) {
      if (l.right === "share") return "own 100";
      return (l.action === "buy" ? "+" : "−") + num(l.strike, 0) +
        String(l.right || "").charAt(0);
    }).join(" ");
    return esc(txt) + (multiExpiry(plan) ? ' <span class="faint">diag</span>' : "");
  }

  function rewardToRisk(plan) {
    if (!has(plan.max_profit) || !has(plan.max_loss) || !plan.max_loss) return null;
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
    if (multiExpiry(plan)) return null;
    var rr = rewardToRisk(plan);
    if (rr === null || !has(plan.dte) || !plan.dte) return null;
    return rr * 365 / plan.dte;
  }

  // One row per candidate, with the parent ticker's block carried along so the
  // detail panel can show that name's summary and caveats.
  function spreadRows() {
    var out = [];
    (store.scan.signals || []).forEach(function (s) {
      var ld = s.long_dated;
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

  var SPREAD_COLUMNS = [
    { k: "ticker", h: "Ticker", cls: "t",
      f: function (r) {
        return esc(r.ticker) + (r.preferred ? ' <span class="pick" title="What the ' +
          'scanner’s directional read points at for this name">pick</span>' : "");
      } },
    { k: "name", h: "Structure", v: function (r) { return r.plan.name; },
      f: function (r) { return esc(r.plan.name || r.plan.key); } },
    { k: "dte", h: "Expiry", v: function (r) { return r.plan.dte; },
      f: function (r) {
        return esc(r.plan.expiry || "—") +
          '<span class="dim"> · ' + num(r.plan.dte, 0) + "d</span>";
      } },
    { k: "legs", h: "Legs", cls: "mono", v: function (r) { return r.plan.key; },
      f: function (r) { return legSummary(r.plan); } },
    { k: "net", h: "Net", r: true, v: function (r) { return r.plan.net; },
      f: function (r) {
        if (!has(r.plan.net)) return "—";
        var d = r.plan.net > 0;
        return '<span class="net ' + (d ? "debit" : "credit") + '">' +
          (d ? "debit " : "credit ") + money(r.plan.net, 0) + "</span>";
      } },
    // A leg with no two-sided market leaves these null. That is "not priced",
    // which must not be shown as an uncapped win or an undefined loss.
    { k: "max_profit", h: "Max profit", r: true, v: function (r) { return r.plan.max_profit; },
      f: function (r) {
        if (has(r.plan.max_profit)) return money(r.plan.max_profit, 0);
        return has(r.plan.max_loss) ? "uncapped" : "—";
      } },
    { k: "max_loss", h: "Max loss", r: true, v: function (r) { return r.plan.max_loss; },
      f: function (r) {
        if (has(r.plan.max_loss)) return money(r.plan.max_loss, 0);
        return r.plan.risk === "undefined" ? "undefined" : "—";
      } },
    { k: "rr", h: "R : R", r: true, v: function (r) { return rewardToRisk(r.plan); },
      f: function (r) {
        var v = rewardToRisk(r.plan);
        return v === null ? "—" : num(v, 2) + "×";
      } },
    { k: "ror", h: "RoR / yr", r: true, v: function (r) { return annualisedReturn(r.plan); },
      f: function (r) {
        var v = annualisedReturn(r.plan);
        if (v !== null) {
          return '<span title="Max profit over max loss, put on a yearly footing so it ' +
            'compares with a monthly trade. Assumes the position could be repeated; it is not ' +
            'a forecast.">' + pct(v * 100, 0) + "</span>";
        }
        if (multiExpiry(r.plan)) {
          var horizon = has(r.plan.profit_horizon_dte)
            ? num(r.plan.profit_horizon_dte, 0) + " days"
            : "the short leg's expiry";
          return '<span class="dim" title="Not annualised: this trade\u2019s legs expire on ' +
            'different dates. Its maximum lands at ' + horizon + ', not at the ' +
            num(r.plan.dte, 0) + '-day expiry in the Expiry column, and it is a single ' +
            'assignment rather than something you repeat — the structure actually earns by ' +
            'rolling the short leg, which no figure here counts.">n/a</span>';
        }
        return "—";
      } },
    { k: "breakevens", h: "Breakeven", r: true,
      v: function (r) { return (r.plan.breakevens || [])[0]; },
      f: function (r) {
        var b = r.plan.breakevens || [];
        return b.length ? b.map(function (x) { return num(x, 2); }).join(" / ") : "—";
      } },
    { k: "pop", h: "POP", r: true, v: function (r) { return r.plan.pop; },
      f: function (r) { return has(r.plan.pop) ? pct(r.plan.pop * 100, 0) : "—"; } },
    { k: "size", h: "Size", r: true,
      v: function (r) { return (r.plan.sizing || {}).contracts; },
      f: function (r) { return sizeCell(r.plan.sizing); } }
  ];

  function spreadDetail(r) {
    var b = r.block;
    var head = '<div class="sd-head">' + esc(r.ticker) + " at " + num(r.price, 2) +
      " · " + esc(b.expiry) + " · " + num(b.dte, 0) + " days" +
      (has(b.iv_annual) ? " · ATM IV " + pct(b.iv_annual, 0) : "") +
      " · liquidity " + esc(b.liquidity || "unknown") +
      (has(b.atm_spread_pct) ? " (spread " + pct(b.atm_spread_pct, 0) + ")" : "") + "</div>";
    // The block summary is about the ticker's pick. On any other row it would
    // read as a description of the structure you just opened, which it is not.
    var sum = b.summary || "";
    if (b.preferred && !r.preferred) {
      var pick = (b.candidates || []).filter(function (c) { return c.key === b.preferred; })[0];
      sum = "Not the pick for " + r.ticker + " — the scanner points at the " +
        ((pick && pick.name) || b.preferred) + " here. This is one of the other structures " +
        "the chain supports.";
    }
    var body = [head, '<p class="sd-sum">' + esc(sum) + "</p>", legsTable(r.plan)];
    if (r.plan.playbook) {
      body.push('<div class="notes"><div class="t">What this trade is</div>' +
        '<p style="margin:0;font-size:.87rem;color:var(--text)">' + esc(r.plan.playbook) + "</p></div>");
    }
    body.push(manageBlock(r.plan));
    body.push(noteList("Watch out", (b.warnings || []).map(esc), "warns"));
    body.push(riskFormNote(r.plan));
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
      return '<button class="chip" data-key="' + esc(k) + '" aria-pressed="' + (on ? "true" : "false") +
        '">' + esc(labels[k]) + '<span class="n">' + counts[k] + "</span></button>";
    }).join("");
    html += '<button class="chip pickonly" data-only="1" aria-pressed="' +
      (spreadFilters.preferredOnly ? "true" : "false") + '">Picks only</button>';
    $("#spreadfilters").innerHTML = html;

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
    var host = $("#spreadtable");
    if (!force && host.dataset.done) return;
    host.dataset.done = "1";

    var all = spreadRows();
    var summary = store.scan.long_dated || {};
    $("#spreadmeta").innerHTML = all.length
      ? "<b>" + num(summary.candidates || all.length, 0) + "</b> long-dated spreads across <b>" +
        num(summary.tickers, 0) + "</b> names · expiries " +
        esc((summary.expiries || []).join(", ")) + " · target " +
        num(summary.target_days, 0) + " days · " + num(summary.preferred, 0) +
        " carry a directional pick"
      : "";

    if (!all.length) {
      host.innerHTML = '<p class="empty">No long-dated spreads in the last run.</p>' +
        '<p class="empty faint">Only the top-ranked names are priced. Of those, some have no ' +
        "listed expiry near 13 months — LEAPS exist mostly on large caps — and others have the " +
        "expiry but no usable quotes on it. A scan that runs outside US market hours reads a " +
        "chain with no bid, no ask and no open interest, which is not enough to price a spread " +
        "against. The scheduled run is half an hour after the close for that reason.</p>";
      $("#spreadfilters").innerHTML = "";
      return;
    }
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
      if (!has(x)) x = -Infinity;
      if (!has(y)) y = -Infinity;
      if (typeof x === "string" || typeof y === "string") {
        return String(x).localeCompare(String(y)) * SPREAD_SORT.dir;
      }
      return (x - y) * SPREAD_SORT.dir;
    });

    var head = SPREAD_COLUMNS.map(function (c) {
      var sort = c.k === SPREAD_SORT.key ? (SPREAD_SORT.dir === 1 ? "ascending" : "descending") : "none";
      return sortableTh(c.k, c.h, null, sort);
    }).join("");

    var body = rows.length ? rows.map(function (r) {
      return '<tr class="srow' + (r.preferred ? " picked" : "") + '" data-id="' + esc(r.id) +
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
    wireSort(host.querySelectorAll("table.spreads > thead th"), function (k) {
      if (SPREAD_SORT.key === k) SPREAD_SORT.dir = -SPREAD_SORT.dir;
      else { SPREAD_SORT.key = k; SPREAD_SORT.dir = (k === "ticker" || k === "name") ? 1 : -1; }
      renderSpreads(true);
      var again = $('table.spreads > thead th[data-key="' + k + '"]', host);
      if (again) again.focus();
    });

    var panel = $("#spreaddetail", host);
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
    var color = theme(up ? "--up" : "--down");
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
      renderSeasonality(d);
    }).catch(function (e) {
      host.innerHTML = loadError(e, "charts", "python run.py");
      $("#seasonbody").innerHTML = loadError(e, "charts", "python run.py");
      $("#chartmeta").textContent = "";
      $("#seasonmeta").textContent = "";
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
  var UP_RGB = themeRgb("--up"), DOWN_RGB = themeRgb("--down"), THIN_RGB = themeRgb("--wait");

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
                 '" class="ylab">' + esc(opt.fmt(t[0])) + "</text>");
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
                 (grey ? "0.35" : "0.85") + '"><title>' + esc(opt.title(r)) + "</title></rect>");

      // Direct-label the extremes only; the table below carries every number.
      if (!grey && (r.month === opt.best || r.month === opt.worst)) {
        parts.push('<text x="' + mid.toFixed(1) + '" y="' + (up ? y - 5 : y + h + 11).toFixed(1) +
                   '" class="blab">' + esc(opt.fmt(v)) + "</text>");
      }
    });

    return '<svg class="monthchart" viewBox="0 0 ' + W + " " + H + '" ' +
      'preserveAspectRatio="xMidYMid meet" role="img" aria-label="' + esc(opt.aria) + '">' +
      parts.join("") + "</svg>";
  }

  function seasonPanel(title, sub, svg) {
    return '<div class="panelcard"><h3>' + esc(title) + "</h3>" +
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

  // Callers pass a series that has a seasonality block: seasonHeat filters on it
  // and the pooled row is only built when the payload carries one.
  function heatRow(name, seas, scale, minYears, cls) {
    var cells = seas.months.map(function (m) {
      if (m.avg_pct === null) return '<td class="r faint">—</td>';
      var weak = thinMonth(m, minYears);
      var behind = m.ticker_years
        ? m.n + " name-months, " + m.ticker_years.median + " years for the typical name" +
          (m.ticker_years.min < m.ticker_years.median ? " (fewest " + m.ticker_years.min + ")" : "")
        : m.n + " observation" + (m.n === 1 ? "" : "s") + " over " + m.years +
          " year" + (m.years === 1 ? "" : "s");
      var tip = MONTH_NAMES[m.month - 1] + ": average " + (m.avg_pct >= 0 ? "+" : "") +
        num(m.avg_pct, 2) + "%, median " + (m.median_pct >= 0 ? "+" : "") + num(m.median_pct, 2) +
        "%, up " + num(m.win_rate_pct, 0) + "% of the time, " + behind +
        (weak ? " — too few to rank" : "");
      return '<td class="r' + (weak ? " faint" : "") + '" title="' + esc(tip) + '" style="' +
        (weak ? "" : heatStyle(m.avg_pct, scale)) + '">' +
        (m.avg_pct >= 0 ? "+" : "") + num(m.avg_pct, 1) + "</td>";
    }).join("");
    var span = seas.years.start + "–" + seas.years.end + ", " + seas.observations +
      " whole months measured";
    return '<tr class="' + cls + '"><td class="t">' + esc(name) + "</td>" +
      '<td class="r faint" title="' + esc(span) + '">' + seas.years.count + "</td>" + cells +
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
      return sortableTh(k, label, cls || "", sort);
    }
    var head = th("ticker", "Name") + th("years", "Yrs", "r") +
      MONTH_NAMES.map(function (n, i) { return th(i + 1, n.slice(0, 3), "r"); }).join("") +
      "<th>Best</th><th>Worst</th>";

    var body = (d.seasonality
      ? heatRow("All " + d.seasonality.tickers + " names", d.seasonality, scale, minYears, "pool")
      : "") + rows.map(function (s) {
        return heatRow(s.ticker, s.seasonality, scale, minYears, "");
      }).join("");

    return '<h2>Every name, month by month</h2>' +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Average return in each calendar month, ' +
      'in percent. Hover a cell for the median, the hit rate and how many years stand behind it; ' +
      'click a month to rank the names by it. Greyed cells rest on fewer than ' + minYears +
      " years — for the pooled row, fewer than that for the typical name — and are never named " +
      "best or worst.</p>" +
      '<div class="tablewrap"><table class="scan heat"><thead><tr>' + head +
      "</tr></thead><tbody>" + body + "</tbody></table></div>";
  }

  function monthYears(row) {
    return row.ticker_years ? row.ticker_years.median : row.years;
  }

  // The pooled year span can be carried by one long history, so the headline
  // quotes the median name's instead — the number the ranking actually gates on.
  function typicalYears(pooled) {
    var years = (pooled.months || []).map(function (m) {
      return m.ticker_years ? m.ticker_years.median : null;
    }).filter(has).sort(function (a, b) { return a - b; });
    return years.length ? years[Math.floor((years.length - 1) / 2)] : pooled.years.count;
  }

  function seasonHeadline(pooled, minYears) {
    function tile(row, cls, label) {
      if (!row) {
        // The ranking gate is years per name, not months — say which one bit.
        return '<div class="rule"><span class="k">' + label + '</span>' +
          '<div class="v">No month yet has ' + minYears +
          " years behind the typical name, so none is called best or worst.</div></div>";
      }
      return '<div class="rule ' + cls + '"><span class="k">' + label + " — " +
        MONTH_NAMES[row.month - 1] + "</span><div class=\"v\">" +
        (row.avg_pct >= 0 ? "+" : "") + num(row.avg_pct, 2) + "% on average · higher in " +
        num(row.win_rate_pct, 0) + "% of them · " + row.n + " name-months, " +
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
    var host = $("#seasonbody");
    var pooled = d.seasonality;
    var minYears = (pooled && pooled.min_years) || 3;

    if (!pooled) {
      $("#seasonmeta").textContent = "";
      host.innerHTML = pooled === undefined
        ? '<p class="empty">This scan was written before the seasonality view existed — the next ' +
          'run adds it.<br><span class="faint">Locally: <code>python run.py</code></span></p>'
        : '<p class="empty">The window holds too few whole months to group by calendar month. ' +
          "A longer <code>charts.history_period</code> fixes it.</p>";
      return;
    }

    $("#seasonmeta").textContent = pooled.tickers + " names · " + pooled.years.start + "–" +
      pooled.years.end + " · " + pooled.observations + " whole months measured";

    var rows = pooled.months;
    var avgChart = monthChart(rows, {
      value: function (r) { return r.avg_pct; },
      baseline: 0, best: pooled.best_month, worst: pooled.worst_month, minYears: minYears,
      fmt: function (v) { return (v >= 0 ? "+" : "") + num(v, 1) + "%"; },
      title: function (r) {
        return MONTH_NAMES[r.month - 1] + ": " + (r.avg_pct >= 0 ? "+" : "") + num(r.avg_pct, 2) +
          "% average, " + (r.median_pct >= 0 ? "+" : "") + num(r.median_pct, 2) + "% median (" +
          r.n + " name-months, " + monthYears(r) + " years per name)";
      },
      aria: "Average return by calendar month across every screened name"
    });
    var winChart = monthChart(rows, {
      value: function (r) { return r.win_rate_pct; },
      baseline: 50, clamp: [0, 100], best: pooled.best_month, worst: pooled.worst_month,
      minYears: minYears,
      fmt: function (v) { return num(v, 0) + "%"; },
      title: function (r) {
        return MONTH_NAMES[r.month - 1] + ": higher in " + num(r.win_rate_pct, 0) + "% of " +
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

    wireSort(document.querySelectorAll("#seasonbody table.heat th"), function (k) {
      if (String(seasonSort.key) === String(k)) seasonSort.dir = -seasonSort.dir;
      else { seasonSort.key = k; seasonSort.dir = k === "ticker" ? 1 : -1; }
      renderSeasonality(store.charts);
      var again = $('#seasonbody table.heat th[data-key="' + k + '"]');
      if (again) again.focus();
    });
  }

  function showChartView(view) {
    if (CHART_VIEWS.indexOf(view) === -1) view = "prices";
    curView = view;
    var buttons = document.querySelectorAll("#chartviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].setAttribute("aria-pressed", buttons[i].dataset.view === view ? "true" : "false");
    }
    var panes = document.querySelectorAll('.panel[data-tab="charts"] > div[data-view]');
    for (var j = 0; j < panes.length; j++) panes[j].hidden = panes[j].dataset.view !== view;
    try { localStorage.setItem("chartview", view); } catch (e) { /* private mode */ }
    writeHash();
  }


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

  var rp = { ticker: "", dir: "up", week: 37, hold: 8, target: 8, years: 10 };
  var rpAt = null;                          // "2025-W37" -> position on the axis
  var rpSort = { key: "rate", dir: -1 };

  // The optional money section. `dir` is not in here: a debit spread is a call
  // spread going up and a put spread going down, and that is the same question
  // the direction chip already answers — two controls for one fact would let
  // them disagree.
  var sp = { on: false, long: 0, short: 8, debit: 40, contracts: 1 };
  var spSort = { key: "net", dir: -1 };

  function spDeal() {
    return { dir: rp.dir, long: sp.long, short: sp.short, debit: sp.debit,
             contracts: sp.contracts };
  }

  function rpStore() {
    try { localStorage.setItem("repeat", JSON.stringify(rp)); } catch (e) { /* private mode */ }
  }

  function spStore() {
    try { localStorage.setItem("repeat-spread", JSON.stringify(sp)); } catch (e) { /* private */ }
  }

  function signed(v, digits) {
    if (!has(v) || isNaN(v)) return "—";
    return (v >= 0 ? "+" : "") + num(v, digits === undefined ? 1 : digits) + "%";
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
    return (m > 0 ? "+" : m < 0 ? "−" : "") + num(Math.abs(m), 1) + "%";
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
        num((n / t.decided) * 100, 0) + "%)";
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
               (t.median_weeks ? ", typically in week " + num(t.median_weeks, 0) + " of it" : "") +
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
        : "entry " + money(r.entry) + ", target " + money(r.target) + ", closed " +
          signed(r.exit_pct) + ", best " + signed(r.best_pct) + ", worst " + signed(r.worst_pct) +
          (r.touched ? ", touched in week " + r.hit_in + " on the way" : ", never touched it");
      var say = r.year + " — " + labels[r.state] + ": " + note;
      // The percentage on the block is the one the colour is about: where the
      // window closed, or where an unfinished one stands so far.
      var shown = r.settled ? signed(r.exit_pct)
        : r.state === "open" ? signed(r.open_pct) : "—";
      return '<div class="yr ' + r.state + '" title="' + esc(say) + '" aria-label="' + esc(say) +
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
        return '<tr class="dim"><td class="t">' + r.year + "</td><td>" + esc(r.start) +
          '</td><td colspan="6" class="faint">' + esc(r.why || "not enough history") +
          '</td><td class="skipped">Skipped</td></tr>';
      }
      // Exit and Touched are coloured independently, and a year that touched
      // and then closed back under the target is exactly why: green in Touched
      // (the profit was there to take), red at the exit (you did not take it,
      // and the exit is what the verdict and a vertical both settle on).
      var exit = r.settled
        ? '<td class="r ' + (r.closed_past ? "hit" : "miss") + '">' + signed(r.exit_pct) + "</td>"
        : '<td class="r faint">running</td>';
      var touched = '<td class="r' + (r.touched ? " hit" : "") + '">' +
        (r.touched ? "week " + r.hit_in : "—") + "</td>";
      // An unfinished window that has already touched is said out loud, because
      // it is the one row a reader might expect in a column it is not in.
      var verdict = r.state === "open"
        ? (r.touched ? "Touched, still open" : "Still open") + " (" + r.ran + "/" + rp.hold + "w)"
        : labels[r.state];
      return "<tr><td class=\"t\">" + r.year + "</td><td>" + esc(r.start) + "</td>" +
        '<td class="r" title="' + esc("the last close of the week beginning " + r.entry_week +
          " — you buy as week " + rp.week + " opens") + '">' + money(r.entry) + "</td>" +
        '<td class="r">' + money(r.target) + "</td>" + exit +
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
      '<td class="r' + (t.touched ? " hit" : "") + '">' + t.touched + " of " + t.decided + "</td>" +
      '<td class="r">' + signed(t.median_worst) + "</td>" +
      "<td>" + num(t.rate, 0) + "% closed · " + num(t.touch_rate, 0) + "% touched</td>" +
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
      x = has(x) ? x : -Infinity; y = has(y) ? y : -Infinity;
      // Equal rates fall back to how many years stand behind them.
      return (x === y ? a.decided - b.decided : x - y) * rpSort.dir;
    });

    function th(k, label, cls) {
      return sortableTh(k, label, cls || "",
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
        (r.ticker === rp.ticker ? " picked" : "") + '" data-ticker="' + esc(r.ticker) +
        '" tabindex="0" title="' + esc(r.thin
          ? r.ticker + " has only " + r.decided + " judged year" + (r.decided === 1 ? "" : "s") +
            " here — shown, but not ranked"
          : "show " + r.ticker + " above") + '">' +
        '<td class="t">' + esc(r.ticker) + "</td>" +
        '<td class="r">' + r.decided + "</td>" +
        '<td class="r' + (r.hit ? " hit" : "") + '">' + r.hit + "</td>" +
        '<td class="r' + (r.decided - r.hit ? " miss" : "") + '">' + (r.decided - r.hit) + "</td>" +
        '<td class="r ' + (r.rate >= 50 ? "hit" : "miss") + '"><b>' + num(r.rate, 0) + "%</b></td>" +
        '<td class="r' + (r.touched ? " hit" : "") + '">' +
          (r.decided ? num((r.touched / r.decided) * 100, 0) + "%" : "—") + "</td>" +
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
      '<td class="r ' + (pooled >= 50 ? "hit" : "miss") + '">' + num(pooled, 0) + "%</td>" +
      '<td class="r' + (sum.touched ? " hit" : "") + '">' + num(pooledTouch, 0) + "%</td>" +
      '<td class="r faint" title="' + esc(medianNote) + '">—</td>' +
      '<td class="r faint" title="' + esc(medianNote) + '">—</td>' +
      "</tr></tfoot>";

    return "<h2>The same question, every name</h2>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Week ' + rp.week + ", " + rp.hold +
      " weeks, " + rpGoal() + rpSide() +
      " by the close of the last week, run across the whole screened list. Click a row to bring " +
      "that name up above. Names with fewer than " + floor + " judged years sit at the bottom, " +
      "greyed: they are reported, never ranked. Green is the target met at the close, red is not, " +
      "and the muted pair on the right is how far each window travelled either way. And these " +
      "names move together, so twenty-nine of them agreeing is nearer one piece of evidence than " +
      "twenty-nine.</p>" +
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
      esc(String(sum.decided)) + " separate ones.</p>";
  }

  function rpCaveats(d) {
    var r = d.reference || {};
    var rules = ["entry", "window", "result", "touch", "incomplete", "prices"]
      .filter(function (k) { return r[k]; })
      .map(function (k) { return "<li>" + esc(r[k]) + "</li>"; }).join("");
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
      "here as the first of those two conditions, not as a backtested return.</p></div>";
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
    rpSweepCache = { key: key, weeks: weeks, best: best };
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
    var host = $("#rp-heat");
    if (!host) return;
    if (!series) { host.innerHTML = ""; return; }

    var sweep = rpSweep(d, series);
    var goal = rpGoal() + rpSide();
    var say = sweep.best
      ? "How often each buy week closed " + goal + ", on these settings. Best is week " +
        sweep.best.week + " at " + num(sweep.best.rate, 0) + "%."
      : "How often each buy week closed " + goal + ", on these settings.";

    var bars = sweep.weeks.map(function (c) {
      var cls = "hs";
      if (c.week === rp.week) cls += " now";
      if (sweep.best && c.week === sweep.best.week) cls += " top";
      if (c.rate === null) {
        return '<i class="' + cls + ' none" title="' + esc("week " + c.week + " — no judged year")
          + '"></i>';
      }
      var note = "week " + c.week + " — " + num(c.rate, 0) + "% closed " + goal + ", " +
        c.hit + " of " + c.decided + (c.thin ? " (too few years to rank)" : "");
      return '<i class="' + cls + (c.thin ? " thin" : "") + '" style="background:' +
        rpHeatColour(c.rate) + '" title="' + esc(note) + '"></i>';
    }).join("");

    var pointer = "";
    if (sweep.best) {
      var when = rpWeekWhen(d, sweep.best.week);
      pointer = '<button type="button" class="bestweek" data-week="' + sweep.best.week +
        '" title="' + esc("set the slider to week " + sweep.best.week) + '">' +
        "Best here: <b>week " + sweep.best.week + "</b>" + (when ? " · w/c " + esc(when) : "") +
        " · " + num(sweep.best.rate, 0) + "% (" + sweep.best.hit + " of " + sweep.best.decided +
        ")</button>";
    }

    host.innerHTML = '<div class="heatbar" role="img" aria-label="' + esc(say) + '">' + bars +
      "</div>" + pointer +
      '<span class="heatnote">' + (sweep.best
        ? "best of 53 weeks tried — and the best of 53 tries is a high bar to clear by luck"
        : "no week here has enough judged years to rank") + "</span>";

    var jump = host.querySelector(".bestweek");
    if (jump) {
      jump.addEventListener("click", function () {
        rp.week = Number(this.dataset.week);
        $("#rp-week").value = rp.week;
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
    var lots = econ.lots > 1 ? " ×" + econ.lots : "";
    var body = econ.rows.map(function (r) {
      // The two ends a vertical can reach are named where they happen: "max"
      // and "expired worthless" read as outcomes where a bare number reads as
      // arithmetic.
      var note = r.maxed ? ' <span class="tag buy">max</span>'
        : r.worthless ? ' <span class="tag sell">worthless</span>' : "";
      return '<tr><td class="t">' + r.year + "</td>" +
        '<td class="r">' + money(r.entry) + "</td>" +
        '<td class="r">' + money(r.long) + " / " + money(r.short) + "</td>" +
        '<td class="r out">−' + cash(r.paid) + "</td>" +
        '<td class="r">' + money(r.exit) + " (" + signed(r.exit_pct) + ")</td>" +
        '<td class="r ' + (r.maxed ? "maxed" : r.worthless ? "zero" : "") + '">+' +
          cash(r.received) + note + "</td>" +
        '<td class="r net ' + (r.net > 0 ? "up" : r.net < 0 ? "down" : "") + '">' +
          cash(r.net) + "</td>" +
        '<td class="r">' + signed(r.roi, 0) + "</td></tr>";
    }).join("");

    var foot = "<tfoot><tr>" +
      '<td class="t">' + econ.years + " year" + (econ.years === 1 ? "" : "s") + lots + "</td>" +
      "<td></td><td></td>" +
      '<td class="r out">−' + cash(econ.paid) + "</td>" +
      "<td></td>" +
      '<td class="r">+' + cash(econ.received) + "</td>" +
      '<td class="r net ' + (econ.net > 0 ? "up" : econ.net < 0 ? "down" : "") + '">' +
        cash(econ.net) + "</td>" +
      '<td class="r">' + signed(econ.roi, 0) + "</td></tr></tfoot>";

    return '<div class="tablewrap"><table class="scan money"><thead><tr>' +
      '<th>Year</th><th class="r">Entry</th><th class="r">Long / short</th>' +
      '<th class="r">Cash out</th><th class="r">Stock at expiry</th>' +
      '<th class="r">Cash in</th><th class="r">Net</th><th class="r">Return</th>' +
      "</tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>";
  }

  /* What the same structure would have done on every other name. */
  function spAllTable(d) {
    var floor = d.min_years || 3;
    var rows = [];
    for (var i = 0; i < d.series.length; i++) {
      var econ = SpreadTrial.economics(rpTrial(d, d.series[i]), spDeal());
      if (!econ.years) continue;
      rows.push({ ticker: d.series[i].ticker, years: econ.years, paid: econ.paid,
                  received: econ.received, net: econ.net, roi: econ.roi, won: econ.won,
                  breakeven: econ.breakeven, thin: econ.years < floor });
    }
    if (!rows.length) return "";

    var key = spSort.key;
    rows.sort(function (a, b) {
      if (key === "ticker") return a.ticker.localeCompare(b.ticker) * spSort.dir;
      // Same floor, same reason as the ranking above: a name with two judged
      // years is reported, never ranked. It matters more here, not less — the
      // biggest net on the screen could be one lucky year.
      if (a.thin !== b.thin) return a.thin ? 1 : -1;
      var x = has(a[key]) ? a[key] : -Infinity, y = has(b[key]) ? b[key] : -Infinity;
      return (x === y ? a.years - b.years : x - y) * spSort.dir;
    });

    function th(k, label, cls) {
      return sortableTh(k, label, cls || "",
        spSort.key === k ? (spSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    var sum = rows.reduce(function (a, r) {
      a.paid += r.paid; a.received += r.received; a.years += r.years; a.won += r.won;
      return a;
    }, { paid: 0, received: 0, years: 0, won: 0 });
    var net = sum.received - sum.paid;

    var body = rows.map(function (r) {
      return '<tr class="srow' + (r.thin ? " thin" : "") +
        (r.ticker === rp.ticker ? " picked" : "") + '" data-ticker="' + esc(r.ticker) +
        '" tabindex="0" title="' + esc(r.thin
          ? r.ticker + " has only " + r.years + " settled year" + (r.years === 1 ? "" : "s") +
            " here — shown, but not ranked"
          : "show " + r.ticker + " above") + '">' +
        '<td class="t">' + esc(r.ticker) + "</td>" +
        '<td class="r">' + r.years + "</td>" +
        '<td class="r">' + r.won + "</td>" +
        '<td class="r out">−' + cash(r.paid) + "</td>" +
        '<td class="r">+' + cash(r.received) + "</td>" +
        '<td class="r net ' + (r.net > 0 ? "up" : r.net < 0 ? "down" : "") + '">' +
          cash(r.net) + "</td>" +
        '<td class="r">' + signed(r.roi, 0) + "</td>" +
        '<td class="r">' + num(r.breakeven, 0) + "%</td></tr>";
    }).join("");

    var foot = "<tfoot><tr>" +
      '<td class="t">' + rows.length + " name" + (rows.length === 1 ? "" : "s") + "</td>" +
      '<td class="r">' + sum.years + "</td>" +
      '<td class="r">' + sum.won + "</td>" +
      '<td class="r out">−' + cash(sum.paid) + "</td>" +
      '<td class="r">+' + cash(sum.received) + "</td>" +
      '<td class="r net ' + (net > 0 ? "up" : net < 0 ? "down" : "") + '">' + cash(net) + "</td>" +
      '<td class="r">' + signed(sum.paid ? (net / sum.paid) * 100 : null, 0) + "</td>" +
      '<td class="r faint" title="' + esc("no total: each name breaks even at its own debit") +
        '">—</td></tr></tfoot>';

    return "<h3>The same structure, every name</h3>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Every name bought on the same ' +
      "rule and priced on the same assumption — so this column of nets is one assumption " +
      "repeated twenty-nine times, not twenty-nine pieces of evidence. <b>Breakeven</b> is the " +
      "debit, as a share of width, that would have left that name exactly square: under it the " +
      "run made money, over it it did not, and it is the one column here that needs no view on " +
      "what the spread cost. Click a row to bring that name up above.</p>" +
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
    var verdict = econ.net > 0 ? "cheap" : econ.net < 0 ? "rich" : "fair";
    var structure = (rp.dir === "up" ? "call" : "put") + " debit spread, " + num(sp.long, 1) +
      "% / " + num(sp.short, 1) + "%, held " + rp.hold + " weeks";
    return '<div class="rulebar">' +
      tile(verdict,
           "Net over " + econ.years + " year" + (econ.years === 1 ? "" : "s") + " — " +
           cash(econ.net),
           cash(econ.received) + " came back against " + cash(econ.paid) + " paid out, on a " +
           structure + (econ.lots > 1 ? ", " + econ.lots + " contracts a year" : "") +
           ". Commission and slippage are not in it, and neither is the fact that a real debit "
           + "would not have been the same every year.") +
      tile("fair", "Return on the money risked — " + signed(econ.roi, 0),
           "Net divided by everything paid in. Not annualised, and not a portfolio return: the "
           + "cash is only at risk for " + rp.hold + " weeks of each year, and a debit vertical "
           + "can lose all of it.") +
      tile(econ.breakeven === null ? "fair" : sp.debit <= econ.breakeven ? "cheap" : "rich",
           "Breakeven debit — " + num(econ.breakeven, 0) + "% of width",
           "Pay less than this and the run made money, more and it did not. This is the one "
           + "number here that does not rest on your assumption, so it is the one to take to a "
           + "live quote. You have set " + num(sp.debit, 0) + "%.") +
      tile("fair", "Won " + econ.won + " of " + econ.years +
           (econ.maxed ? " · " + econ.maxed + " at max" : ""),
           econ.worthless + " expired worthless, which for a debit vertical means the whole "
           + "premium gone. A win rate is not an edge until the sizes are in it — that is what "
           + "the net on the left is for.") +
      "</div>";
  }

  function spDraw() {
    var host = $("#moneybody"), d = store.weekly, section = $("#spreadsection");
    if (section) section.hidden = !sp.on;
    if (!sp.on || !d || !host) return;

    var series = null;
    for (var i = 0; i < d.series.length; i++) {
      if (d.series[i].ticker === rp.ticker) { series = d.series[i]; break; }
    }
    if (!series) { host.innerHTML = ""; return; }

    var econ = SpreadTrial.economics(rpTrial(d, series), spDeal());
    if (econ.why) {
      host.innerHTML = '<p class="empty">' + esc(econ.why) +
        " — the strike you sell is what caps the payout, so it has to sit further out than the " +
        "one you buy.</p>";
      return;
    }

    host.innerHTML = (econ.years ? spTiles(econ) : "") +
      "<h3>" + esc(rp.ticker) + ", year by year</h3>" +
      spYearTable(econ) + spAllTable(d);

    // Scoped to table.money, and spSort is its own: the ranking above sorts on
    // keys this table does not have, and sharing one sort state made picking a
    // column in one table quietly scramble the other.
    wireSort(host.querySelectorAll("table.money thead th"), function (k) {
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
    var d = store.weekly, host = $("#repeatbody");
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

    host.innerHTML = short + rpTiles(t) +
      (pending.length
        ? '<p class="faint" style="font-size:.83rem;margin:-12px 0 16px">' +
          esc(pending.join(" · ")) + " — counted in neither column.</p>"
        : "") +
      rpStrip(t) + rpYearTable(t) + rpAllTable(d) + rpCaveats(d);

    wireSort(host.querySelectorAll("table.scan thead th"), function (k) {
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
    $("#rp-ticker").value = ticker;
    rpStore();
    rpDraw();
    $("#repeatcontrols").scrollIntoView({ block: "nearest" });
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
    $("#rp-weeklabel").textContent = label;
  }

  function rpNum(el, lo, hi, fallback) {
    var v = Math.round(Number(el.value));
    if (isNaN(v)) v = fallback;
    return Math.min(hi, Math.max(lo, v));
  }

  // The money controls. Separate from wireRepeat's, because they redraw only
  // the money section — re-running the whole tab to change a contract count
  // would rebuild thirty names' worth of tables for nothing.
  function wireSpread() {
    // Like rpNum but without the rounding: these controls step in halves, and a
    // 2.5% strike offset that silently became 3% would be a lie on the table.
    function spNum(el, lo, hi, fallback) {
      var v = Number(el.value);
      if (isNaN(v) || el.value === "") v = fallback;
      return Math.min(hi, Math.max(lo, v));
    }
    function onChange(fn) {
      return function () { fn(this); spStore(); spDraw(); };
    }
    $("#sp-long").addEventListener("input", onChange(function (el) {
      sp.long = spNum(el, -50, 100, 0);
    }));
    $("#sp-short").addEventListener("input", onChange(function (el) {
      sp.short = spNum(el, -50, 200, 8);
    }));
    $("#sp-debit").addEventListener("input", onChange(function (el) {
      // A debit of 0 is free money and a debit of 100 is the whole width for
      // certain — neither is a spread, so the range stops short of both.
      sp.debit = spNum(el, 1, 99, 40);
    }));
    $("#sp-contracts").addEventListener("input", onChange(function (el) {
      sp.contracts = Math.round(spNum(el, 1, 1000, 1));
    }));
    $("#sp-on").addEventListener("change", function () {
      sp.on = this.checked;
      spStore();
      spDraw();
      if (sp.on) $("#spreadcontrols").scrollIntoView({ block: "nearest", behavior: "smooth" });
    });

    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("repeat-spread") || "null"); } catch (e) {
      saved = null;
    }
    if (saved) {
      for (var key in sp) if (has(saved[key])) sp[key] = saved[key];
    }
    $("#sp-on").checked = !!sp.on;
    $("#sp-long").value = sp.long;
    $("#sp-short").value = sp.short;
    $("#sp-debit").value = sp.debit;
    $("#sp-contracts").value = sp.contracts;
    $("#spreadsection").hidden = !sp.on;
  }

  function renderRepeat() {
    load("weekly").then(function (d) {
      if (!rpAt) {
        rpAt = SpreadTrial.index(d);

        var select = $("#rp-ticker");
        select.innerHTML = d.series.map(function (s) {
          return '<option value="' + esc(s.ticker) + '">' + esc(s.ticker) + "</option>";
        }).join("");
        // A remembered name that has since dropped out of the screen is not an
        // error; it just is not on this page any more.
        var known = d.series.some(function (s) { return s.ticker === rp.ticker; });
        if (!known) rp.ticker = (d.series[0] || {}).ticker || "";
        select.value = rp.ticker;
        rpWeekLabel(d);
      }
      if (!d.series.length) {
        $("#repeatbody").innerHTML = '<p class="empty">The weekly history is empty — no name in ' +
          "this screen has the " + (d.min_weeks || 26) + " weeks the test needs.</p>";
        return;
      }
      rpDraw();
    }).catch(function (e) {
      $("#repeatbody").innerHTML = loadError(e, "weekly", "python run.py");
    });
  }

  function wireRepeat() {
    function onChange(fn) {
      return function () {
        fn(this);
        rpStore();
        if (store.weekly) { rpWeekLabel(store.weekly); rpDraw(); }
      };
    }
    $("#rp-ticker").addEventListener("change", onChange(function (el) { rp.ticker = el.value; }));
    $("#rp-week").addEventListener("input", onChange(function (el) {
      rp.week = rpNum(el, 1, 53, 37);
    }));
    $("#rp-hold").addEventListener("input", onChange(function (el) {
      rp.hold = rpNum(el, 1, 52, 8);
    }));
    $("#rp-years").addEventListener("input", onChange(function (el) {
      rp.years = rpNum(el, 2, 25, 10);
    }));
    $("#rp-target").addEventListener("input", onChange(function (el) {
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
        if (store.weekly) rpDraw();
      });
    }

    // Whatever was set last time, so a setup survives a reload — the same
    // contract the tab strip has.
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("repeat") || "null"); } catch (e) { saved = null; }
    if (saved) {
      for (var key in rp) if (has(saved[key])) rp[key] = saved[key];
    }
    $("#rp-week").value = rp.week;
    $("#rp-hold").value = rp.hold;
    $("#rp-target").value = rp.target;
    $("#rp-years").value = rp.years;
    for (var k = 0; k < dirs.length; k++) {
      dirs[k].setAttribute("aria-pressed", dirs[k].dataset.dir === rp.dir ? "true" : "false");
    }
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
    // Latched per panel, and only once that panel has actually rendered.
    // Setting one flag up front meant a single transient failure pinned the tab
    // on its error message until a reload — including the ordinary case where
    // calibration.json simply does not exist yet and appears after the next
    // scheduled run.
    if (!host.dataset.backtestDone) renderBacktestPanel(host);
    if (!host.dataset.calibrationDone) renderCalibrationPanel(host);
  }

  function renderBacktestPanel(host) {
    load("backtest").then(function (d) {
      host.dataset.backtestDone = "1";
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
  }

  // Is the fit on this panel the one the scan on the other tabs actually used?
  // It need not be: weights.json is a working file and gitignored, while
  // calibration.json is committed — so a day when the calibration step fails
  // leaves yesterday's fit here beside a scan scored with the built-in weights.
  // Both facts were already on the page, on two different tabs, with nothing
  // reconciling them.
  function calibrationMismatch(d) {
    var w = (store.scan && store.scan.weights) || {};
    if (w.source === "auto-calibrated" && (!d.as_of || w.as_of === d.as_of)) return "";
    var used = w.source === "auto-calibrated"
      ? "weights calibrated " + esc(w.as_of || "on an earlier run")
      : "the built-in weights";
    return '<div class="notice" style="margin:14px 0 0">' +
      "<b>This fit is not what the current scan used.</b> The scan on the other tabs scored with " +
      used + ", while the calibration below is from " + esc(d.as_of || "an earlier run") +
      ". The calibration step is best-effort — when it cannot fetch its history the scan " +
      "falls back and says so, but the last good fit stays published here." +
      "</div>";
  }

  function renderCalibrationPanel(host) {
    load("calibration").then(function (d) {
      host.dataset.calibrationDone = "1";
      if (!d.ok) { $("#calibration").innerHTML = '<p class="empty">' + esc(d.note || "Not calibrated yet.") + "</p>"; return; }
      var sep = d.separation;
      $("#calibration").innerHTML = calibrationMismatch(d) +
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
      $("#calibration").innerHTML = loadError(e, "calibration", "python calibrate.py");
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
      earnings: "Earnings", debt_cash_ratio: "Debt % / Cash %",
      long_dated: "The long-dated expiry", leaps_vega: "Vega over a year",
      reward_to_risk: "Reward to risk", annualised_return: "Reward to risk, annualised",
      risk_form: "What secures a position"
    };
    // Any key without an entry above reads as a sentence rather than as the raw
    // snake_case name, so a new glossary term is never published looking like one.
    function title(k) {
      if (titles[k]) return titles[k];
      var words = k.replace(/_/g, " ");
      return words.charAt(0).toUpperCase() + words.slice(1);
    }
    host.innerHTML = "<dl class=\"glossary\">" + Object.keys(g).map(function (k) {
      return "<dt>" + esc(title(k)) + "</dt><dd>" + esc(g[k]) + "</dd>";
    }).join("") + "</dl>";

    var play = ref("playbook", {});
    $("#strategy-list").innerHTML = "<dl class=\"glossary\">" + Object.keys(play).map(function (k) {
      return "<dt>" + esc(k.replace(/_/g, " ").replace(/\b\w/g, function (c) { return c.toUpperCase(); })) +
        "</dt><dd>" + esc(play[k]) + "</dd>";
    }).join("") + "</dl>";
  }

  // ------------------------------------------------------------------ boot

  // Left/Right (and Home/End) move between tabs, as a tablist is expected to.
  function tabKeydown(e) {
    var keys = { ArrowLeft: -1, ArrowRight: 1, Left: -1, Right: 1 };
    var buttons = [].slice.call(document.querySelectorAll(".tabs button"));
    var here = buttons.indexOf(this);
    var next = null;
    if (e.key in keys) next = (here + keys[e.key] + buttons.length) % buttons.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = buttons.length - 1;
    if (next === null) return;
    e.preventDefault();
    showTab(buttons[next].dataset.tab);
    buttons[next].focus();
  }

  function boot() {
    var buttons = document.querySelectorAll(".tabs button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () { showTab(this.dataset.tab); });
      buttons[i].addEventListener("keydown", tabKeydown);
    }

    var views = document.querySelectorAll("#chartviews button");
    for (var v = 0; v < views.length; v++) {
      views[v].addEventListener("click", function () { showChartView(this.dataset.view); });
    }
    wireRepeat();
    wireSpread();
    // A fragment is an explicit request, so it outranks the last visit's tab.
    var linked = parseHash();
    var savedView = null;
    try { savedView = localStorage.getItem("chartview"); } catch (e) { savedView = null; }
    // Nothing is on screen yet — the showTab below writes both halves at once.
    hashLock = true;
    showChartView((linked && linked.view) || savedView);
    hashLock = false;

    window.addEventListener("hashchange", applyHash);

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
      showTab((linked && linked.tab) || saved || "playbook");
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
