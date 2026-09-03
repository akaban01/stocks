"""Long-dated (≈13-month) spreads — the LEAPS side of the same chain.

The near-term engine in ``strategy.py`` answers *what to place this month*. This
module answers a different question on the same data: **if you wanted this name
for the next year, which spread expresses it?**

They are not the same trade, and the differences are the whole point:

* **Theta barely works for you.** A 13-month short option decays a rounding
  error per day. Selling premium out here is not an income trade; it is a
  directional trade you happen to get paid for.
* **Vega dominates.** Over a year, a shift in the implied-volatility level moves
  the position far more than a week of time decay does. A long-dated debit
  spread is mostly a bet on direction, partly a bet on vol going up.
* **Every long-dated spread is directional.** There is no honest 13-month
  neutral structure: a year-long iron condor collects negligible theta against a
  full year of gap risk. So when the scanner has no directional read, this
  module recommends nothing and simply lists what the chain would support.
* **The strikes go by moneyness, not sigma.** One sigma over 13 months is 40%+
  of spot on a volatile name. Placing a vertical there would make a spread so
  wide it is a synthetic long stock position. Long-dated strikes are placed as
  percentages of spot instead (see the constants below).
* **Four earnings reports fit inside the expiry**, and the bid/ask is several
  times the front month's. Both ship as warnings rather than footnotes.

Structures built here, all off the real long-dated chain:

    leaps_bull_call / leaps_bear_put    debit verticals, direction with a cap
    poor_mans_covered_call              deep-ITM LEAPS call + a short front call
    leaps_bull_put / leaps_bear_call    credit verticals, paid to be right slowly

    ⚠️ Educational tool, not financial advice. A 13-month spread ties up capital
    for 13 months; the position sizes here are against the long-dated risk
    budget in ``config.yaml``, not against your account.
"""

from __future__ import annotations

from .options import LONG_TARGET_DAYS, OptionView
from .strategy import (Plan, chain_side, make_leg, net_cost, pick_quote, pop_estimate,
                       resolve_risk_form, sigma_to_expiry, size_position, strike_step,
                       wing_strike)

# --- strike placement, as fractions of spot ----------------------------------
# Deliberately not in sigmas: see the module docstring.
ITM_DEPTH = 0.20         # long leg of the diagonal, ~20% in the money
VERTICAL_WIDTH = 0.15    # short leg of a debit vertical, ~15% out of the money
CREDIT_OTM = 0.20        # short leg of a credit vertical, ~20% out of the money

# The short leg of the diagonal is a front-month contract, so it *is* placed in
# sigmas — that is the expiry theta actually works on.
DIAGONAL_SHORT_SIGMA = 1.0

# Below this, the long leg of a diagonal has too much extrinsic value left to
# behave like stock, which is the entire premise of the structure.
MIN_ITM_FRACTION = 0.05

EARNINGS_PER_YEAR = 4

PLAYBOOK = {
    "leaps_bull_call": "Buy a call about 13 months out, sell a higher one in the same expiry. A "
                       "bullish position with a year to be right and a cost you know on day one — "
                       "the short leg pays for part of the long one and caps the win.",
    "leaps_bear_put": "Buy a long-dated put, sell a lower one. The same shape pointed down: a year "
                      "of room for a thesis, at a defined cost.",
    "poor_mans_covered_call": "Buy a deep in-the-money call 13 months out as a cheaper stand-in for "
                              "100 shares, then sell a near-dated call against it and roll that "
                              "short leg each month. Covered-call income on a fraction of the "
                              "capital — and the long call, not stock, is what secures it.",
    "leaps_bull_put": "Sell a long-dated put well below the price, buy a lower one for protection. "
                      "You collect the credit up front and keep it if the stock holds up — but the "
                      "capital is committed for a year and the decay arrives mostly at the end.",
    "leaps_bear_call": "Sell a long-dated call well above the price, buy a higher one. The credit "
                       "is yours if the stock stays under the short strike for the year.",
}


# ------------------------------------------------------------------ management

def _manage_debit() -> dict:
    return {
        "profit_target": "take 50–70% of the maximum profit — the last of it takes months to arrive",
        "profit_target_pct": 60,
        "stop": "cut at −50% of the debit, or sooner if the reason you put it on stops being true",
        "stop_loss_pct": 50,
        "time_stop": "roll or close with ~90 days left; the final quarter is where a long-dated "
                     "spread loses its remaining time value fastest",
        "close_by_dte": 90,
    }


