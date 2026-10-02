/* Spread Scanner — the Spread pricer tab's maths.
 *
 * Everything the tab computes, as pure functions of what the reader typed:
 * Black-Scholes, implied volatility, a vertical spread's payoff, a ladder of
 * limit prices from mid to natural, a model fair value, probabilities and
 * expected value, and a ranking of every strike pair in the pasted chain.
 * Nothing here touches the DOM, the network or the scan — the reader's broker
 * quotes are the only input — so tests/test_pricer_js.py runs this exact file
 * under node and checks it against the Python Black-Scholes in tests/conftest.py.
 *
 * Conventions, matching spread_scanner/strategy.py where they overlap:
 *   - one contract is 100 shares; dollar figures per spread are × 100;
 *   - the "planned" fill is mid + 1/3 of the half-spread toward natural
 *     (strategy.FILL_SLIP);
 *   - time is calendar days / 365, and the terminal price is lognormal.
 *
 * Browser: exposes window.SpreadPricer (needs window.SpreadRender for the HTML
 * helpers). Node: module.exports.
 */
(function (root, factory) {
  var R = (typeof module !== "undefined" && module.exports) ? require("./render.js") : root.SpreadRender;
  var api = factory(R);
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.SpreadPricer = api;
})(typeof self !== "undefined" ? self : this, function (R) {
  "use strict";

  var MULT = 100;
  var FILL_SLIP = 1 / 3;
  var LADDER = [0, 0.1, 0.2, FILL_SLIP, 0.5, 0.75, 1];

  // ------------------------------------------------------------ the model

  /* The standard normal CDF to double precision — Hart's algorithm 5666 as
     given by West (2005), "Better approximations to cumulative normal
     functions". The usual short approximation is good to ~1e-7, which is
     ~1e-5 dollars on a $100 stock: harmless, but this is exact enough to
     agree with Python's math.erf, which the scanner's tests price with. */
  function normCdf(x) {
    var z = Math.abs(x), c;
    if (z > 37) {
      c = 0;
    } else {
      var e = Math.exp(-z * z / 2);
      if (z < 7.07106781186547) {
        var n = (((((0.0352624965998911 * z + 0.700383064443688) * z + 6.37396220353165) * z +
                   33.912866078383) * z + 112.079291497871) * z + 221.213596169931) * z + 220.206867912376;
        var d = ((((((0.0883883476483184 * z + 1.75566716318264) * z + 16.064177579207) * z +
                    86.7807322029461) * z + 296.564248779674) * z + 637.333633378831) * z +
                 793.826512519948) * z + 440.413735824752;
        c = e * n / d;
      } else {
        var f = z + 1 / (z + 2 / (z + 3 / (z + 4 / (z + 0.65))));
        c = e / f / 2.506628274631;
      }
    }
    return x > 0 ? 1 - c : c;
  }

  // European option value. T in years, r and q continuously compounded, sigma annual.
  function bs(right, S, K, T, r, q, sigma) {
    var dfr = Math.exp(-r * T), dfq = Math.exp(-q * T);
    if (!(T > 0) || !(sigma > 0)) {
      var intrinsic = right === "call" ? S * dfq - K * dfr : K * dfr - S * dfq;
      return Math.max(0, intrinsic);
    }
    var v = sigma * Math.sqrt(T);
    var d1 = (Math.log(S / K) + (r - q) * T) / v + v / 2;
    var d2 = d1 - v;
    return right === "call"
      ? S * dfq * normCdf(d1) - K * dfr * normCdf(d2)
      : K * dfr * normCdf(-d2) - S * dfq * normCdf(-d1);
  }

  /* The volatility that makes bs() equal `price`, by bisection (the price is
     monotonic in sigma, so this cannot wander off the way Newton can on deep
     out-of-the-money strikes). null when no volatility reproduces the price:
     below intrinsic, or above what an option can be worth. */
  function impliedVol(right, price, S, K, T, r, q) {
    if (!(price > 0) || !(T > 0)) return null;
    var lo = 1e-4, hi = 5;
    if (price < bs(right, S, K, T, r, q, lo) - 1e-9 || price > bs(right, S, K, T, r, q, hi)) return null;
    for (var i = 0; i < 100; i++) {
      var mid = (lo + hi) / 2;
      if (bs(right, S, K, T, r, q, mid) > price) hi = mid; else lo = mid;
      if (hi - lo < 1e-7) break;
    }
    return (lo + hi) / 2;
  }

  // P(S_T > K) under a lognormal with forward S·e^{(r−q)T} and volatility sigma.
  function probAbove(S, K, T, r, q, sigma) {
    if (!(T > 0) || !(sigma > 0)) return S * Math.exp((r - q) * Math.max(T, 0)) > K ? 1 : 0;
    var v = sigma * Math.sqrt(T);
    return normCdf((Math.log(S / K) + (r - q) * T) / v - v / 2);
  }

  // ------------------------------------------------------------ the chain

  function num(s) {
    var t = String(s == null ? "" : s).replace(/[$,%\s]/g, "");
    if (t === "" || t === "-" || t === "—") return null;
    var v = Number(t);
    return isFinite(v) ? v : null;
  }

  /* One expiry, one right, pasted from a broker: a line per strike,
     `strike, bid, ask[, iv]`, separated by commas, tabs or spaces. "$" and "%"
     are ignored. An IV above 3 is read as a percent (32 → 0.32); at or below,
     as a decimal (0.32). A header line, or any line that does not start with a
     number, is skipped and reported — never guessed at — except a first line
     of words, which is a column header. */
  function parseChain(text) {
    var rows = [], errors = [], seen = {};
    String(text || "").split(/\r?\n/).forEach(function (line, i) {
      var raw = line.trim();
      if (!raw) return;
      var parts = raw.split(/[\t,;]+|\s+/).filter(function (p) { return p !== ""; });
      var strike = num(parts[0]), bid = num(parts[1]), ask = num(parts[2]), iv = num(parts[3]);
      var where = "line " + (i + 1);
      // A column header ("Strike Bid Ask IV") is what a paste from a broker
      // starts with, so a first line of words is skipped quietly.
      if (strike === null && !rows.length && !errors.length && /^[A-Za-z]/.test(raw)) return;
      if (strike === null) { errors.push(where + ": skipped (no strike at the start)"); return; }
      if (!(strike > 0)) { errors.push(where + ": skipped (strike must be above 0)"); return; }
      if (bid === null || ask === null) { errors.push(where + ": skipped (needs a bid and an ask)"); return; }
      if (bid < 0 || ask <= 0 || bid > ask) {
        errors.push(where + ": skipped (bid " + bid + " / ask " + ask + " is not a two-sided market)");
        return;
      }
      if (seen[strike]) { errors.push(where + ": skipped (strike " + strike + " already given)"); return; }
      seen[strike] = 1;
      if (iv !== null) iv = iv > 3 ? iv / 100 : iv;
      if (iv !== null && !(iv > 0 && iv < 5)) { errors.push(where + ": IV ignored (out of range)"); iv = null; }
      rows.push({ strike: strike, bid: bid, ask: ask, iv: iv });
    });
    rows.sort(function (a, b) { return a.strike - b.strike; });
    return { rows: rows, errors: errors };
  }

  /* The pasted rows with each strike's mid and an IV: the one the broker gave,
     or else the one implied by the mid. ivSource says which. */
  function prepare(rows, ctx) {
    var T = ctx.days / 365;
    return rows.map(function (row) {
      var mid = (row.bid + row.ask) / 2;
      var iv = row.iv, src = "given";
      if (iv === null || iv === undefined) {
        iv = impliedVol(ctx.right, mid, ctx.spot, row.strike, T, ctx.rate, ctx.div);
        src = iv === null ? "none" : "from mid";
      }
      // No volatility reproduces the mid: the quote is below intrinsic value or
      // above what the option can be worth. It is shown, but never priced into
      // a spread — the model would read the bad quote as a huge edge.
      return { strike: row.strike, bid: row.bid, ask: row.ask, mid: mid, iv: iv, ivSource: src,
               usable: iv !== null };
    });
  }

  /* The chain's IV at the underlying's price, interpolated between the two
     strikes either side (or the nearest one at the edge). The default model
     volatility: one flat number, so the fair value is the spread without the
     skew the market prices into each strike. */
  function atmVol(rows, spot) {
    var pts = rows.filter(function (r) { return r.iv > 0; });
    if (!pts.length) return null;
    for (var i = 0; i < pts.length - 1; i++) {
      var a = pts[i], b = pts[i + 1];
      if (a.strike <= spot && spot <= b.strike) {
        var w = (spot - a.strike) / (b.strike - a.strike);
        return a.iv + w * (b.iv - a.iv);
      }
    }
    return spot < pts[0].strike ? pts[0].iv : pts[pts.length - 1].iv;
  }

  // ------------------------------------------------------------ the spread

  function find(rows, k) {
    for (var i = 0; i < rows.length; i++) if (rows[i].strike === k) return rows[i];
    return null;
  }

  /* A vertical: sell one strike, buy another, same right and expiry. It is a
     credit spread when the leg sold is worth more than the leg bought (at mid),
     else a debit spread. Prices here are per share, positive: the credit
     received or the debit paid. */
  function makeSpread(rows, right, shortK, longK) {
    var s = find(rows, shortK), l = find(rows, longK);
    if (!s || !l || shortK === longK || s.usable === false || l.usable === false) return null;
    var credit = s.mid > l.mid;
    var mid = credit ? s.mid - l.mid : l.mid - s.mid;
    var natural = credit ? s.bid - l.ask : l.ask - s.bid;
    return { right: right, short: s, long: l, kind: credit ? "credit" : "debit",
             width: Math.abs(shortK - longK), mid: mid, natural: natural };
  }

  // The limit price `f` of the way from mid toward natural (0 = mid, 1 = natural).
  function priceAt(sp, f) {
    return sp.kind === "credit" ? sp.mid - f * (sp.mid - sp.natural) : sp.mid + f * (sp.natural - sp.mid);
  }

  /* The spread's model value today, per share, with every leg priced at one
     volatility: for a credit spread, what the short leg is worth minus the long
     leg (what a fair credit would be); for a debit spread the reverse. */
  function fairValue(sp, ctx, sigma) {
    var T = ctx.days / 365;
    var vs = bs(sp.right, ctx.spot, sp.short.strike, T, ctx.rate, ctx.div, sigma);
    var vl = bs(sp.right, ctx.spot, sp.long.strike, T, ctx.rate, ctx.div, sigma);
    return sp.kind === "credit" ? vs - vl : vl - vs;
  }

  /* Everything at one entry price, per share and per spread.

     Expected value is the model's: the discounted expected payoff at expiry,
     which is exactly fairValue(), against the price paid or received. So it is
     zero at the fair value by construction, and the "best" price question
     becomes "how far past fair am I willing to go" — see walkAway(). The
     probabilities use the same lognormal and the same sigma. Held to expiry;
     early exits and assignment are not modelled. */
  function metricsAt(sp, price, ctx, sigma) {
    var T = ctx.days / 365, S = ctx.spot, r = ctx.rate, q = ctx.div;
    var up = sp.right === "call";
    var hiK = Math.max(sp.short.strike, sp.long.strike), loK = Math.min(sp.short.strike, sp.long.strike);
    var credit = sp.kind === "credit";
    var maxProfit = credit ? price : sp.width - price;
    var maxLoss = credit ? sp.width - price : price;
    // A bull spread (put credit, call debit) profits above its breakeven, a bear spread below.
    var bull = (credit && !up) || (!credit && up);
    var be = credit ? (up ? sp.short.strike + price : sp.short.strike - price)
                    : (up ? sp.long.strike + price : sp.long.strike - price);
    var pAbove = function (k) { return probAbove(S, k, T, r, q, sigma); };
    var fair = fairValue(sp, ctx, sigma);
    var out = {
      price: price, maxProfit: maxProfit, maxLoss: maxLoss, breakeven: be,
      creditToWidth: credit ? price / sp.width : null,
      returnOnRisk: maxLoss > 0 ? maxProfit / maxLoss : null,
      pop: bull ? pAbove(be) : 1 - pAbove(be),
      pMaxProfit: bull ? pAbove(hiK) : 1 - pAbove(loK),
      pMaxLoss: bull ? 1 - pAbove(loK) : pAbove(hiK),
      ev: (credit ? price - fair : fair - price) * MULT,
      valid: price > 0 && price < sp.width
    };
    out.evPerRisk = out.maxLoss > 0 ? out.ev / (out.maxLoss * MULT) : null;
    return out;
  }

  function ladder(sp, ctx, sigma) {
    return LADDER.map(function (f) {
      var m = metricsAt(sp, priceAt(sp, f), ctx, sigma);
      m.fraction = f;
      m.planned = f === FILL_SLIP;
      return m;
    });
  }

  /* The worst price worth accepting under the model: the fair value. Past it
     the expected value is negative. Where it falls relative to mid and natural
     is the answer to "what should my limit be". */
  function walkAway(sp, ctx, sigma) {
    var fair = fairValue(sp, ctx, sigma);
    var credit = sp.kind === "credit";
    var betterThanMid = credit ? fair > sp.mid : fair < sp.mid;
    var pastNatural = credit ? fair <= sp.natural : fair >= sp.natural;
    return {
      fair: fair,
      // Where the fair value sits on the mid→natural line, 0 = mid, 1 = natural.
      fraction: sp.natural === sp.mid ? null : (sp.mid - fair) / (sp.mid - sp.natural),
      status: betterThanMid ? "no-edge-at-mid" : pastNatural ? "edge-at-natural" : "edge-inside"
    };
  }

  /* Every strike pair of the pasted chain as a credit spread or a debit spread
     (whichever `kind` asks for), priced at the planned fill, ranked by expected
     value per dollar at risk. Pairs whose planned price is not strictly between
     0 and the width are dropped: there is no trade there. */
  function rankPairs(rows, ctx, sigma, kind, limit) {
    var out = [];
    for (var i = 0; i < rows.length; i++) {
      for (var j = 0; j < rows.length; j++) {
        if (i === j) continue;
        var sp = makeSpread(rows, ctx.right, rows[i].strike, rows[j].strike);
        if (!sp || sp.kind !== kind) continue;
        var m = metricsAt(sp, priceAt(sp, FILL_SLIP), ctx, sigma);
        if (!m.valid) continue;
        out.push({ shortK: sp.short.strike, longK: sp.long.strike, width: sp.width, m: m });
      }
    }
    out.sort(function (a, b) { return (b.m.evPerRisk || 0) - (a.m.evPerRisk || 0); });
    return out.slice(0, limit || 15);
  }

  // ------------------------------------------------------------ HTML

  var esc = R.esc;
  function usd(v, digits) {
    if (v === null || v === undefined || !isFinite(v)) return "—";
    var d = digits === undefined ? 2 : digits;
    return (v < 0 ? "−$" : "$") + Math.abs(v).toFixed(d).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }
  function pc(v, d) { return v === null || v === undefined || !isFinite(v) ? "—" : (v * 100).toFixed(d === undefined ? 0 : d) + "%"; }
  function rungLabel(f) {
    if (f === 0) return "mid";
    if (f === 1) return "natural";
    return Math.round(f * 100) + "% toward natural";
  }

  function ladderTable(sp, rungs) {
    var credit = sp.kind === "credit";
    return '<div class="tablewrap"><table class="stats pr-table"><thead><tr>' +
      ["Limit", credit ? "Credit" : "Debit", "Max profit", "Max loss", "Breakeven",
       credit ? "Credit / width" : "Return on risk", "P(profit)", "Expected value"].map(function (h, i) {
        return "<th" + (i ? ' class="r"' : "") + ">" + esc(h) + "</th>";
      }).join("") + "</tr></thead><tbody>" + rungs.map(function (m) {
        return '<tr class="' + (m.planned ? "pr-planned" : "") + '"><td>' + esc(rungLabel(m.fraction)) +
          (m.planned ? ' <span class="faint">(the scanner\'s planned fill)</span>' : "") + "</td>" +
          '<td class="r">' + usd(m.price) + '</td><td class="r">' + usd(m.maxProfit * MULT, 0) +
          '</td><td class="r">' + usd(m.maxLoss * MULT, 0) + '</td><td class="r">' + usd(m.breakeven) +
          '</td><td class="r">' + (credit ? pc(m.creditToWidth) : pc(m.returnOnRisk)) +
          '</td><td class="r">' + pc(m.pop) + '</td><td class="r pr-ev' + (m.ev >= 0 ? " up" : " down") + '">' +
          usd(m.ev, 0) + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  function rankTable(pairs, kind) {
    if (!pairs.length) return '<p class="empty">No ' + esc(kind) + " spread in this chain has a tradable price.</p>";
    var credit = kind === "credit";
    return '<div class="tablewrap"><table class="stats pr-table"><thead><tr>' +
      ["Sell", "Buy", "Width", credit ? "Credit" : "Debit", "Max loss", credit ? "Credit / width" : "Return on risk",
       "P(profit)", "Expected value", "EV per $ risked", ""].map(function (h, i) {
        return "<th" + (i ? ' class="r"' : "") + ">" + esc(h) + "</th>";
      }).join("") + "</tr></thead><tbody>" + pairs.map(function (p) {
        var m = p.m;
        return "<tr><td>" + esc(p.shortK) + '</td><td class="r">' + esc(p.longK) + '</td><td class="r">' +
          esc(p.width) + '</td><td class="r">' + usd(m.price) + '</td><td class="r">' + usd(m.maxLoss * MULT, 0) +
          '</td><td class="r">' + (credit ? pc(m.creditToWidth) : pc(m.returnOnRisk)) + '</td><td class="r">' +
          pc(m.pop) + '</td><td class="r pr-ev' + (m.ev >= 0 ? " up" : " down") + '">' + usd(m.ev, 0) +
          '</td><td class="r">' + pc(m.evPerRisk, 1) + '</td><td class="r"><button class="chip pr-use" data-short="' +
          esc(p.shortK) + '" data-long="' + esc(p.longK) + '">Price it</button></td></tr>';
      }).join("") + "</tbody></table></div>";
  }

  function chainTable(rows) {
    return '<div class="tablewrap"><table class="stats pr-table"><thead><tr><th>Strike</th><th class="r">Bid</th>' +
      '<th class="r">Ask</th><th class="r">Mid</th><th class="r">IV</th></tr></thead><tbody>' +
      rows.map(function (r) {
        return "<tr><td>" + esc(r.strike) + '</td><td class="r">' + usd(r.bid) + '</td><td class="r">' + usd(r.ask) +
          '</td><td class="r">' + usd(r.mid) + '</td><td class="r">' + pc(r.iv, 1) +
          (r.ivSource === "given" ? "" : r.ivSource === "none"
            ? ' <span class="pr-bad">no IV fits this price — not used</span>'
            : ' <span class="faint">' + esc(r.ivSource) + "</span>") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
  }

  /* The sentence the tab leads with. Plain about what the model can and cannot
     say: with sigma at the market's own ATM vol, a credit beating fair value is
     mostly the skew premium, which is payment for tail risk, not free money. */
  function advice(sp, w, sigma, sigmaIsDefault) {
    var credit = sp.kind === "credit";
    var verb = credit ? "collect" : "pay";
    var base = "Start at mid, " + usd(sp.mid) + ". ";
    var vol = (sigma * 100).toFixed(1) + "% volatility" + (sigmaIsDefault ? " (the chain's at-the-money IV)" : "");
    if (w.status === "no-edge-at-mid") {
      return base + "At " + vol + " the spread is worth " + usd(w.fair) + ", so even a fill at mid has negative " +
        "expected value: the market is not " + (credit ? "paying enough" : "cheap enough") + " under this volatility.";
    }
    if (w.status === "edge-at-natural") {
      return base + "At " + vol + " the spread is worth " + usd(w.fair) + ", beyond the natural price (" +
        usd(sp.natural) + "), so every price on the ladder has positive expected value under this volatility.";
    }
    return base + "At " + vol + " the spread is worth " + usd(w.fair) + ": " + (credit ? "don't " + verb + " less" :
      "don't " + verb + " more") + " than that, about " + Math.round(w.fraction * 100) + "% of the way from mid " +
      "to natural. Past it the expected value turns negative.";
  }

  /* What a positive expected value means when the model volatility is the
     chain's own at-the-money IV. Out-of-the-money puts usually trade at a
     higher IV than at-the-money ones (the skew), so pricing them at the flat
     ATM vol makes selling them look like an edge. That premium is the market's
     price for crash risk, which a flat lognormal underweights — not free money. */
  function skewNote(sp, sigmaIsDefault, ev) {
    if (!sigmaIsDefault || !(ev > 0)) return "";
    var shortIv = sp.short.iv, longIv = sp.long.iv;
    if (!(shortIv > 0) || !(longIv > 0)) return "";
    var sold = sp.kind === "credit" ? "selling" : "buying";
    return "This edge comes from " + sold + " options priced at a different IV (" + (shortIv * 100).toFixed(1) +
      "% and " + (longIv * 100).toFixed(1) + "%) from the flat at-the-money vol the model uses. Skew is usually the " +
      "market's price for tail risk that a lognormal underweights, so treat the edge as compensation for that risk, " +
      "not as free money. Enter your own volatility estimate to test it.";
  }

  return {
    MULT: MULT, FILL_SLIP: FILL_SLIP, LADDER: LADDER,
    normCdf: normCdf, bs: bs, impliedVol: impliedVol, probAbove: probAbove,
    parseChain: parseChain, prepare: prepare, atmVol: atmVol,
    makeSpread: makeSpread, priceAt: priceAt, fairValue: fairValue, metricsAt: metricsAt,
    ladder: ladder, walkAway: walkAway, rankPairs: rankPairs,
    usd: usd, ladderTable: ladderTable, rankTable: rankTable, chainTable: chainTable, advice: advice,
    skewNote: skewNote
  };
});
