/* Spread Scanner — frontend: the Spread pricer tab.
 * One of the files in public/assets/app/ (see core.js for how they fit together).
 *
 * A calculator, not a report: the reader pastes one expiry's chain from their
 * broker and picks two strikes. Nothing is fetched and nothing leaves the
 * browser. The maths lives in assets/pricer.js (node-tested); this file reads
 * the form, keeps it in localStorage, and redraws the results. */
(function (App) {
  "use strict";

  App.renderPricer = renderPricer;

  var P = window.SpreadPricer;
  var KEY = "pricer";
  var FIELDS = ["spot", "days", "rate", "div", "vol", "chain"];
  var wired = false;
  var pick = { shortK: null, longK: null };
  var kind = "credit";        // which pairs the ranking lists
  var timer = null;

  // A chain to try the tab with. Illustrative numbers, not a live quote.
  var EXAMPLE = {
    spot: "102.40", days: "30", rate: "4", div: "0", vol: "", right: "put",
    chain: "strike, bid, ask\n85, 0.38, 0.44\n90, 0.80, 0.90\n95, 1.70, 1.85\n" +
           "100, 3.30, 3.45\n105, 5.85, 6.10\n110, 9.40, 9.80"
  };

  function el(id) { return App.$("#pr-" + id); }

  function save() {
    var state = { right: right(), pick: pick, kind: kind };
    FIELDS.forEach(function (f) { state[f] = el(f).value; });
    try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* private mode */ }
  }

  function restore() {
    var state = null;
    try { state = JSON.parse(localStorage.getItem(KEY) || "null"); } catch (e) { state = null; }
    if (!state) return;
    FIELDS.forEach(function (f) { if (typeof state[f] === "string") el(f).value = state[f]; });
    setRight(state.right === "call" ? "call" : "put");
    if (state.pick) pick = { shortK: state.pick.shortK, longK: state.pick.longK };
    if (state.kind === "debit") kind = "debit";
  }

  function right() {
    var on = document.querySelector('#pr-right button[aria-pressed="true"]');
    return on ? on.dataset.right : "put";
  }
  function setRight(r) {
    var b = document.querySelectorAll("#pr-right button");
    for (var i = 0; i < b.length; i++) b[i].setAttribute("aria-pressed", b[i].dataset.right === r ? "true" : "false");
  }

  function wire() {
    if (wired) return;
    wired = true;
    restore();
    FIELDS.forEach(function (f) {
      el(f).addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { save(); draw(); }, 150);
      });
    });
    var rb = document.querySelectorAll("#pr-right button");
    for (var i = 0; i < rb.length; i++) {
      rb[i].addEventListener("click", function () {
        setRight(this.dataset.right); pick = { shortK: null, longK: null }; save(); draw();
      });
    }
    el("example").addEventListener("click", function () {
      FIELDS.forEach(function (f) { el(f).value = EXAMPLE[f]; });
      setRight(EXAMPLE.right); pick = { shortK: null, longK: null }; save(); draw();
    });
    // The results are redrawn wholesale, so their controls are wired by delegation.
    el("out").addEventListener("change", function (e) {
      if (e.target.id === "pr-short") pick.shortK = Number(e.target.value);
      else if (e.target.id === "pr-long") pick.longK = Number(e.target.value);
      else return;
      save(); draw();
    });
    el("out").addEventListener("click", function (e) {
      var t = e.target.closest ? e.target.closest("button") : null;
      if (!t) return;
      if (t.classList.contains("pr-use")) {
        pick = { shortK: Number(t.dataset.short), longK: Number(t.dataset.long) };
        save(); draw();
        var top = el("spread");
        if (top && top.scrollIntoView) top.scrollIntoView({ behavior: "smooth", block: "start" });
      } else if (t.dataset.kind) {
        kind = t.dataset.kind; save(); draw();
      }
    });
  }

  function renderPricer() { wire(); draw(); }

  function pctField(id, fallback) {
    var v = Number(el(id).value);
    return el(id).value.trim() === "" || !isFinite(v) ? fallback : v / 100;
  }

  // A default pair: for a credit spread, sell the first strike out of the money
  // and buy the next one further out.
  function defaultPick(rows, spot, r) {
    var usable = rows.filter(function (x) { return x.usable; });
    var otm = usable.filter(function (x) { return r === "put" ? x.strike < spot : x.strike > spot; });
    if (r === "put") otm.reverse();
    if (otm.length >= 2) return { shortK: otm[0].strike, longK: otm[1].strike };
    if (usable.length >= 2) return { shortK: usable[0].strike, longK: usable[1].strike };
    return { shortK: null, longK: null };
  }

  function draw() {
    var out = el("out");
    var esc = App.esc;
    var spot = Number(el("spot").value), days = Number(el("days").value);
    var ctx = { spot: spot, days: days, rate: pctField("rate", 0), div: pctField("div", 0), right: right() };
    var parsed = P.parseChain(el("chain").value);
    var problems = [];
    if (!(spot > 0)) problems.push("the underlying price");
    if (!(days >= 1)) problems.push("days to expiry (1 or more)");
    if (parsed.rows.length < 2) problems.push("at least two strikes in the chain");
    var errs = parsed.errors.length
      ? '<ul class="pr-errors">' + parsed.errors.map(function (e) { return "<li>" + esc(e) + "</li>"; }).join("") + "</ul>"
      : "";
    if (problems.length) {
      out.innerHTML = errs + '<p class="empty">Enter ' + esc(problems.join(", ")) +
        " to price a spread — or load the example.</p>";
      return;
    }

    var rows = P.prepare(parsed.rows, ctx);
    var atm = P.atmVol(rows, spot);
    var own = pctField("vol", null);
    var sigma = own && own > 0 ? own : atm;
    if (!(sigma > 0)) {
      out.innerHTML = errs + P.chainTable(rows) +
        '<p class="empty">No strike\'s price implies a volatility, so there is nothing to model with. ' +
        "Check the underlying price, days and quotes, or enter your own volatility.</p>";
      return;
    }
    var keys = rows.filter(function (x) { return x.usable; }).map(function (x) { return x.strike; });
    if (keys.indexOf(pick.shortK) === -1 || keys.indexOf(pick.longK) === -1 || pick.shortK === pick.longK) {
      pick = defaultPick(rows, spot, ctx.right);
    }
    var sp = pick.shortK === null ? null : P.makeSpread(rows, ctx.right, pick.shortK, pick.longK);

    function select(id, label, value) {
      return '<label class="ctl"><span>' + esc(label) + '</span><select id="' + id + '">' + keys.map(function (k) {
        return '<option value="' + esc(k) + '"' + (k === value ? " selected" : "") + ">" + esc(k) + " " +
          esc(ctx.right) + "</option>";
      }).join("") + "</select></label>";
    }

    var html = errs;
    html += '<h2 id="pr-spread">The spread</h2><div class="controls">' +
      select("pr-short", "Sell", pick.shortK) + select("pr-long", "Buy", pick.longK) +
      '<div class="ctl"><span>Model volatility</span><b class="pr-vol">' + (sigma * 100).toFixed(1) + "%</b>" +
      '<span class="faint pr-volnote">' + (own > 0 ? "yours" : "the chain's at-the-money IV") + "</span></div></div>";

    if (!sp) {
      html += '<p class="empty">Pick two different strikes with usable quotes.</p>';
    } else {
      var w = P.walkAway(sp, ctx, sigma);
      var rungs = P.ladder(sp, ctx, sigma);
      var planned = rungs.filter(function (m) { return m.planned; })[0];
      var credit = sp.kind === "credit";
      html += '<div class="pr-cards">' +
        card(credit ? "Credit spread" : "Debit spread", "$" + sp.width + " wide · " + ctx.right + "s") +
        card("Mid", P.usd(sp.mid)) + card("Natural", P.usd(sp.natural)) +
        card("Model fair value", P.usd(w.fair)) +
        card("Planned fill", P.usd(planned.price)) + "</div>" +
        '<div class="verdict ' + (w.status === "no-edge-at-mid" ? "bad" : "good") + '">' +
        esc(P.advice(sp, w, sigma, !(own > 0))) + "</div>" +
        (P.skewNote(sp, !(own > 0), planned.ev)
          ? '<p class="pr-note">' + esc(P.skewNote(sp, !(own > 0), planned.ev)) + "</p>" : "") +
        "<h3>Limit-price ladder</h3>" +
        '<p class="faint pr-small">Per spread (one contract each leg, ×100). Expected value is the model\'s: the ' +
        "discounted expected payoff at the model volatility, against the price. Probabilities are at expiry, " +
        "from the same model.</p>" +
        P.ladderTable(sp, rungs) +
        '<p class="faint pr-small">Chance of keeping the full ' + (credit ? "credit" : "width") + ": " +
        pct(planned.pMaxProfit) + " · chance of the maximum loss: " + pct(planned.pMaxLoss) + ".</p>";
    }

    html += "<h2>Best strikes in this chain</h2>" +
      '<div class="filters" role="group" aria-label="Spread type">' +
      ["credit", "debit"].map(function (k) {
        return '<button class="chip" data-kind="' + k + '" aria-pressed="' + (k === kind) + '">' +
          (k === "credit" ? "Credit spreads" : "Debit spreads") + "</button>";
      }).join("") + "</div>" +
      '<p class="faint pr-small">Every pair of strikes above, priced at the planned fill (mid + a third of the ' +
      "way to natural) and ranked by expected value per dollar at risk. Top 15.</p>" +
      P.rankTable(P.rankPairs(rows, ctx, sigma, kind, 15), kind) +
      "<h2>The chain you entered</h2>" + P.chainTable(rows);
    out.innerHTML = html;
  }

  function card(label, value) {
    return '<div class="pr-card"><span>' + App.esc(label) + "</span><b>" + App.esc(value) + "</b></div>";
  }
  function pct(v) { return v === null || !isFinite(v) ? "—" : Math.round(v * 100) + "%"; }
})(window.SpreadApp = window.SpreadApp || {});