def _manage_credit() -> dict:
    return {
        "profit_target": "buy it back at 50% of the credit — usually long before expiry",
        "profit_target_pct": 50,
        "stop": "close if the loss reaches 2× the credit received",
        "stop_loss_multiple": 2.0,
        "time_stop": "close or roll at ~90 days to expiry, before gamma starts to matter",
        "close_by_dte": 90,
    }


def _manage_diagonal() -> dict:
    return {
        "profit_target": "roll the short call out each month for a fresh credit; take the whole "
                         "position off at 50–70% of the maximum",
        "profit_target_pct": 60,
        "stop": "cut if the long call loses half its value — the thesis is gone, not just early",
        "stop_loss_pct": 50,
        "time_stop": "roll the long leg with ~90 days left. Never let the short call go to "
                     "assignment while you still want the long one.",
        "close_by_dte": 90,
    }


# --------------------------------------------------------------- the structures

def _debit_vertical(view: OptionView, bullish: bool) -> Plan | None:
    """Long-dated bull call / bear put spread, placed by moneyness."""
    exp, dte = view.long_expiry, view.long_dte
    right = "call" if bullish else "put"
    long_q = pick_quote(view, exp, right, view.spot)
    if long_q is None:
        return None
    target = view.spot * (1 + VERTICAL_WIDTH * (1 if bullish else -1))
    short_q = pick_quote(view, exp, right, target, exclude={long_q.strike})
    legs = [x for x in (make_leg("buy", long_q, exp), make_leg("sell", short_q, exp)) if x]
    if len(legs) < 2:
        return None
    width = abs(short_q.strike - long_q.strike)
    if width <= 0:
        return None
    plan = Plan(
        key="leaps_bull_call" if bullish else "leaps_bear_put",
        name="LEAPS Bull Call Spread" if bullish else "LEAPS Bear Put Spread",
        action="BUY_PREMIUM", bias="bullish" if bullish else "bearish",
        thesis=("A year of room for the " + ("upside" if bullish else "downside")
                + " thesis, at a cost that is fixed the day you put it on."),
        playbook=PLAYBOOK["leaps_bull_call" if bullish else "leaps_bear_put"],
        vega="long", theta="negative", risk="defined",
        legs=legs, expiry=exp, dte=dte,
        profit_zone="above" if bullish else "below",
        risk_form={"basis": "debit"}, manage=_manage_debit(),
    )
    debit = net_cost(legs)
    if debit is not None and debit > 0:
        plan.max_loss = debit
        plan.max_profit = round(width * 100 - debit, 2)
        plan.breakevens = [round(long_q.strike + (debit / 100) * (1 if bullish else -1), 2)]
    return plan


def _credit_vertical(view: OptionView, bullish: bool) -> Plan | None:
    """Long-dated bull put / bear call spread, short strike ~20% out of the money."""
    exp, dte = view.long_expiry, view.long_dte
    right = "put" if bullish else "call"
    sign = -1 if bullish else 1
    short_q = pick_quote(view, exp, right, view.spot * (1 + sign * CREDIT_OTM))
    if short_q is None:
        return None
    step = strike_step(chain_side(view, exp, right))
    long_q = pick_quote(view, exp, right,
                        wing_strike(view.spot, short_q.strike, step, below=bullish),
                        exclude={short_q.strike})
    legs = [x for x in (make_leg("sell", short_q, exp), make_leg("buy", long_q, exp)) if x]
    if len(legs) < 2:
        return None
    width = abs(short_q.strike - long_q.strike)
    if width <= 0:
        return None
    plan = Plan(
        key="leaps_bull_put" if bullish else "leaps_bear_call",
        name="LEAPS Bull Put Spread" if bullish else "LEAPS Bear Call Spread",
        action="SELL_PREMIUM", bias="bullish" if bullish else "bearish",
        thesis=("Paid up front to be right about a direction over a year, with the loss capped "
                "by the long wing — but the capital is committed for that whole year."),
        playbook=PLAYBOOK["leaps_bull_put" if bullish else "leaps_bear_call"],
        vega="short", theta="positive", risk="defined",
        legs=legs, expiry=exp, dte=dte,
        profit_zone="above" if bullish else "below",
        risk_form={"basis": "credit"}, manage=_manage_credit(),
    )
    net = net_cost(legs)
    if net is not None and net < 0:
        credit = -net
        plan.max_profit = round(credit, 2)
        plan.max_loss = round(width * 100 - credit, 2)
        plan.credit_to_width = round(credit / (width * 100), 3)
        plan.breakevens = [round(short_q.strike + (credit / 100) * (-1 if bullish else 1), 2)]
    return plan


