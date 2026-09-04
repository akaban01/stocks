"""Write the scan out as JSON — the only thing the backend renders.

This module used to build Markdown and a hand-rolled HTML dashboard. It no
longer does: **the backend emits data, the frontend renders it.** Deciding what
to trade is now a genuinely complex call (volatility regime, term structure,
skew, liquidity, catalysts, exact strikes), and baking that into f-string HTML
made both halves harder to change.

So everything lands in ``<outdir>/data/`` as versioned JSON:

    scan.json     the ranked signals, the option/IV read, one explicit strategy
                  recommendation per ticker, and the ≈13-month spread candidates
                  behind the Spreads tab — plus the copy the UI needs (glossary,
                  action labels, strategy playbook), so the frontend never has
                  to hardcode trading explanations.
    signals.csv   the same rows, flat, for spreadsheets.

``SCHEMA_VERSION`` is bumped whenever the shape changes so a cached frontend can
tell it is looking at something it doesn't understand.
"""

from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import pandas as pd

SCHEMA_VERSION = "2.2.0"

# An equity option quoted below this annualized implied volatility is not a
# quote. Outside US market hours the feed returns every contract with a floor
# value — 0.01% to 0.8% observed — and zero bid, ask and open interest: data
# shaped like data, with nothing in it.
MIN_PLAUSIBLE_IV = 5.0

# ---------------------------------------------------------------------------
# UI copy. It lives here — not in the frontend — so the meaning of a field and
# the field itself ship together and can never drift apart.
# ---------------------------------------------------------------------------

ACTIONS = {
    "BUY_PREMIUM": {
        "label": "Buy premium",
        "verb": "BUY",
        "tone": "buy",
        "blurb": "Options are cheap relative to how much this name actually moves. "
                 "You want to be long optionality — pay a defined premium and let the move come to you.",
    },
    "SELL_PREMIUM": {
        "label": "Sell premium",
        "verb": "SELL",
        "tone": "sell",
        "blurb": "Options are rich relative to realized movement. You want to be the seller — "
                 "collect the premium and let time decay work, with the risk defined by long wings.",
    },
    "NEUTRAL_INCOME": {
        "label": "Collect decay",
        "verb": "DECAY",
        "tone": "neutral",
        "blurb": "No cheap-or-rich edge outright, but the shape of the volatility curve pays you "
                 "to own time in one expiry and sell it in another.",
    },
    "STAND_ASIDE": {
        "label": "Stand aside",
        "verb": "WAIT",
        "tone": "wait",
        "blurb": "Nothing here is mispriced enough to pay for the risk. No trade is a position.",
    },
    "NO_DATA": {
        "label": "Not priced",
        "verb": "—",
        "tone": "none",
        "blurb": "No option chain was read for this ticker in this run, so no premium call can be made.",
    },
}

PREMIUM_STATES = {
    "cheap": {"label": "Cheap", "rule": "Low IV → BUY options",
              "detail": "Implied volatility sits at the low end of this name's own range and near or "
                        "below what it actually realizes. Long premium: straddles, strangles, debit spreads."},
    "fair": {"label": "Fair", "rule": "Mid IV → no volatility edge",
             "detail": "Options are priced about right. Any trade here has to come from the chart or the "
                       "term structure, not from the level of volatility."},
    "rich": {"label": "Rich", "rule": "High IV → SELL premium",
             "detail": "Implied volatility is high in its own range and well above realized. Short premium: "
                       "credit spreads and iron condors, with defined risk."},
    "unknown": {"label": "Unknown", "rule": "No IV read",
                "detail": "The option chain wasn't priced for this name this run."},
}

