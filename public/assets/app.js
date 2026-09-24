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

  // Pure data -> HTML string helpers live in render.js, so the node tests can
  // run the exact code that escapes what the payload says (see
  // tests/test_render_js.py). Aliased here to keep every call site unchanged.
  var R = window.SpreadRender;
  var esc = R.esc;
  var has = R.has;
  var num = R.num;
  var money = R.money;
  var cash = R.cash;
  var pct = R.pct;
  var multiExpiry = R.multiExpiry;
  var sizeCell = R.sizeCell;
  var legsTable = R.legsTable;
  var noteList = R.noteList;
  var manageBlock = R.manageBlock;
  var altBlock = R.altBlock;
  var riskFormNote = R.riskFormNote;
  var statsTable = R.statsTable;
  var impliedSection = R.impliedSection;

  var DATA_DIR = "data/";
  // The payload shape this page was written against. Every file the backend
  // writes carries the same `schema_version`, so one constant checks them all —
  // and having it in one place is the point: scan.json was the only one being
  // checked, which is how weekly.json came to be read by a page that had no way
  // of noticing it was reading a stale one.
  var SCHEMA_MAJOR = "2";
  var store = { scan: null, charts: null, weekly: null, backtest: null, calibration: null };
  var filters = { actions: new Set(), query: "" };

  // ------------------------------------------------------------- utilities

  function $(sel, root) { return (root || document).querySelector(sel); }

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
  var TAB_NAMES = ["playbook", "spreads", "scanner", "charts", "repeat", "backtest",
                   "validation", "reference"];
  var CHART_VIEWS = ["prices", "seasonality"];
  /* The Backtest tab's four views. Same idea as the Charts tab's, and they
     share the fragment's view segment — the lists must not collide, because a
     segment is matched against both and the tab it implies is whichever list
     claimed it. */
  var BACKTEST_VIEWS = ["rules", "sweep", "search", "money"];
  var VIEW_TABS = { charts: CHART_VIEWS, backtest: BACKTEST_VIEWS };

  // Which tab a view name belongs to, or "" if it is not a view name at all.
  function viewTab(lower) {
    for (var tab in VIEW_TABS) {
      if (VIEW_TABS[tab].indexOf(lower) !== -1) return tab;
    }
    return "";
  }
  /* The two tabs that are *about* one name, and so carry it in the fragment:
   *
   *     …/#backtest#NVDA   the Backtest tab, on NVDA
   *     …/#repeat#AAPL     the Repeat test, on AAPL
   *
   * Their dials — hold, target, lookback — stay out of the URL and in
   * localStorage, because those are working state. The name is not: it is what
   * the tables on screen are *about*, and "look at NVDA's squeeze" is the thing
   * a reader wants to send someone. It is also what lets a Scanner row link
   * straight into a backtest of that name, which needs no new machinery — an
   * ordinary anchor and the fragment handler already here. */
  var NAMED_TABS = { repeat: 1, backtest: 1 };
  // A plausible ticker, and nothing that could be a tab or a chart view. Both
  // of those are checked first, so this only has to be narrow enough not to
  // swallow a typo into something that looks like a name.
  var TICKER_RE = /^[A-Za-z][A-Za-z0-9.\-]{0,9}$/;
  var curTab = "playbook";
  var curView = "prices";
  var btView = "rules";
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
      seg = seg.trim();
      // Tab and view names are matched case-insensitively; a ticker keeps the
      // case it is written in until it is upper-cased, because "#backtest#nvda"
      // is a reasonable thing to type and NVDA is what the data calls it.
      var lower = seg.toLowerCase();
      if (!out.tab && TAB_NAMES.indexOf(lower) !== -1) out.tab = lower;
      else if (!out.view && viewTab(lower)) out.view = lower;
      else if (!out.ticker && TICKER_RE.test(seg)) out.ticker = seg.toUpperCase();
    }
    // A bare "#seasonality" or "#sweep" can only mean one tab, so read it as
    // that tab. A view named alongside the wrong tab is dropped rather than
    // allowed to move the tab out from under it.
    if (out.view && !out.tab) out.tab = viewTab(out.view);
    else if (out.view && viewTab(out.view) !== out.tab) out.view = undefined;
    return out.tab ? out : null;
  }

  // The name the tab on screen is about, for the tabs that are about one.
  function tabName(tab) {
    return tab === "repeat" ? rp.ticker : tab === "backtest" ? bt.ticker : "";
  }

  function writeHash() {
    if (hashLock) return;
    var name = NAMED_TABS[curTab] ? tabName(curTab) : "";
    /* Name first, then view — "#backtest#NVDA#sweep" reads as a name being
       looked at a particular way, which is the order the sentence goes in.
       `parseHash` matches each segment on its own, so it accepts either. The
       default view is left off: an unadorned "#backtest#NVDA" is the link
       worth having, and the Charts tab has always written its view out. */
    var view = curTab === "charts" ? "#" + curView
      : curTab === "backtest" && btView !== "rules" ? "#" + btView : "";
    var want = "#" + curTab + (name ? "#" + name : "") + view;
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
  /* A name carried in the fragment, applied to the tab that was asked for.

     Deliberately *before* showTab: the tab's render reads this state, and
     setting it afterwards would draw the old name and then redraw. A name the
     screen does not have is left to the tab's own fallback — the same one a
     remembered name that has since dropped out of the screen goes through —
     rather than being a special case here. */
  function applyName(tab, ticker) {
    if (!NAMED_TABS[tab] || !ticker) return;
    // A name this screen does not have is ignored outright once the payload is
    // in — not adopted and then fallen back from. Adopting it blanked the
    // picker and left every table below asking about a name that is not there.
    // Before the payload lands there is nothing to check against, so it is
    // taken on trust and the render's own fallback catches it.
    if (store.weekly && !store.weekly.series.some(function (x) { return x.ticker === ticker; })) {
      return;
    }
    if (tab === "repeat") { rp.ticker = ticker; rpStore(); } else { bt.ticker = ticker; btStore(); }
    if (!store.weekly) return;
    var select = $(tab === "repeat" ? "#rp-ticker" : "#bt-ticker");
    if (select && select.options.length) select.value = ticker;
  }

  function applyHash() {
    var want = parseHash();
    if (want) {
      hashLock = true;
      // The view belongs to the tab that owns its name; parseHash has already
      // dropped one that came with the wrong tab.
      if (want.view && want.tab === "charts") showChartView(want.view);
      else if (want.view && want.tab === "backtest") showBacktestView(want.view);
      applyName(want.tab, want.ticker);
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
    // an unknown name has to mean the first tab rather than a page of hidden
    // panels.
    if (TAB_NAMES.indexOf(name) === -1) name = "playbook";
    curTab = name;
    var buttons = document.querySelectorAll(".tabs button");
    for (var i = 0; i < buttons.length; i++) {
      var on = buttons[i].dataset.tab === name;
      buttons[i].setAttribute("aria-selected", on ? "true" : "false");
      // Roving tabindex: one tab stop for the whole strip, arrows move within
      // it. One tab stop per tab in front of the content is the thing this
      // pattern exists to avoid.
      buttons[i].tabIndex = on ? 0 : -1;
    }
    var panels = document.querySelectorAll(".panel");
    for (var j = 0; j < panels.length; j++) panels[j].hidden = panels[j].dataset.tab !== name;
    try { localStorage.setItem("tab", name); } catch (e) { /* private mode */ }
    if (name === "spreads") renderSpreads();
    if (name === "charts") renderCharts();
    if (name === "repeat") renderRepeat();
    if (name === "backtest") renderBacktest();
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
      '<span class="tkr"><a class="totest" href="#backtest#' + esc(sig.ticker) + '" title="' +
        esc("backtest a rule on " + sig.ticker) + '">' + esc(sig.ticker) + "</a></span>" +
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
    // Whether straddles and strangles were on the menu this run, and the
    // evidence that decided it. Withheld is the default until the record says
    // buying premium on the setup pays.
    if (d.long_vol && d.long_vol.supported === false) {
      $("#summary").innerHTML += ' <span class="dim">Straddles and strangles are withheld: ' +
        esc(d.long_vol.text) + "</span>";
    }

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
    // The ticker is a link into the Backtest tab on that name. An ordinary
    // anchor, because the fragment handler above already knows what to do with
    // one — no click handler, and it works from a middle-click or a copied
    // address like any other link on the page.
    { k: "ticker", h: "Ticker", cls: "t", f: function (s) {
        return '<a class="totest" href="#backtest#' + esc(s.ticker) + '" title="' +
          esc("backtest a rule on " + s.ticker) + '">' + esc(s.ticker) + "</a>";
      } },
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
        num(m.avg_pct, 2) + "%, median " + (m.median_pct >= 0 ? "+" : "") + num(m.median_pct, 2) +
        "%, up " + num(m.win_rate_pct, 0) + "% of the time, " + behind +
        (weak ? " — too few to rank" : "") + " — click for every year.";
      return '<td class="r seasoncell' + (weak ? " faint" : "") + '" tabindex="0" role="button" ' +
        'aria-pressed="false" data-ticker="' + esc(tickerId) + '" data-month="' + m.month +
        '" title="' + esc(tip) + '" style="' + (weak ? "" : heatStyle(m.avg_pct, scale)) + '">' +
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
        return '<option value="' + esc(t) + '"' + (t === ticker ? " selected" : "") + '>' + esc(t) + "</option>";
      }).join("");

    var behind = row.ticker_years
      ? row.n + " name-months, " + row.ticker_years.median + " years for the typical name"
      : row.n + " observation" + (row.n === 1 ? "" : "s") + " over " + row.years +
        " year" + (row.years === 1 ? "" : "s");
    var stat = (row.avg_pct >= 0 ? "+" : "") + num(row.avg_pct, 2) + "% average, " +
      (row.median_pct >= 0 ? "+" : "") + num(row.median_pct, 2) + "% median, up " +
      num(row.win_rate_pct, 0) + "% of the time — " + behind + ".";

    function pctCell(v) {
      return '<td class="r ' + (v >= 0 ? "up" : "down") + '">' + (v >= 0 ? "+" : "") + num(v, 2) + "</td>";
    }
    var byYear = row.by_year || [];
    var head = isAll ? "<tr><th>Year</th><th>Name</th><th class=\"r\">Return %</th></tr>"
                     : "<tr><th>Year</th><th class=\"r\">Return %</th></tr>";
    var body = byYear.length ? byYear.map(function (e) {
      return "<tr><td class=\"r faint\">" + e.year + "</td>" +
        (isAll ? "<td>" + esc(e.ticker) + "</td>" : "") + pctCell(e.pct) + "</tr>";
    }).join("") : '<tr><td colspan="' + (isAll ? 3 : 2) + '" class="empty">No years yet.</td></tr>';

    return '<div class="panelcard seasondetail">' +
      "<h3>" + esc(MONTH_NAMES[month - 1]) + " — " +
      esc(isAll ? "All " + d.seasonality.tickers + " names" : ticker) + "</h3>" +
      '<p class="faint" style="font-size:.8rem;margin:-4px 0 8px">' + esc(stat) + "</p>" +
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

    // A click on a month cell opens the year-by-year panel below the table —
    // one name or every name, picked from the <select> the panel carries.
    var detail = $("#seasondetail", host);
    var openCell = null;
    function showDetail(ticker, month) {
      detail.innerHTML = seasonDetail(d, ticker, month);
      detail.hidden = false;
      var pick = $(".seasondetail-pick", detail);
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
    if (BACKTEST_VIEWS.indexOf(view) === -1) view = "rules";
    btView = view;
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
    btSweepDraw();
    bsDraw();
    bmDraw();
    writeHash();
  }

  function wireBacktestViews() {
    var buttons = document.querySelectorAll("#btviews button");
    for (var i = 0; i < buttons.length; i++) {
      buttons[i].addEventListener("click", function () { showBacktestView(this.dataset.btview); });
    }
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
      '<td class="r' + (t.touched ? " touch" : "") + '">' + t.touched + " of " + t.decided +
        "</td>" +
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
        '<td class="r' + (r.touched ? " touch" : "") + '">' +
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
      '<td class="r' + (sum.touched ? " touch" : "") + '">' + num(pooledTouch, 0) + "%</td>" +
      '<td class="r faint" title="' + esc(medianNote) + '">—</td>' +
      '<td class="r faint" title="' + esc(medianNote) + '">—</td>' +
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
      esc(String(sum.decided)) + " separate ones.</p>";
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
      if (r[keys[i]]) have.push("<li>" + esc(r[keys[i]]) + "</li>");
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
      : String(v).split(".")[0] !== SCHEMA_MAJOR
        ? "It declares schema " + v + ", and this page reads " + SCHEMA_MAJOR + ".x."
        : "";
    if (!wrong) return "";
    return '<div class="notice"><b>This history is not the shape this page was written for.</b> ' +
      esc(wrong) + " Everything below is still computed from the closes in it, but which column " +
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
      (one ? "the rule " : "the rules ") + "<code>" + gone.map(esc).join("</code>, <code>") +
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

    rpSweepCache = { key: key, weeks: weeks, best: best, second: second,
                     median: middle, ranked: ranked.length };
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
    // The field the best week won against, in words. There is a button here
    // that adopts the winner in one click, directly under a warning that the
    // best of 53 tries is a high bar to clear by luck — and a warning with no
    // number in it loses that argument to a button every time. This is the
    // number: how far ahead of the runner-up, and of the middle week, the crown
    // actually sits.
    var field = sweep.best && sweep.second
      ? "runner-up week " + sweep.second.week + " at " + num(sweep.second.rate, 0) +
        "%, middle of the " + sweep.ranked + " rankable weeks " + num(sweep.median, 0) + "%"
      : "";
    var say = sweep.best
      ? "How often each buy week closed " + goal + ", on these settings. Best is week " +
        sweep.best.week + " at " + num(sweep.best.rate, 0) + "%" +
        (field ? ", against a " + field + "." : ".")
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
        '" title="' + esc("set the slider to week " + sweep.best.week +
          (field ? " — the week that won, against a " + field : "")) + '">' +
        "Best here: <b>week " + sweep.best.week + "</b>" + (when ? " · w/c " + esc(when) : "") +
        " · " + num(sweep.best.rate, 0) + "% (" + sweep.best.hit + " of " + sweep.best.decided +
        ")</button>";
    }

    host.innerHTML = '<div class="heatbar" role="img" aria-label="' + esc(say) + '">' + bars +
      "</div>" + pointer +
      '<span class="heatnote">' + (sweep.best
        ? esc("best of 53 weeks tried" + (field ? " · " + field : "") +
              " — and the best of 53 tries is a high bar to clear by luck")
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
    var single = spSingle();
    var lots = econ.lots > 1 ? " ×" + econ.lots : "";
    var body = econ.rows.map(function (r) {
      // The two ends a vertical can reach are named where they happen: "max"
      // and "expired worthless" read as outcomes where a bare number reads as
      // arithmetic. A single leg has no cap, so it is never "max".
      var note = r.maxed ? ' <span class="tag buy">max</span>'
        : r.worthless ? ' <span class="tag sell">worthless</span>' : "";
      return '<tr><td class="t">' + r.year + "</td>" +
        '<td class="r">' + money(r.entry) + "</td>" +
        '<td class="r">' + money(r.long) + (single ? "" : " / " + money(r.short)) + "</td>" +
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
      ? side + ", " + num(sp.long, 1) + "% strike, held " + rp.hold + " weeks"
      : side + " debit spread, " + num(sp.long, 1) + "% / " + num(sp.short, 1) + "%, held " +
        rp.hold + " weeks";
    var basis = single ? "entry price" : "width";
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
           + "cash is only at risk for " + rp.hold + " weeks of each year, and " +
           (single ? "a single leg" : "a debit vertical") + " can lose all of it.") +
      tile(econ.breakeven === null ? "fair" : sp.debit <= econ.breakeven ? "cheap" : "rich",
           "Breakeven debit — " + num(econ.breakeven, 0) + "% of " + basis,
           "Pay less than this and the run made money, more and it did not. This is the one "
           + "number here that does not rest on your assumption, so it is the one to take to a "
           + "live quote. You have set " + num(sp.debit, 0) + "%.") +
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
      var hint = econ.why.indexOf("short strike") >= 0
        ? " — the strike you sell is what caps the payout, so it has to sit further out than " +
          "the one you buy."
        : "";
      host.innerHTML = '<p class="empty">' + esc(econ.why) + hint + "</p>";
      return;
    }

    host.innerHTML = (econ.years ? spTiles(econ) : "") +
      "<h3>" + esc(rp.ticker) + ", year by year</h3>" +
      spYearTable(econ) + spAllTable(d) + spRules(d);

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

    host.innerHTML = rpStale(d) + short + rpTiles(t) +
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
    writeHash();
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
    var shortCtl = $("#sp-short-ctl");
    if (shortCtl) shortCtl.hidden = single;
    var label = $("#sp-debit-label");
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
      $(id).addEventListener("input", function () {
        sp[key] = spClamp(key, this.value);
        spStore();
        spDraw();
      });
    }
    onNum("#sp-long", "long");
    onNum("#sp-short", "short");
    onNum("#sp-debit", "debit");
    onNum("#sp-contracts", "contracts");
    $("#sp-on").addEventListener("change", function () {
      sp.on = this.checked;
      spStore();
      spDraw();
      if (sp.on) $("#spreadcontrols").scrollIntoView({ block: "nearest", behavior: "smooth" });
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
        if (!has(saved[key])) continue;
        sp[key] = key === "on" ? !!saved[key]
          : key === "structure" ? (saved[key] === "single" ? "single" : "spread")
          : spClamp(key, saved[key]);
      }
    }
    $("#sp-on").checked = !!sp.on;
    $("#sp-long").value = sp.long;
    $("#sp-short").value = sp.short;
    $("#sp-debit").value = sp.debit;
    $("#sp-contracts").value = sp.contracts;
    $("#spreadsection").hidden = !sp.on;
    spApplyStructure();
  }

  function renderRepeat() {
    load("weekly").then(function (d) {
      if (!rpAt) {
        rpAt = SpreadTrial.index(d);

        var select = $("#rp-ticker");
        select.innerHTML = d.series.map(function (s) {
          return '<option value="' + esc(s.ticker) + '">' + esc(s.ticker) + "</option>";
        }).join("");
        rpWeekLabel(d);
      }
      if (!d.series.length) {
        $("#repeatbody").innerHTML = '<p class="empty">The weekly history is empty — no name in ' +
          "this screen has the " + (d.min_weeks || 26) + " weeks the test needs.</p>";
        return;
      }
      // A remembered name that has since dropped out of the screen is not an
      // error; it just is not on this page any more. Checked on every render
      // rather than only the first, because a fragment can name a ticker before
      // the payload that would have vetted it has landed.
      var known = d.series.some(function (s) { return s.ticker === rp.ticker; });
      if (!known) rp.ticker = (d.series[0] || {}).ticker || "";
      $("#rp-ticker").value = rp.ticker;
      writeHash();
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
        writeHash();          // as on the Backtest tab: the name is a view
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
             target: 8, years: 10, overlap: false, ticker: "" };
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
    return (m > 0 ? "+" : m < 0 ? "−" : "") + num(Math.abs(m), 1) + "%";
  }

  function btSide() { return bt.dir === "up" ? " or better" : " or lower"; }

  // Points, not percent: the gap between two rates is a difference of
  // percentages and calling it a percentage is how "6 points better" becomes
  // "6% better", which is a different and much smaller claim.
  function points(v, digits) {
    if (!has(v) || isNaN(v)) return "—";
    var d = digits === undefined ? 1 : digits;
    var shown = Math.abs(Number(v)).toFixed(d);
    return (v >= 0 ? "+" : "−") + num(Math.abs(v), d) +
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
      tile("fair", "The rule — " + (res.trades ? num(res.rate, 0) + "% of " + num(res.trades, 0) +
             " trade" + (res.trades === 1 ? "" : "s") : "no finished trade"),
           "Trades that closed " + goal + " at the exit. The rule fired " + num(res.fired, 0) +
           " time" + (res.fired === 1 ? "" : "s") + " and " +
           (bt.overlap ? "every firing was counted" : "took " + num(res.taken, 0) +
             " of them one at a time") + ".") +
      tile("fair", "Every week — " + (base.trades ? num(base.rate, 0) + "% of " +
             num(base.trades, 0) : "nothing to compare"),
           "The same names, hold and target, entered on every week of the same history. Not a "
           + "strategy — a description of what these weeks did in general, which is what the "
           + "rule has to beat to have said anything.") +
      tile("fair", "Median exit — " + signed(res.median_exit) +
             " against " + signed(base.median_exit),
           "Where the typical trade closed, against where the typical week closed. Two rules can "
           + "clear the line equally often and be nothing alike; this is what separates them. "
           + (gap.worst === null ? "" : "The typical worst point inside the window was " +
              signed(res.median_worst) + ", against " + signed(base.median_worst) + ".")) +
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
      esc(points(gap.rate, 0)) + " above is not a finding. It is the <b>noise floor</b> on these "
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
        num(res.rate, 0) + "% of its trades closed " + btGoal() + btSide() + ", against " +
        num(base.rate, 0) + "% of all weeks — " + points(gap.rate, 0) + "."
      : gap.rate === 0
        ? "On this history the rule picked weeks that were indistinguishable from weeks in "
          + "general: both closed " + btGoal() + btSide() + " " + num(base.rate, 0) +
          "% of the time."
        : "On this history the rule picked worse weeks than the average week: " +
          num(res.rate, 0) + "% against " + num(base.rate, 0) + "% — " + points(gap.rate, 0) +
          ". Going the other way on the direction chip is not the fix it looks like; "
          + "a rule that is wrong is rarely exactly inverted.";
    var caution = thin
      ? " That rests on " + num(res.trades, 0) + " finished trade" +
        (res.trades === 1 ? "" : "s") + ", which is too few to separate a rule from a run of luck."
      : " And these names move together, so the trades behind it are not as independent as their "
        + "number looks.";
    return '<div class="verdict ' + (better && !thin ? "good" : "bad") + '">' + esc(said) +
      esc(caution) + "</div>";
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
      esc(bits.join(" · ")) + ".</p>";
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
               base: has(mine.rate) ? mine.rate : null, edge: gap.rate,
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
      x = has(x) ? x : -Infinity; y = has(y) ? y : -Infinity;
      return (x === y ? a.trades - b2.trades : x - y) * btSort.dir;
    });

    function th(k, label, cls) {
      return sortableTh(k, label, cls || "",
        btSort.key === k ? (btSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    var body = rows.map(function (r) {
      return '<tr class="srow' + (r.thin ? " thin" : "") +
        (r.ticker === bt.ticker ? " picked" : "") + '" data-ticker="' + esc(r.ticker) +
        '" tabindex="0" title="' + esc(r.thin
          ? r.ticker + " has only " + r.trades + " finished trade" + (r.trades === 1 ? "" : "s") +
            " here — shown, but not ranked"
          : "show " + r.ticker + "'s trades below") + '">' +
        '<td class="t">' + esc(r.ticker) + "</td>" +
        '<td class="r">' + num(r.trades, 0) + "</td>" +
        '<td class="r' + (r.hit ? " hit" : "") + '">' + num(r.hit, 0) + "</td>" +
        '<td class="r ' + (r.rate >= 50 ? "hit" : "miss") + '"><b>' + num(r.rate, 0) + "%</b></td>" +
        '<td class="r faint">' + (has(r.base) ? num(r.base, 0) + "%" : "—") + "</td>" +
        '<td class="r ' + (!has(r.edge) ? "" : r.edge > 0 ? "hit" : r.edge < 0 ? "miss" : "") +
          '">' + points(r.edge, 0) + "</td>" +
        '<td class="r soft-hit">' + signed(r.exit) + "</td>" +
        '<td class="r soft-miss">' + signed(r.worst) + "</td></tr>";
    }).join("");

    var gap = SpreadBacktest.edge(res, base);
    var foot = "<tfoot><tr>" +
      '<td class="t">' + rows.length + " name" + (rows.length === 1 ? "" : "s") + "</td>" +
      '<td class="r">' + num(res.trades, 0) + "</td>" +
      '<td class="r' + (res.hit ? " hit" : "") + '">' + num(res.hit, 0) + "</td>" +
      '<td class="r ' + (res.rate >= 50 ? "hit" : "miss") + '">' + num(res.rate, 0) + "%</td>" +
      '<td class="r faint">' + num(base.rate, 0) + "%</td>" +
      '<td class="r ' + (!has(gap.rate) ? "" : gap.rate > 0 ? "hit" : gap.rate < 0 ? "miss" : "") +
        '">' + points(gap.rate, 0) + "</td>" +
      '<td class="r faint" title="' + esc("no total: a median of medians is not a median — the " +
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
      return "<h2>" + esc(bt.ticker) + " — every trade</h2>" +
        '<p class="empty">This rule never fired on ' + esc(bt.ticker) +
        " over that stretch of history.</p>";
    }
    var labels = { hit: "Closed past", miss: "Fell short", open: "Still open", skipped: "Skipped" };
    var shown = one.rows.slice().reverse().slice(0, BT_MAX_ROWS);
    var body = shown.map(function (r) {
      if (r.state === "skipped") {
        return '<tr class="dim"><td class="t">' + esc(r.week) + "</td><td>" + esc(r.start) +
          '</td><td colspan="5" class="faint">' + esc(r.why || "not enough history") +
          '</td><td class="skipped">Skipped</td></tr>';
      }
      var exit = r.settled
        ? '<td class="r ' + (r.closed_past ? "hit" : "miss") + '">' + signed(r.exit_pct) + "</td>"
        : '<td class="r faint">running</td>';
      // Touched keeps its own colour, off the buy/sell axis, for the reason it
      // does on the Repeat test: a trade that touched and then closed back is
      // red at the exit and still blue here, and those are two different claims.
      var touched = '<td class="r' + (r.touched ? " touch" : "") + '">' +
        (r.touched ? "week " + r.hit_in : "—") + "</td>";
      var verdict = r.state === "open"
        ? (r.touched ? "Touched, still open" : "Still open") + " (" + r.ran + "/" + bt.hold + "w)"
        : labels[r.state];
      return '<tr><td class="t">' + esc(r.week) + "</td><td>" + esc(r.start) + "</td>" +
        '<td class="r">' + money(r.entry) + "</td>" +
        '<td class="r">' + money(r.target) + "</td>" + exit +
        '<td class="r">' + signed(r.best_pct) + "</td>" + touched +
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
      '<td class="r">' + signed(one.median_best) + "</td>" +
      '<td class="r' + (one.touched ? " touch" : "") + '">' + one.touched + " of " + one.trades +
        "</td>" +
      "<td>" + num(one.rate, 0) + "% closed · " + num(one.touch_rate, 0) + "% touched</td>" +
      "</tr></tfoot>";

    return "<h2>" + esc(bt.ticker) + " — every trade this rule took</h2>" +
      '<div class="tablewrap"><table class="scan trial"><thead><tr>' +
      "<th>Signal week</th><th>Entered</th><th class=\"r\">Entry</th><th class=\"r\">Target</th>" +
      '<th class="r">At exit</th><th class="r">Best</th><th class="r">Touched</th>' +
      "<th>Result</th></tr></thead><tbody>" + body + "</tbody>" + foot + "</table></div>" + more;
  }

  /* What this test does, and — separately — what it is not.

     The rules it does are backtest.js's own and ship with it. The one fact
     about the *data* comes off weekly.json, where the backend owns it. */
  function btCaveats(d) {
    var notes = SpreadBacktest.NOTES.map(function (n) { return "<li>" + esc(n) + "</li>"; });
    var prices = ((d && d.reference) || {}).prices;
    if (prices) notes.push("<li>" + esc(prices) + "</li>");
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
      esc(said.label) + (said.alsoLabel ? " + " + esc(said.alsoLabel) : "") + ".</b> " +
      esc(said.rule) +
      (said.also ? " <b>And only where</b> " + esc(said.also.charAt(0).toLowerCase() +
        said.also.slice(1)) : "") +
      " Held " + bt.hold + " week" + (bt.hold === 1 ? "" : "s") + ", counted as a hit at " +
      esc(btGoal() + btSide()) + " on the closing week, over the last " + bt.years +
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
    var mags = cells.filter(function (c) { return has(c.edge) && !isNaN(c.edge); })
                    .map(function (c) { return Math.abs(c.edge); })
                    .sort(function (a, b) { return a - b; });
    if (!mags.length) return 1;
    return Math.max(1, mags[Math.floor(mags.length * 0.9)] || mags[mags.length - 1]);
  }

  function btWeeks(n) { return n + "w"; }

  function btSweepCell(c, scale) {
    var say = btWeeks(c.look) + " lookback held " + btWeeks(c.hold) + ": " +
      (has(c.rate) ? num(c.rate, 0) + "% of " + num(c.trades, 0) + " trades closed " +
        btGoal() + btSide() + ", against " + num(c.base_rate, 0) + "% for every week — " +
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
      ' style="' + heatStyle(c.thin ? null : c.edge, scale) + '"' +
      ' title="' + esc(say) + '" aria-label="' + esc(say) + '">' +
      (has(c.edge) ? points(c.edge, 0).replace(" pts", "").replace(" pt", "") : "—") +
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
          btWeeks(sw.best.hold) + ", " + esc(points(sw.best.edge, 0)) + "</span>" +
        '<div class="v">' + num(sw.best.rate, 0) + "% of " + num(sw.best.trades, 0) +
          " trades closed " + esc(btGoal() + btSide()) + ", against " +
          num(sw.best.base_rate, 0) + "% for weeks in general. " +
          '<button class="bestweek" data-look="' + sw.best.look + '" data-hold="' +
          sw.best.hold + '">Use these settings</button></div></div>' +
      '<div class="rule fair"><span class="k">Runner-up — ' +
        (sw.runner ? esc(points(sw.runner.edge, 0)) + " at " + btWeeks(sw.runner.look) +
          " / " + btWeeks(sw.runner.hold) : "none") + "</span>" +
        '<div class="v">' + (spread === null ? "Only one cell could be ranked."
          : "The winner is " + esc(points(spread, 1)) + " clear of it. A cell well clear of "
            + "the field is a ridge; a cell a fraction clear is the same crown and much "
            + "weaker evidence.") + "</div></div>" +
      '<div class="rule ' + (sw.middle > 0 ? "cheap" : "rich") + '">' +
        '<span class="k">The middle cell — ' + esc(points(sw.middle, 0)) + "</span>" +
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
    $("#bt-look").value = bt.look;
    $("#bt-hold").value = bt.hold;
    btStore();
    btDraw();
    $("#btcontrols").scrollIntoView({ block: "nearest" });
  }

  function btSweepDraw() {
    var host = $("#sweepbody");
    if (btView !== "sweep" || !store.weekly) { host.innerHTML = ""; return; }
    if (!store.weekly.series.length) { host.innerHTML = ""; return; }
    host.innerHTML = btSweepBody(store.weekly);

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
    var d = store.weekly, host = $("#backtestbody");
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

    host.innerHTML = rpStale(d) + btRuleSaid() + btTiles(res, base, gap) + btPending(res) +
      btVerdict(res, base, gap) + btNameTable(res, base) + btTradeTable(res) + btCaveats(d);

    wireSort(host.querySelectorAll("table.scan.rank thead th"), function (k) {
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
    var lots = econ.lots === 1 ? "one contract" : num(econ.lots, 0) + " contracts";
    var won = econ.years ? num((econ.won / econ.years) * 100, 0) + "%" : "—";
    return '<div class="rulebar">' +
      tile(econ.net > 0 ? "cheap" : econ.net < 0 ? "rich" : "fair",
           "Net — " + cash(econ.net),
           num(econ.years, 0) + " finished trade" + (econ.years === 1 ? "" : "s") + " at " +
           lots + " each: " + cash(econ.paid) + " paid in, " + cash(econ.received) +
           " back. Every one of them is in the table below.") +
      tile("fair", "Return on what you staked — " + (has(econ.roi) ? signed(econ.roi, 0) : "—"),
           "The net over the total debit. It is not an annual figure and it is not " +
           "compounded — the trades overlap or they do not depending on the setting above, " +
           "so there is no one account this could have been run in.") +
      // The sentence under this one has to agree with the number above it. A
      // generic warning that a debit structure usually loses, printed beside a
      // 7-of-7 record, reads as a page not looking at its own output.
      tile("fair", "Won " + econ.won + " of " + num(econ.years, 0) + " — " + won,
           (econ.maxed ? num(econ.maxed, 0) + " reached the full width; " : "") +
           num(econ.worthless, 0) + " expired worthless. " +
           (econ.years && econ.won / econ.years >= 0.5
             ? "Winning this often is not the structure being safe — it is " + esc(bt.ticker) +
               " over this stretch, on " + num(econ.years, 0) + " trade" +
               (econ.years === 1 ? "" : "s") + ". The net and the breakeven are what to read."
             : "A debit structure loses its whole cost more often than it wins, which is why " +
               "the net matters and the hit rate does not.")) +
      tile(has(econ.breakeven) && econ.breakeven >= bm.debit ? "cheap" : "rich",
           "Breakeven debit — " + (has(econ.breakeven) ? num(econ.breakeven, 1) + "%" : "—"),
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
      return "<tr><td class=\"t\">" + esc(String(r.when)) + "</td>" +
        '<td class="r">' + money(r.entry) + "</td>" +
        '<td class="r">' + money(r.long) + "</td>" +
        '<td class="r">' + (r.short === null ? "—" : money(r.short)) + "</td>" +
        '<td class="r">' + money(r.exit) + "</td>" +
        '<td class="r' + (r.maxed ? " maxed" : r.worthless ? " zero" : "") + '">' +
          money(r.worth) + "</td>" +
        '<td class="r out">' + cash(r.paid) + "</td>" +
        '<td class="r">' + cash(r.received) + "</td>" +
        '<td class="r net ' + net + '">' + cash(r.net) + "</td></tr>";
    }).join("");
    var more = econ.rows.length > BM_MAX_ROWS
      ? '<p class="faint" style="font-size:.83rem;margin:8px 0 0">The ' + BM_MAX_ROWS +
        " most recent of " + econ.rows.length + " — the totals above are over all of them.</p>"
      : "";
    var foot = "<tfoot><tr><td class=\"t\">" + num(econ.years, 0) + " trades</td>" +
      "<td></td><td></td><td></td><td></td><td></td>" +
      '<td class="r out">' + cash(econ.paid) + "</td>" +
      '<td class="r">' + cash(econ.received) + "</td>" +
      '<td class="r net ' + (econ.net > 0 ? "up" : econ.net < 0 ? "down" : "") + '">' +
        cash(econ.net) + "</td></tr></tfoot>";
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
    var shortCtl = $("#bm-short-ctl");
    if (shortCtl) shortCtl.hidden = single;
    var label = $("#bm-debit-label");
    if (label) {
      label.textContent = single ? "Premium paid, % of entry price" : "Debit paid, % of width";
    }
  }

  /* The trades this prices are the ones on screen: the same rule, the same
     name, the same settings. Deliberately the picked name rather than every
     name pooled — a dollar total across twenty-nine names is a portfolio
     nobody ran, sized by nothing. */
  function bmDraw() {
    var host = $("#bmbody");
    if (btView !== "money" || !store.weekly || !btLast) { if (host) host.innerHTML = ""; return; }

    var one = null;
    for (var i = 0; i < btLast.names.length; i++) {
      if (btLast.names[i].ticker === bt.ticker) { one = btLast.names[i]; break; }
    }
    if (!one || !one.trades) {
      host.innerHTML = '<p class="empty">' + esc(bt.ticker) +
        " has no finished trade on these settings, so there is nothing to price.</p>";
      return;
    }

    var econ = SpreadTrial.economics(one, bmDeal());
    if (econ.why) {
      host.innerHTML = '<div class="notice">Nothing to price — ' + esc(econ.why) + ".</div>";
      return;
    }
    host.innerHTML = "<h2>" + esc(bt.ticker) + " — priced as " +
      (bmSingle() ? "a single option" : "a debit spread") + "</h2>" +
      '<p class="dim" style="font-size:.87rem;margin:0 0 10px">Every finished trade the ' +
      "rule took on " + esc(bt.ticker) + ", each priced off its own entry — strikes are " +
      "percentages of it, for the same reason the target is. Pick another name in the " +
      "ranking above to price that one instead.</p>" +
      bmTiles(econ) + bmTable(econ);
  }

  function wireMoney() {
    function onNum(id, key) {
      $(id).addEventListener("input", function () {
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
        if (!has(saved[key])) continue;
        bm[key] = key === "structure" ? (saved[key] === "single" ? "single" : "spread")
          : bmClamp(key, saved[key]);
      }
    }
    $("#bm-long").value = bm.long;
    $("#bm-short").value = bm.short;
    $("#bm-debit").value = bm.debit;
    $("#bm-contracts").value = bm.contracts;
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
    return esc((r.label || c.rule).split(" — ")[0]) + " " + c.look + "w, held " + c.hold +
      "w, " + (c.dir === "down" ? "down ↓" : "up ↑");
  }

  /* The verdict, and it is not the crown.
   *
   * A search that works picks settings that go on beating their own baseline
   * far more often than a coin would. One that is fitting noise picks settings
   * that hold up about half the time, with a large in-sample edge and nothing
   * left out of sample — and prints exactly that. */
  function bsVerdict(all) {
    function tile(cls, k, v) {
      return '<div class="rule ' + cls + '"><span class="k">' + k + '</span><div class="v">' + v +
        "</div></div>";
    }
    var pct = all.held_pct;
    // Better than a coin by a margin worth the name. Under that, the search is
    // an expensive way to generate noise and is told to say so.
    var works = has(pct) && pct >= 65;
    var decay = has(all.median_train) && has(all.median_test)
      ? all.median_train - all.median_test : null;

    return '<div class="rulebar">' +
      tile(works ? "cheap" : "rich",
           "Held up out of sample — " + all.held + " of " + all.rated +
             (has(pct) ? " (" + num(pct, 0) + "%)" : ""),
           "The settings the search crowned on the older history, then measured on the " +
           "years it never saw: this many both <b>made money</b> and <b>beat simply holding " +
           "the name</b> over the same weeks. Either test alone is cheap — a short that loses " +
           "less than other shorts passes the first, and any long on a name that rose passes " +
           "the second. " +
           (works
             ? "Better than a coin by enough to be worth something — but read the decay beside "
               + "it before believing any single row."
             : "<b>That is about what a coin would do.</b> Picking a strategy per name, on this "
               + "much history, mostly finds what already happened rather than what is going to.")) +
      tile("rich", "Against simply holding — " + points(all.median_train, 0) + " → " +
             points(all.median_test, 0),
           "The yardstick is the alternative anyone actually had: buying the name and keeping " +
           "it for the same number of weeks. This is how many percentage points of return the " +
           "median pick beat that by, while it was being searched and afterwards. " +
           (has(decay) && decay > 0
             ? "<b>" + esc(points(decay, 0)) + " of it was not there once the holdout started.</b> "
             : "") +
           "That gap is the cost of searching: most of what a search finds is the search.") +
      tile("fair", "What they returned — " + signed(all.median_return),
           "The median pick's own return out of sample, before any comparison. A strategy has " +
           "to clear two different bars and they are not the same one: this number says it made " +
           "money, the one beside it says it was worth the trouble. A rule can do either without " +
           "the other.") +
      tile("fair", "Best that was available — " + points(all.median_hindsight, 0),
           "The best edge the holdout actually contained, per name — what you would have " +
           "picked knowing the answer. There <i>were</i> edges out there; the search just " +
           "could not tell in advance which. The distance from the middle number to this one " +
           "is the part it missed.") +
      tile("fair", "Searched — " + num(all.tried, 0) + " per name",
           "Every rule at every lookback, hold and direction, on " + esc(all.names.length) +
           " names. Split at " + esc(all.train_to || "—") + ": searched on " +
           esc(all.train_from || "—") + "–" + esc(all.train_to || "—") + ", reported on " +
           esc(all.test_from || "—") + "–" + esc(all.test_to || "—") + ". A combination needs " +
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
      return sortableTh(k, label, cls || "",
        bsSort.key === k ? (bsSort.dir === 1 ? "ascending" : "descending") : "none");
    }
    var body = rows.map(function (n) {
      var p = n.pick;
      return '<tr class="srow" data-ticker="' + esc(n.ticker) + '" data-rule="' + esc(p.rule) +
        '" data-look="' + p.look + '" data-hold="' + p.hold + '" data-dir="' + esc(p.dir) +
        '" tabindex="0" title="' + esc("put " + n.ticker + "'s pick into the controls above") +
        '">' +
        '<td class="t">' + esc(n.ticker) + "</td>" +
        "<td>" + bsCombo(p) + "</td>" +
        '<td class="r soft-hit">' + points(p.train.ret, 0) + "</td>" +
        '<td class="r ' + (p.test.ret > 0 ? "hit" : "miss") + '"><b>' +
          points(p.test.ret, 0) + "</b></td>" +
        '<td class="r ' + (p.test.exit > 0 ? "hit" : "miss") + '">' +
          signed(p.test.exit) + "</td>" +
        '<td class="r faint">' + points(n.hindsight.test.ret, 0) + "</td>" +
        '<td class="r">' + num(p.test.trades, 0) + "</td>" +
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
      "strategy for that name. It is one combination out of " + num(all.tried, 0) + " that " +
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
    $("#bt-ticker").value = bt.ticker;
    $("#bt-rule").value = bt.rule;
    $("#bt-look").value = bt.look;
    $("#bt-hold").value = bt.hold;
    var dirs = document.querySelectorAll("#bt-dir button");
    for (var i = 0; i < dirs.length; i++) {
      dirs[i].setAttribute("aria-pressed", dirs[i].dataset.dir === bt.dir ? "true" : "false");
    }
    btStore();
    writeHash();
    btApplyRule();
    btDraw();
    $("#btcontrols").scrollIntoView({ block: "nearest" });
  }

  function bsDraw() {
    var host = $("#bsbody");
    if (btView !== "search" || !store.weekly) { if (host) host.innerHTML = ""; return; }
    if (!store.weekly.series.length) { host.innerHTML = ""; return; }

    var all = bsRun(store.weekly);
    host.innerHTML =
      '<div class="lede">Every rule, at every lookback, hold and direction — ' +
      num(all.tried, 0) + " combinations for each of " + all.names.length + " names — " +
      "searched on the <b>older</b> part of the history. What is reported is what each " +
      "winner then did on the <b>rest</b>, which the search never saw. That split is the " +
      "whole point: a search always finds a winner, and the only question worth asking is " +
      "whether the winner was still one afterwards. It is the same train/holdout split " +
      "<code>calibrate.py</code> fits the Setup Score's weights on.</div>" +
      bsVerdict(all) + bsTable(all);

    wireSort(host.querySelectorAll("table.scan.rank thead th"), function (k) {
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
    $("#bt-ticker").value = ticker;
    btStore();
    writeHash();
    btDraw();
  }

  // The lookback means something different for every rule, and for the baseline
  // it means nothing at all — so the label says which, and the control goes away
  // where it would be answering a question nobody asked.
  function btApplyRule() {
    var r = SpreadBacktest.RULES[bt.rule] || {};
    var ctl = $("#bt-look-ctl");
    if (ctl) ctl.hidden = !r.look;
    var label = $("#bt-look-label");
    if (label && r.look) {
      label.textContent = r.look.charAt(0).toUpperCase() + r.look.slice(1) + ", weeks";
    }
    // The second rule's lookback is its own, and named for what it means to
    // *that* rule — the whole reason the two are not one control.
    var also = btFiltered() ? SpreadBacktest.RULES[bt.also] : null;
    var alsoCtl = $("#bt-alsolook-ctl");
    if (alsoCtl) alsoCtl.hidden = !(also && also.look);
    var alsoLabel = $("#bt-alsolook-label");
    if (alsoLabel && also && also.look) {
      alsoLabel.textContent = also.look.charAt(0).toUpperCase() + also.look.slice(1) +
        ", weeks";
    }
    var note = $("#bt-overlap-note");
    if (note) {
      note.textContent = bt.overlap
        ? "On — every firing counted, including windows that overlap each other. The right "
          + "reading for a survey, the wrong one for a plan."
        : "Off, one position at a time — what you could actually have held.";
    }
  }

  function renderBacktest() {
    load("weekly").then(function (d) {
      var select = $("#bt-ticker");
      if (!select.options.length) {
        select.innerHTML = d.series.map(function (s) {
          return '<option value="' + esc(s.ticker) + '">' + esc(s.ticker) + "</option>";
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
      writeHash();
      btDraw();
    }).catch(function (e) {
      $("#backtestbody").innerHTML = loadError(e, "weekly", "python run.py");
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
        writeHash();
        if (store.weekly) btDraw();
      };
    }
    $("#bt-rule").addEventListener("change", onChange(function (el) {
      bt.rule = SpreadBacktest.RULES[el.value] ? el.value : "every";
    }));
    $("#bt-ticker").addEventListener("change", onChange(function (el) { bt.ticker = el.value; }));
    $("#bt-look").addEventListener("input", onChange(function (el) {
      bt.look = btClamp("look", el.value);
    }));
    $("#bt-also").addEventListener("change", onChange(function (el) {
      bt.also = SpreadBacktest.RULES[el.value] ? el.value : "every";
    }));
    $("#bt-alsolook").addEventListener("input", onChange(function (el) {
      bt.alsoLook = btClamp("alsoLook", el.value);
    }));
    $("#bt-hold").addEventListener("input", onChange(function (el) {
      bt.hold = btClamp("hold", el.value);
    }));
    $("#bt-years").addEventListener("input", onChange(function (el) {
      bt.years = btClamp("years", el.value);
    }));
    $("#bt-target").addEventListener("input", onChange(function (el) {
      bt.target = btClamp("target", el.value);
    }));
    $("#bt-overlap").addEventListener("change", onChange(function (el) {
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
        if (store.weekly) btDraw();
      });
    }

    // The rule list is built from backtest.js, so the labels cannot drift from
    // the rules they name.
    $("#bt-rule").innerHTML = SpreadBacktest.RULE_KEYS.map(function (k) {
      return '<option value="' + esc(k) + '">' + esc(SpreadBacktest.RULES[k].label) + "</option>";
    }).join("");
    // The second list is the same rules, with "every" reading as the no-op it
    // is: a filter that passes every week is no filter.
    $("#bt-also").innerHTML = SpreadBacktest.RULE_KEYS.map(function (k) {
      return '<option value="' + esc(k) + '">' +
        (k === "every" ? "— nothing" : esc(SpreadBacktest.RULES[k].label)) + "</option>";
    }).join("");

    // Whatever was set last time, clamped on the way in: localStorage is input,
    // not state, and it is treated as input.
    var saved = null;
    try { saved = JSON.parse(localStorage.getItem("backtest") || "null"); } catch (e) { saved = null; }
    if (saved) {
      for (var key in bt) {
        if (!has(saved[key])) continue;
        bt[key] = key === "overlap" ? !!saved[key]
          : (key === "rule" || key === "also")
            ? (SpreadBacktest.RULES[saved[key]] ? saved[key] : bt[key])
          : key === "dir" ? (saved[key] === "down" ? "down" : "up")
          : key === "ticker" ? String(saved[key])
          : btClamp(key, saved[key]);
      }
    }
    $("#bt-rule").value = bt.rule;
    $("#bt-look").value = bt.look;
    $("#bt-also").value = bt.also;
    $("#bt-alsolook").value = bt.alsoLook;
    $("#bt-hold").value = bt.hold;
    $("#bt-target").value = bt.target;
    $("#bt-years").value = bt.years;
    $("#bt-overlap").checked = bt.overlap;
    for (var k = 0; k < dirs.length; k++) {
      dirs[k].setAttribute("aria-pressed", dirs[k].dataset.dir === bt.dir ? "true" : "false");
    }
    btApplyRule();
  }

  // ----------------------------------------------------------- validation


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
      if (!d.ok) { $("#score-backtest").innerHTML = '<p class="empty">' + esc(d.note || "No backtest yet.") + "</p>"; return; }
      var b = d.buckets, s = d.squeeze;
      $("#score-backtest").innerHTML =
        '<p class="dim" style="font-size:.85rem">' + esc(d.universe) + " tickers · " + esc(d.history_years) +
        "y history · horizon " + esc(d.horizon_days) + " trading days · " + num(d.bars, 0) + " signal-bars</p>" +
        '<div class="panelcard"><p>' + esc(d.explainer) + "</p></div>" +
        "<h3 style=\"margin-top:18px\">By Setup Score</h3>" +
        statsTable([b.high, b.mid, b.low], "Score bucket", d.long_band_days) +
        '<div class="verdict ' + (d.verdict.holds ? "good" : "bad") + '">' + esc(d.verdict.text) + "</div>" +
        (d.verdict.long_band_text
          ? '<div class="verdict ' + (d.independent && d.independent.long_band &&
              d.independent.long_band.ci95_pts[0] > 0 ? "good" : "bad") + '">' +
            esc(d.verdict.long_band_text) + "</div>"
          : "") +
        (d.independent
          ? '<p class="faint" style="font-size:.82rem">Verdicts use ' + num(d.independent.bars, 0) +
            " non-overlapping bars (one every " + esc(d.independent.step_days) +
            " trading days per name), with 95% intervals from resampling whole dates.</p>"
          : "") +
        "<h3>Squeeze on vs off</h3>" + statsTable([s.on, s.off], "State", d.long_band_days) +
        "<h3>Expected-move calibration</h3>" +
        "<p>Realized moves landed inside the ±1σ band <b>" + pct(d.coverage_pct, 0) +
        "</b> of the time against a theoretical 68%. " +
        (d.coverage_ok ? "The bands are well calibrated." : "The bands look mis-calibrated — consider tuning <code>vol_lookback</code>.") +
        "</p>" + impliedSection(d.implied) +
        "<p class=\"faint\" style=\"font-size:.82rem\">" + esc(d.caveat) + "</p>";
    }).catch(function (e) {
      $("#score-backtest").innerHTML = loadError(e, "backtest", "python backtest.py --years 5");
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
      var quint = d.separation_basis === "quintile";
      $("#calibration").innerHTML = calibrationMismatch(d) +
        '<div class="panelcard"><p>' + esc(d.method) + "</p></div>" +
        '<table class="stats" style="margin-top:14px"><thead><tr><th>Weights (from the train split)</th>' +
        (quint
          ? '<th class="r">top 20% of score</th><th class="r">bottom 20%</th>'
          : '<th class="r">score ≥ 60</th><th class="r">score &lt; 30</th>') +
        '<th class="r">separation</th>' +
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
      pop: "Probability of profit", fill: "Fill price", credit_to_width: "Credit to width",
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
    wireBacktest();
    wireMoney();
    wireBacktestViews();
    // A fragment is an explicit request, so it outranks the last visit's tab.
    var linked = parseHash();
    var savedView = null, savedBtView = null;
    try { savedView = localStorage.getItem("chartview"); } catch (e) { savedView = null; }
    try { savedBtView = localStorage.getItem("backtestview"); } catch (e) { savedBtView = null; }
    // Nothing is on screen yet — the showTab below writes both halves at once.
    hashLock = true;
    // A view in the fragment belongs to whichever tab owns that name, so it is
    // only allowed to set the view of the tab it came with. Otherwise the last
    // visit's view stands.
    showChartView((linked && linked.tab === "charts" && linked.view) || savedView);
    showBacktestView((linked && linked.tab === "backtest" && linked.view) || savedBtView);
    // And the name the fragment asked for, before anything renders. A cold load
    // does not go through applyHash — that listens for hashchange, and arriving
    // at a URL is not a change — so a deep link into one name was parsed here
    // and then quietly dropped in favour of whatever was remembered.
    if (linked) applyName(linked.tab, linked.ticker);
    hashLock = false;

    window.addEventListener("hashchange", applyHash);

    load("scan").then(function (d) {
      if (!d || !d.schema_version) throw new Error("scan.json is missing or malformed");
      if (d.schema_version.split(".")[0] !== SCHEMA_MAJOR) {
        $("#schema-warning").hidden = false;
        $("#schema-warning").textContent =
          "This page expects scan schema " + SCHEMA_MAJOR + ".x but the data says " +
          d.schema_version +
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