def _poor_mans_covered_call(view: OptionView, front_sigma: float) -> Plan | None:
    """Deep-ITM long-dated call as a stock substitute, with a short front call.

    The long leg has to be far enough in the money that it tracks the shares —
    otherwise it is just a long call with a short call in front of it, and the
    "covered" premise does not hold."""
    long_exp, long_dte = view.long_expiry, view.long_dte
    front_exp = view.expiry
    long_q = pick_quote(view, long_exp, "call", view.spot * (1 - ITM_DEPTH))
    if long_q is None or long_q.strike >= view.spot * (1 - MIN_ITM_FRACTION):
        return None
    short_q = pick_quote(view, front_exp, "call",
                         view.spot * (1 + DIAGONAL_SHORT_SIGMA * front_sigma))
    if short_q is None or short_q.strike <= long_q.strike:
        return None
    legs = [x for x in (make_leg("buy", long_q, long_exp),
                        make_leg("sell", short_q, front_exp)) if x]
    if len(legs) < 2:
        return None
    plan = Plan(
        key="poor_mans_covered_call", name="Poor Man's Covered Call",
        action="SELL_PREMIUM", bias="bullish",
        thesis="Own the year cheaply through a deep in-the-money call, and rent the near month "
               "out against it every cycle.",
        playbook=PLAYBOOK["poor_mans_covered_call"],
        vega="long", theta="positive", risk="defined",
        legs=legs, expiry=long_exp, dte=long_dte, profit_zone="above",
        # Not "covered": there are no shares here. The long call is what secures
        # the short one, and saying otherwise would misdescribe the risk.
        risk_form={"basis": "long_option"}, manage=_manage_diagonal(),
    )
    debit = net_cost(legs)
    if debit is not None and debit > 0:
        plan.max_loss = debit
        # The figure everyone quotes: assigned on the short call at the front
        # expiry, the long call sold to cover it. It ignores the extrinsic value
        # still left in the long leg, so it is a floor, not a ceiling.
        plan.max_profit = round((short_q.strike - long_q.strike) * 100 - debit, 2)
        plan.breakevens = [round(long_q.strike + debit / 100, 2)]
    return plan


_BUILDERS = {
    "leaps_bull_call": lambda v, fs: _debit_vertical(v, True),
    "leaps_bear_put": lambda v, fs: _debit_vertical(v, False),
    "leaps_bull_put": lambda v, fs: _credit_vertical(v, True),
    "leaps_bear_call": lambda v, fs: _credit_vertical(v, False),
    "poor_mans_covered_call": _poor_mans_covered_call,
}

# Everything the chain supports, in the order the tab lists them.
CANDIDATE_ORDER = ["leaps_bull_call", "poor_mans_covered_call", "leaps_bull_put",
                   "leaps_bear_put", "leaps_bear_call"]


# ------------------------------------------------------------------- selection

def preferred_key(premium_state: str, bias: str) -> str | None:
    """Which structure the inputs actually point at — or None.

    Direction is the gate, not volatility: with no directional read there is no
    long-dated spread worth preferring, because none of them are neutral."""
    if bias == "bullish":
        return "leaps_bull_put" if premium_state == "rich" else "leaps_bull_call"
    if bias == "bearish":
        return "leaps_bear_call" if premium_state == "rich" else "leaps_bear_put"
    return None


def _warnings(view: OptionView, row: dict, candidates: list[Plan]) -> list[str]:
    out: list[str] = []
    dte = view.long_dte or 0
    if abs(dte - LONG_TARGET_DAYS) > 60:
        months = dte / 30.4
        out.append(f"The nearest listed expiry to 13 months is {dte} days out (~{months:.0f} months). "
                   "LEAPS are listed on January cycles, so the target rarely lands on a real expiry — "
                   "every number below is for that actual expiry.")
    if view.long_liquidity == "poor":
        out.append(f"The long-dated chain is thin (ATM spread ~{view.long_spread_pct:.0f}% of mid"
                   f"{f', OI {view.long_open_interest:,}' if view.long_open_interest is not None else ''}). "
                   "You pay that spread on the way in and again on the way out, a year apart. "
                   "Work the order at mid and expect to wait."
                   if view.long_spread_pct is not None else
                   "The long-dated chain is thin — expect a wide market on both entry and exit.")
    elif view.long_liquidity == "unknown":
        out.append("No bid/ask depth on the long-dated at-the-money contract, so the prices below "
                   "are indicative only.")
    if view.iv_rank is not None:
        out.append(f"IV rank {view.iv_rank:.0f} is a front-month reading. A 13-month contract is "
                   "priced off a flatter part of the volatility surface, so 'cheap' or 'rich' here "
                   "is a weaker signal than it is on the near expiry"
                   + (f" — this expiry's own ATM IV is {view.long_iv:.0f}%."
                      if view.long_iv is not None else "."))
    out.append(f"About {EARNINGS_PER_YEAR} earnings reports fall inside this expiry. A long-dated "
               "spread is priced through all of them; none of them is the trade.")
    if any(p.key == "poor_mans_covered_call" for p in candidates):
        out.append("The Poor Man's Covered Call's max profit below is the conservative case — "
                   "assigned on the very first short call. Rolling that short leg out each month "
                   "is where the structure actually earns, and no figure here counts those rolls.")
    if any(p.key in ("leaps_bull_put", "leaps_bear_call", "poor_mans_covered_call")
           for p in candidates):
        out.append("The short legs can be assigned early — most often a short call the day before "
                   "an ex-dividend date. Know the dividend calendar before you sell one.")
    if row.get("note"):
        out.append(f"Data note: {row['note']}.")
    return out