GLOSSARY = {
    "score": "Setup Score, 0–100: how coiled the chart is right now — compressed Bollinger bandwidth, "
             "low realized-vol percentile, and an active TTM squeeze. It says a move is loading. It says "
             "nothing about direction.",
    "iv_rank": "IV Rank, 0–100: where today's implied volatility sits between the low and the high of the "
               "trailing year. Under ~25 is cheap, over ~65 is rich. Free data has no implied-vol history, "
               "so this is ranked against the name's own realized-volatility range — a proxy that reads a "
               "little high, because implied vol carries a persistent premium over realized.",
    "iv_percentile": "The share of the trailing year's volatility readings that sit below today's implied "
                     "volatility. Same proxy caveat as IV Rank.",
    "premium_score": "0–100 blend of IV rank (45%), the IV/HV risk premium (40%) and the term structure "
                     "(15%). This is the number the buy-or-sell call is actually made on.",
    "iv_hv_ratio": "Implied volatility divided by realized. Around 1.1–1.2× is the normal risk premium; "
                   "well above that is the market paying you to sell, well below it is a discount to buy.",
    "vrp": "Volatility risk premium: implied minus realized, in volatility points.",
    "implied_move_pct": "How far the option market is pricing this name to travel over the horizon, as a "
                        "percentage of spot.",
    "hist_move_pct": "The same move estimated from realized volatility instead. The gap between the two is "
                     "the edge.",
    "term_structure": "Front-expiry IV against a later one. Contango (later is pricier) is normal. "
                      "Backwardation means the market expects something soon — it favours selling the "
                      "front month or running a calendar.",
    "skew": "Out-of-the-money put IV minus call IV, in vol points. Fat put skew means the put side pays "
            "more to sell than the call side.",
    "liquidity": "How tradable the chain is, from the at-the-money bid/ask spread and open interest. Wide "
                 "markets quietly cost more than the edge is worth, so thin names get simpler structures "
                 "or none at all.",
    "pop": "Model probability of finishing in the profit zone: N(d₂) under a lognormal whose expected "
           "price is today's, at the chain's own implied volatility. Because the price rather than its "
           "logarithm is held flat, the median outcome sits slightly below spot — so the chance of "
           "finishing above spot is a little under half, and more so the longer the expiry. An estimate, "
           "not a guarantee; it also reads the volatility at the money rather than at each strike, and a "
           "high probability of a small win is not the same as a good trade.",
    "credit_to_width": "Credit collected divided by the width of the spread. Under ~20% you are being paid "
                       "too little for the risk.",
    "em_pct": "One-sigma expected move over the horizon from realized volatility. Roughly 68% of outcomes "
              "land inside ±1σ, 95% inside ±2σ.",
    "squeeze": "The TTM squeeze: Bollinger Bands sitting inside the Keltner Channels. On means energy is "
               "building; fired means it just released, which is the actual entry trigger.",
    "lean": "A faint directional hint from squeeze momentum. A tiebreaker between two structures, never a "
            "reason to take a position.",
    "long_dated": "The ≈13-month expiry the Spreads tab is built on. It is whichever listed expiry "
                  "sits closest to 395 days out, because exchanges list long-dated options on "
                  "January cycles rather than on a rolling 13-month schedule — so the real number "
                  "of days is always shown next to it.",
    "leaps_vega": "Over a year, the level of implied volatility moves a long-dated spread far more "
                  "than time decay does. A 13-month debit spread is mostly a bet on direction and "
                  "partly a bet on volatility rising; a 13-month credit spread earns its theta "
                  "almost entirely in the final months.",
    "reward_to_risk": "Maximum profit divided by maximum loss — what the trade pays if it works, "
                      "against what it costs if it does not.",
    "annualised_return": "The same reward-to-risk put on a yearly footing (× 365 ÷ the days the "
                         "maximum is earned over), so a thirteen-month spread can be compared with "
                         "a monthly one. A 0.6× return over 409 days is not better than 0.3× over "
                         "30 days, because the monthly trade recycles the same capital twelve "
                         "times. For everything but the diagonal those days are simply the days to "
                         "expiry; the Poor Man's Covered Call is annualised over its short leg, "
                         "because the maximum quoted for it is the assigned-on-the-first-call case "
                         "and that arrives at the front expiry. It assumes the position could be "
                         "repeated, which is an assumption, not a forecast — and it says nothing "
                         "about how likely either is to win.",
    "risk_form": "What actually secures a position: a debit you have already paid, margin against a "
                 "short option, or shares you already own. It decides how the trade can hurt you, "
                 "which is a different question from how likely it is to win.",
    "earnings": "Calendar days to the next report. Inside the expiry, IV is elevated for a reason and "
                "collapses the morning after — that cuts both ways depending on which side you're on.",
    "debt_cash_ratio": "The balance-sheet screen: interest-bearing debt and cash as a share of market "
                       "cap, both required under ~33%.",
}

