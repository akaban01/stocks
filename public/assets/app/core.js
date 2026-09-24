/* Spread Scanner — frontend.
 *
 * The backend writes JSON and nothing else; everything you see is rendered
 * here from data/scan.json, data/charts.json, data/backtest.json and
 * data/calibration.json.
 *
 * The trading copy — action labels, premium-state rules, the strategy playbook,
 * the glossary — is NOT hardcoded below. It ships inside scan.json under
 * `reference`, so an explanation can never drift from the field it explains.
 *
 * It is split across public/assets/app/*.js, one file per part of the page, loaded
 * in order by index.html. Names more than one file uses live on one shared object,
 * window.SpreadApp, read as `App.name`; everything else stays private to its file.
 */
(function (App) {
  "use strict";

  // Shared with the other frontend files (function declarations are hoisted).
  App.$ = $;
  App.tone = tone;
  App.ref = ref;
  App.sortableTh = sortableTh;
  App.wireSort = wireSort;
  App.theme = theme;
  App.themeRgb = themeRgb;
  App.localTime = localTime;
  App.load = load;
  App.loadError = loadError;
  App.parseHash = parseHash;
  App.writeHash = writeHash;
  App.applyName = applyName;
  App.applyHash = applyHash;
  App.showTab = showTab;


  // Pure data -> HTML string helpers live in render.js, so the node tests can
  // run the exact code that escapes what the payload says (see
  // tests/test_render_js.py). Aliased here to keep every call site unchanged.
  var R = window.SpreadRender;
  var esc = R.esc; App.esc = esc;
  var has = R.has; App.has = has;
  var num = R.num; App.num = num;
  var money = R.money; App.money = money;
  var cash = R.cash; App.cash = cash;
  var pct = R.pct; App.pct = pct;
  var multiExpiry = R.multiExpiry; App.multiExpiry = multiExpiry;
  var sizeCell = R.sizeCell; App.sizeCell = sizeCell;
  var legsTable = R.legsTable; App.legsTable = legsTable;
  var noteList = R.noteList; App.noteList = noteList;
  var manageBlock = R.manageBlock; App.manageBlock = manageBlock;
  var altBlock = R.altBlock; App.altBlock = altBlock;
  var riskFormNote = R.riskFormNote; App.riskFormNote = riskFormNote;
  var statsTable = R.statsTable; App.statsTable = statsTable;
  var impliedSection = R.impliedSection; App.impliedSection = impliedSection;
  var directionSection = R.directionSection; App.directionSection = directionSection;

  var DATA_DIR = "data/";
  // The payload shape this page was written against. Every file the backend
  // writes carries the same `schema_version`, so one constant checks them all —
  // and having it in one place is the point: scan.json was the only one being
  // checked, which is how weekly.json came to be read by a page that had no way
  // of noticing it was reading a stale one.
  var SCHEMA_MAJOR = "2"; App.SCHEMA_MAJOR = SCHEMA_MAJOR;
  var store = { scan: null, charts: null, weekly: null, backtest: null, calibration: null }; App.store = store;
  var filters = { actions: new Set(), query: "" }; App.filters = filters;

  // ------------------------------------------------------------- utilities

  function $(sel, root) { return (root || document).querySelector(sel); }

  // The actions that mean "there is a trade here" — as opposed to standing
  // aside or having no chain to read. The headline counts these and the sizing
  // tally filters by them, so they are named once rather than twice.
  var ACTIONABLE = { BUY_PREMIUM: true, SELL_PREMIUM: true, NEUTRAL_INCOME: true }; App.ACTIONABLE = ACTIONABLE;

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
  var CHART_VIEWS = ["prices", "seasonality"]; App.CHART_VIEWS = CHART_VIEWS;
  /* The Backtest tab's four views. Same idea as the Charts tab's, and they
     share the fragment's view segment — the lists must not collide, because a
     segment is matched against both and the tab it implies is whichever list
     claimed it. */
  var BACKTEST_VIEWS = ["rules", "sweep", "search", "money"]; App.BACKTEST_VIEWS = BACKTEST_VIEWS;
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
  App.curView = "prices";
  App.btView = "rules";
  // Set while the page is putting itself into a state it was *handed* — during
  // boot, and while applying an incoming fragment — so that those moves do not
  // write the fragment back over the one they are reading.
  App.hashLock = false;

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
    return tab === "repeat" ? App.rp.ticker : tab === "backtest" ? App.bt.ticker : "";
  }

  function writeHash() {
    if (App.hashLock) return;
    var name = NAMED_TABS[curTab] ? tabName(curTab) : "";
    /* Name first, then view — "#backtest#NVDA#sweep" reads as a name being
       looked at a particular way, which is the order the sentence goes in.
       `parseHash` matches each segment on its own, so it accepts either. The
       default view is left off: an unadorned "#backtest#NVDA" is the link
       worth having, and the Charts tab has always written its view out. */
    var view = curTab === "charts" ? "#" + App.curView
      : curTab === "backtest" && App.btView !== "rules" ? "#" + App.btView : "";
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
    if (tab === "repeat") { App.rp.ticker = ticker; App.rpStore(); } else { App.bt.ticker = ticker; App.btStore(); }
    if (!store.weekly) return;
    var select = $(tab === "repeat" ? "#rp-ticker" : "#bt-ticker");
    if (select && select.options.length) select.value = ticker;
  }

  function applyHash() {
    var want = parseHash();
    if (want) {
      App.hashLock = true;
      // The view belongs to the tab that owns its name; parseHash has already
      // dropped one that came with the wrong tab.
      if (want.view && want.tab === "charts") App.showChartView(want.view);
      else if (want.view && want.tab === "backtest") App.showBacktestView(want.view);
      applyName(want.tab, want.ticker);
      showTab(want.tab);
      App.hashLock = false;
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
    if (name === "spreads") App.renderSpreads();
    if (name === "charts") App.renderCharts();
    if (name === "repeat") App.renderRepeat();
    if (name === "backtest") App.renderBacktest();
    if (name === "validation") App.renderValidation();
    if (name === "reference") App.renderReference();
    writeHash();
  }
})(window.SpreadApp = window.SpreadApp || {});