def _summary(view: OptionView, preferred: str | None, candidates: list[Plan],
             bias: str) -> str:
    if not candidates:
        return ("The long-dated chain has no strikes at the distances these structures need.")
    if preferred is None:
        return (f"{len(candidates)} long-dated spreads price out on the {view.long_expiry} expiry, "
                "but the scanner has no directional read on this name — and every 13-month spread "
                "is a directional bet. Listed for reference, not recommended.")
    name = next((p.name for p in candidates if p.key == preferred), preferred)
    return (f"The {bias} lean plus {view.premium_state} premium points at the {name} on the "
            f"{view.long_expiry} expiry ({view.long_dte} days out).")


# ------------------------------------------------------------------ entry point

def long_spreads(row: dict, view: OptionView | None, risk_budget: float = 2500.0,
                 bias: str = "neutral", bias_strength: str = "none") -> dict | None:
    """Every ≈13-month spread this chain supports, for one ticker.

    Returns None when the name has no long-dated chain — which is the common
    case for anything outside the large caps, and is worth saying plainly rather
    than filling in with a nearer expiry."""
    if view is None or not view.long_expiry or not view.long_dte:
        return None
    if view.long_expiry not in view.chain:
        return None

    long_sigma = (view.long_iv or view.iv_annual) / 100 * ((view.long_dte / 365) ** 0.5)
    front_sigma = sigma_to_expiry(view, view.days_to_expiry)

    candidates: list[Plan] = []
    for key in CANDIDATE_ORDER:
        plan = _BUILDERS[key](view, front_sigma)
        if plan is None:
            continue
        plan.net = net_cost(plan.legs)
        # The diagonal's two legs expire at different times, so a single-sigma
        # probability would be quietly wrong. Only price POP where the whole
        # structure lands on one expiry.
        if plan.key != "poor_mans_covered_call":
            plan.pop = pop_estimate(view.spot, plan.breakevens, plan.profit_zone, long_sigma)
        plan.sizing = size_position(plan, risk_budget)
        plan.risk_form = resolve_risk_form(plan.risk_form.get("basis", "none"))
        candidates.append(plan)

    preferred = preferred_key(view.premium_state, bias)
    if preferred is not None and not any(p.key == preferred for p in candidates):
        preferred = None

    return {
        "expiry": view.long_expiry,
        "dte": view.long_dte,
        "target_days": LONG_TARGET_DAYS,
        "iv_annual": view.long_iv,
        "front_iv_annual": view.iv_annual,
        "liquidity": view.long_liquidity,
        "atm_spread_pct": view.long_spread_pct,
        "atm_open_interest": view.long_open_interest,
        "premium_state": view.premium_state,
        "bias": bias,
        "bias_strength": bias_strength,
        "preferred": preferred,
        "summary": _summary(view, preferred, candidates, bias),
        "candidates": [p.as_dict() for p in candidates],
        "warnings": _warnings(view, row, candidates),
    }


def long_spreads_all(rows: list[dict], views: dict[str, OptionView],
                     risk_budget: float = 2500.0,
                     biases: dict[str, tuple[str, str]] | None = None) -> dict[str, dict]:
    """{ticker: long-dated block} for every scanned row that has a LEAPS chain."""
    biases = biases or {}
    out: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("ticker", ""))
        if not ticker:
            continue
        bias, strength = biases.get(ticker, ("neutral", "none"))
        block = long_spreads(row, views.get(ticker), risk_budget, bias, strength)
        if block is not None:
            out[ticker] = block
    return out