DISCLAIMER = {
    "general": "Educational tool, not financial advice. Expected-move bands and probabilities are "
               "statistical estimates from past and implied volatility — not predictions. Option prices "
               "are last-known mids and will have moved; price every trade in your broker before placing it.",
    "risk": "Every position here can lose. A debit spread can expire worthless and take the whole "
            "premium with it; a credit spread can lose several times what it collected. Position "
            "sizes are computed against the risk budgets in config.yaml, not against your account "
            "or your tolerance for a losing year.",
    "method": "Indicators are computed without look-ahead: every value uses only data up to and including "
              "its own bar. IV rank and percentile are proxies ranked against realized-volatility history, "
              "because free data sources do not publish implied-volatility history.",
}


# ------------------------------------------------------------------- helpers

def _clean(obj):
    """Recursively turn pandas/numpy values into JSON-safe Python ones."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if isinstance(obj, (str, bool)):
        return obj
    if isinstance(obj, (int,)) and not isinstance(obj, bool):
        return int(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    # Missing values first: pd.NaT survives .item() unchanged and would otherwise
    # fall through to str() and serialize as the literal string "NaT".
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass                      # arrays and most objects aren't NA-testable
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    # numpy / pandas scalars
    item = getattr(obj, "item", None)
    if callable(item):
        try:
            unwrapped = item()
        except (ValueError, TypeError):
            unwrapped = obj
        if unwrapped is not obj:
            return _clean(unwrapped)
    return str(obj)


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean(payload), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def _now() -> tuple[str, str]:
    now = dt.datetime.now(dt.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ"), now.strftime("%Y-%m-%d %H:%M UTC")


def _counts(signals: list[dict]) -> dict:
    out: dict[str, int] = {}
    for s in signals:
        action = ((s.get("recommendation") or {}).get("action")) or "NO_DATA"
        out[action] = out.get(action, 0) + 1
    return out


def _headline_actions(signals: list[dict], limit: int = 5) -> list[dict]:
    """The few names actually worth opening the page for: a real trade, ranked
    by how much the inputs agree rather than by how coiled the chart is."""
    live = [s for s in signals
            if (s.get("recommendation") or {}).get("action") in
            ("BUY_PREMIUM", "SELL_PREMIUM", "NEUTRAL_INCOME")]
    live.sort(key=lambda s: s["recommendation"].get("confidence") or 0, reverse=True)
    return [{
        "ticker": s["ticker"],
        "action": s["recommendation"]["action"],
        "strategy": (s["recommendation"].get("plan") or {}).get("name"),
        "headline": s["recommendation"]["headline"],
        "detail": s["recommendation"]["detail"],
        "confidence": s["recommendation"].get("confidence"),
    } for s in live[:limit]]


# -------------------------------------------------------------- the main write

def option_data_health(signals: list[dict]) -> dict:
    """Did the option feed return real quotes, or empty contracts?

    Only meaningful once something has been priced: a scan with the options
    layer switched off has no option data to judge, which is a different thing
    from having bad option data. The check is deliberately blunt — it asks
    whether the feed was working at all, not whether any individual name looks
    odd — because the failure it exists to catch is total."""
    priced = [s for s in signals if s.get("options")]
    usable = [s for s in priced
              if (s["options"].get("iv_annual") or 0) >= MIN_PLAUSIBLE_IV]
    ivs = sorted((s["options"].get("iv_annual") or 0) for s in priced)
    health = {
        "priced": len(priced),
        "usable": len(usable),
        "median_iv": ivs[len(ivs) // 2] if ivs else None,
        "ok": True,
        "reason": "",
    }
    # Half is a wide margin: a working feed prices every name it reaches, and a
    # broken one prices none of them. Nothing observed has landed in between.
    if priced and len(usable) * 2 < len(priced):
        health["ok"] = False
        health["reason"] = (
            f"only {len(usable)} of {len(priced)} priced names came back with a plausible "
            f"implied volatility (median {health['median_iv']}%, floor {MIN_PLAUSIBLE_IV}%). "
            "That is what the feed returns outside US market hours: every contract present, "
            "with no bid, no ask and no open interest. A scan built on it is worth less than "
            "the last good one it would replace."
        )
    return health


def _long_dated_summary(signals: list[dict]) -> dict:
    """Headline counts for the Spreads tab, so it can say what it has before
    the reader scrolls a table of legs."""
    blocks = [s["long_dated"] for s in signals if s.get("long_dated")]
    candidates = sum(len(b.get("candidates") or []) for b in blocks)
    expiries = sorted({b["expiry"] for b in blocks if b.get("expiry")})
    return {
        "tickers": len(blocks),
        "candidates": candidates,
        "preferred": sum(1 for b in blocks if b.get("preferred")),
        "expiries": expiries,
        "target_days": blocks[0]["target_days"] if blocks else None,
    }


def build_scan(df: pd.DataFrame, params: dict, *,
               weights: dict | None = None,
               weights_as_of: str | None = None,
               recommendations: dict[str, dict] | None = None,
               option_views: dict | None = None,
               long_spreads: dict[str, dict] | None = None,
               universe: dict | None = None,
               playbook: dict | None = None) -> dict:
    """Assemble the full scan payload (no I/O — handy to test and to reuse)."""
    now_iso, now_utc = _now()
    recommendations = recommendations or {}
    option_views = option_views or {}
    long_spreads = long_spreads or {}

    signals: list[dict] = []
    for row in (df.to_dict("records") if not df.empty else []):
        ticker = str(row.get("ticker", ""))
        signal = _clean(row)
        # The option/IV read lives in its own block rather than smeared across
        # the row, so the frontend can show "no IV data" as its own state.
        view = option_views.get(ticker)
        signal["options"] = _clean(view.as_dict()) if view is not None else None
        signal["recommendation"] = _clean(recommendations.get(ticker))
        # The ≈13-month spreads are a separate block, not a variant of the
        # recommendation: they answer a different question on the same chain,
        # and most names have no long-dated chain at all.
        signal["long_dated"] = _clean(long_spreads.get(ticker))
        signals.append(signal)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso,
        "generated_at_utc": now_utc,
        "horizon_days": int(params.get("horizon_days", 10)),
        "params": _clean(params),
        "universe": _clean(universe or {}),
        "weights": {
            "values": _clean(weights or {}),
            "as_of": weights_as_of,
            "source": "auto-calibrated" if weights_as_of else "default",
        },
        "counts": _counts(signals),
        "long_dated": _long_dated_summary(signals),
        "top_actions": _headline_actions(signals),
        "reference": {
            "actions": ACTIONS,
            "premium_states": PREMIUM_STATES,
            "glossary": GLOSSARY,
            "playbook": playbook or {},
        },
        "disclaimer": DISCLAIMER,
        "signals": signals,
    }


def write_scan(df: pd.DataFrame, outdir: str | Path, params: dict, **kwargs) -> Path:
    """Write ``<outdir>/data/scan.json`` (+ ``signals.csv``). Returns the JSON path."""
    outdir = Path(outdir)
    data_dir = outdir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    if not df.empty:
        df.to_csv(data_dir / "signals.csv", index=False)

    return write_json(data_dir / "scan.json", build_scan(df, params, **kwargs))
