"""Strategy engine — turns the scan into one explicit instruction per ticker.

The scanner says *a move is loading*. The options layer says *what the market
charges for it*. Neither, on its own, tells you what to place. This module joins
them and answers the only question that matters at the screen:

    **Buy premium, sell premium, or stand aside — and with which exact legs?**

The decision runs on four axes:

1. **Premium state** (the big one) — from IV rank, the IV/HV risk premium and
   the term structure. Cheap premium favours *buying* options (long vega:
   straddles, strangles, debit spreads). Rich premium favours *selling* them
   (short vega: credit spreads, iron condors).
2. **Directional bias** — normally *none*: this is a both-ways tool. A released
   squeeze with a break direction (or a firm lean) is what promotes a neutral
   structure to a one-sided one.
3. **Catalyst** — earnings inside the expiry flips undefined risk to defined,
   and turns "cheap IV" into "cheap for a reason".
4. **Tradability** — a four-leg condor in a 20%-wide market is a loss on entry,
   so liquidity caps how many legs a recommendation is allowed to have.

Every plan comes back fully specified: real strikes off the live chain, real
expiry, net debit/credit from live mids, max profit, max loss, breakevens, a
model probability of profit, a position size for your risk budget, and the
management rules (profit target, stop, when to be out).

    ⚠️ Educational tool, not financial advice. Every plan carries a `risk_form`
    note describing what secures the position — a paid debit, margin, or shares
    you already own — because that is what decides how it can hurt you.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from .options import OptionView, Quote

# --- strike placement, in sigmas of the chosen expiry ------------------------
SHORT_SIGMA = 1.0        # ~16-delta short strike for credit structures
STRANGLE_SIGMA = 0.75    # long strangle, a touch inside 1σ so it can pay
DEBIT_SHORT_SIGMA = 1.0  # short leg of a vertical debit spread

# Protective wings are placed by *width*, not by another sigma: a 2σ wing on a
# volatile name is 30 points wide, which turns a $400 credit into $2,600 of risk
# for protection you will never use. Aim at ~40% of the distance from spot to the
# short strike, floored at 2 strike increments and capped at 6.
WING_WIDTH_FRAC = 0.40
WING_MIN_STEPS = 2
WING_MAX_STEPS = 6

# --- gates -------------------------------------------------------------------
MIN_SCORE_FOR_FAIR_IV = 55.0   # at fair IV, only a genuinely coiled name is worth a trade
MIN_CREDIT_TO_WIDTH = 0.20     # a credit spread paying less than this isn't worth the risk
MAX_CONTRACTS = 20

# What actually secures each structure. This is the difference between a trade
# that can only lose the cash you put up and one that can be called on for more,
# so it ships alongside every plan rather than being left to the reader.
_RISK_FORM = {
    "debit": ("defined_debit", "You pay a known premium up front and can never lose more than it. "
                               "The debit is the whole risk; nothing can be called on later."),
    "credit": ("short_premium", "You collect a premium for taking on an obligation, held against "
                                "margin. The long wings cap the loss — without them it is open-ended."),
    "covered": ("covered", "The short call is secured by shares you already own rather than by "
                           "margin, so assignment delivers stock you hold instead of creating a short."),
    "long_option": ("option_covered", "The short call is secured by the long call, not by shares and not "
                                      "by margin: assignment is covered by exercising or selling the long "
                                      "leg. That is why the whole position can only lose the debit — and "
                                      "why the long leg must never be closed first."),
    "shares": ("shares_only", "Owning the shares outright — no option contract, no expiry, no margin."),
    "none": ("n/a", "No position — nothing to secure."),
}

# Human-readable copy for each strategy, shipped in the JSON so the frontend
# never has to hardcode trading explanations.
PLAYBOOK = {
    "long_straddle": "Buy the at-the-money call and the at-the-money put. You win if the move is big enough, either way; you lose if it sits still. The trade for cheap options in front of a coiled chart.",
    "long_strangle": "Buy an out-of-the-money call and an out-of-the-money put. Cheaper than a straddle and needs a bigger move to pay — the budget version of the same both-ways bet.",
    "bull_call_spread": "Buy a call, sell a higher one. A capped, cheaper bullish bet — the short leg pays for part of the long one.",
    "bear_put_spread": "Buy a put, sell a lower one. A capped, cheaper bearish bet.",
    "iron_condor": "Sell an out-of-the-money call spread and an out-of-the-money put spread. You collect premium and keep it if price stays between them. Defined risk on both sides.",
    "short_strangle": "Sell an out-of-the-money call and put with no wings. Collects more than a condor, but the loss is open-ended — margin-heavy, experts only.",
    "bull_put_spread": "Sell a put, buy a lower one. You collect credit and win if the stock stays above the short put — a bullish way to be short rich premium.",
    "bear_call_spread": "Sell a call, buy a higher one. You collect credit and win if the stock stays below the short call.",
    "calendar_spread": "Sell the near expiry, buy the same strike further out. Profits when the front decays faster than the back — the trade for an inverted term structure.",
    "covered_call": "Against shares you already own, sell a call above the price. Turns rich premium into income and caps your upside at the strike.",
    "stand_aside": "No edge worth paying for. The best trade is often none.",
    "shares_only": "Trade the underlying instead of options.",
    "no_data": "No usable option chain this run — nothing to price.",
}


# ----------------------------------------------------------------- data model

@dataclass
class Leg:
    action: str               # buy / sell
    right: str                # call / put / share
    strike: float | None
    expiry: str | None
    qty: int
    mid: float | None
    bid: float | None
    ask: float | None
    iv: float | None
    open_interest: int | None
    label: str


@dataclass
class Plan:
    key: str
    name: str
    action: str               # BUY_PREMIUM / SELL_PREMIUM / NEUTRAL_INCOME / STAND_ASIDE / NO_DATA
    bias: str                 # bullish / bearish / neutral
    thesis: str
    playbook: str
    vega: str                 # long / short / flat
    theta: str                # positive / negative / flat
    risk: str                 # defined / undefined / none
    legs: list[Leg] = field(default_factory=list)
    expiry: str | None = None
    dte: int | None = None
    net: float | None = None            # + = debit paid, − = credit received ($/spread)
    max_profit: float | None = None     # None = unlimited
    max_loss: float | None = None       # None = undefined
    breakevens: list[float] = field(default_factory=list)
    profit_zone: str = ""               # above / below / inside / outside
    pop: float | None = None            # model probability of profit, 0..1
    credit_to_width: float | None = None
    manage: dict = field(default_factory=dict)
    sizing: dict = field(default_factory=dict)
    risk_form: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    ticker: str
    action: str
    headline: str             # the one line to read
    detail: str               # the exact order, in words
    confidence: float         # 0..1
    premium_state: str        # cheap / fair / rich / unknown
    premium_score: float | None
    bias: str
    bias_strength: str        # strong / weak / none
    plan: dict
    alternatives: list[dict] = field(default_factory=list)
    avoid: list[dict] = field(default_factory=list)
    why: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------- maths

def _norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _p_above(spot: float, strike: float, sigma: float) -> float | None:
    """P(S_T > strike) under a zero-drift lognormal with volatility `sigma`
    over the life of the trade. The −σ²/2 drift term is dropped: at these
    horizons it moves the answer by well under a point."""
    if sigma <= 0 or spot <= 0 or strike <= 0:
        return None
    return 1 - _norm_cdf(math.log(strike / spot) / sigma)


def pop_estimate(spot: float, breakevens: list[float], zone: str, sigma: float) -> float | None:
    """Probability of finishing in the profit zone, from the same model."""
    if not breakevens or sigma <= 0:
        return None
    if zone == "above":
        p = _p_above(spot, min(breakevens), sigma)
    elif zone == "below":
        p = _p_above(spot, max(breakevens), sigma)
        p = None if p is None else 1 - p
    elif zone in ("inside", "outside"):
        if len(breakevens) < 2:
            return None
        lo, hi = min(breakevens), max(breakevens)
        p_lo, p_hi = _p_above(spot, lo, sigma), _p_above(spot, hi, sigma)
        if p_lo is None or p_hi is None:
            return None
        inside = p_lo - p_hi
        p = inside if zone == "inside" else 1 - inside
    else:
        return None
    return None if p is None else round(max(0.0, min(1.0, p)), 3)


# -------------------------------------------------------------- chain helpers

def chain_side(view: OptionView, expiry: str, right: str) -> dict[float, Quote]:
    return (view.chain.get(expiry) or {}).get(right, {})


def strike_step(strikes) -> float:
    """Typical gap between consecutive strikes in this chain."""
    ks = sorted(strikes)
    if len(ks) < 2:
        return 1.0
    gaps = sorted(round(b - a, 4) for a, b in zip(ks, ks[1:]) if b > a)
    return gaps[len(gaps) // 2] if gaps else 1.0


def wing_strike(spot: float, short_strike: float, step: float, below: bool) -> float:
    """Where to put the protective long leg, as an absolute strike."""
    dist = abs(spot - short_strike)
    width = max(WING_MIN_STEPS * step, WING_WIDTH_FRAC * dist)
    width = min(width, WING_MAX_STEPS * step)
    return short_strike - width if below else short_strike + width


def pick_quote(view: OptionView, expiry: str, right: str, target: float,
          exclude: set[float] | None = None) -> Quote | None:
    """The real contract closest to `target`, skipping strikes already used."""
    side = chain_side(view, expiry, right)
    candidates = [k for k in side if k not in (exclude or set())]
    if not candidates:
        return None
    return side[min(candidates, key=lambda k: abs(k - target))]


def make_leg(action: str, q: Quote | None, expiry: str, qty: int = 1) -> Leg | None:
    if q is None:
        return None
    strike_txt = f"{q.strike:g}"
    return Leg(
        action=action, right=q.right, strike=q.strike, expiry=expiry, qty=qty,
        mid=q.mid, bid=q.bid, ask=q.ask, iv=q.iv, open_interest=q.open_interest,
        label=f"{action.title()} {qty}× {expiry} {strike_txt} {q.right}",
    )


def net_cost(legs: list[Leg]) -> float | None:
    """Net cost per spread in dollars. Positive = debit, negative = credit."""
    total = 0.0
    for leg in legs:
        if leg.mid is None:
            return None
        total += (leg.mid if leg.action == "buy" else -leg.mid) * leg.qty
    return round(total * 100, 2)


def sigma_to_expiry(view: OptionView, dte: int) -> float:
    """1σ move over the life of the expiry, as a fraction of spot."""
    return view.iv_annual / 100 * math.sqrt(max(dte, 1) / 365)


# ------------------------------------------------------------- plan assembly

def _manage_long() -> dict:
    return {
        "profit_target": "close at +50% to +100% of the debit",
        "profit_target_pct": 50,
        "stop": "cut at −50% of the debit",
        "stop_loss_pct": 50,
        "time_stop": "be out with ~7 days to expiry — theta accelerates into the last week",
        "close_by_dte": 7,
    }


def _manage_credit() -> dict:
    return {
        "profit_target": "buy it back at 50% of the credit received",
        "profit_target_pct": 50,
        "stop": "close if the loss reaches 2× the credit received",
        "stop_loss_multiple": 2.0,
        "time_stop": "close or roll at ~21 days to expiry — gamma risk rises fast after that",
        "close_by_dte": 21,
    }


def _manage_calendar() -> dict:
    return {
        "profit_target": "take 25–35% of the debit",
        "profit_target_pct": 30,
        "stop": "cut at −40% of the debit, or if the term structure flips back to contango",
        "stop_loss_pct": 40,
        "time_stop": "close at the front expiry — do not let the short leg go to assignment",
        "close_by_dte": 1,
    }


def size_position(plan: Plan, budget: float) -> dict:
    """How many spreads fit the risk budget, and what they tie up."""
    risk = plan.max_loss if plan.max_loss is not None else (plan.net if (plan.net or 0) > 0 else None)
    if not risk or risk <= 0:
        return {"risk_budget": budget, "contracts": None,
                "note": "Risk is undefined — size this by hand against your account, not a budget."}
    if risk > budget:
        return {
            "risk_budget": budget,
            "contracts": 0,
            "risk_per_spread": round(risk, 2),
            "total_risk": 0.0,
            "over_budget": True,
            "note": (f"One spread already risks ${risk:,.0f}, above the ${budget:,.0f} budget. "
                     "Narrow the wings, go further out of the money, or skip it."),
        }
    contracts = max(1, min(MAX_CONTRACTS, int(budget // risk)))
    return {
        "risk_budget": budget,
        "contracts": contracts,
        "risk_per_spread": round(risk, 2),
        "total_risk": round(contracts * risk, 2),
        "over_budget": False,
        "note": f"{contracts}× risks ${contracts * risk:,.0f} of a ${budget:,.0f} budget.",
    }


def resolve_risk_form(basis: str) -> dict:
    """{"tier", "note"} for a structure's ``risk_form`` basis (debit / credit /
    covered / shares / none). Shared with the long-dated engine."""
    tier, note = _RISK_FORM.get(basis, _RISK_FORM["none"])
    return {"tier": tier, "note": note}


def _finish(plan: Plan, view: OptionView, sigma: float, budget: float) -> Plan:
    plan.net = net_cost(plan.legs)
    plan.pop = pop_estimate(view.spot, plan.breakevens, plan.profit_zone, sigma)
    plan.risk_form = resolve_risk_form(plan.risk_form.get("basis", "none"))
    plan.sizing = size_position(plan, budget)
    plan.playbook = PLAYBOOK.get(plan.key, "")
    return plan


# ---- the individual structures ---------------------------------------------

def _long_straddle(view: OptionView, sigma: float) -> Plan | None:
    exp, dte = view.expiry, view.days_to_expiry
    call = pick_quote(view, exp, "call", view.spot)
    put = pick_quote(view, exp, "put", view.spot)
    legs = [x for x in (make_leg("buy", call, exp), make_leg("buy", put, exp)) if x]
    if len(legs) < 2:
        return None
    plan = Plan(
        key="long_straddle", name="Long Straddle", action="BUY_PREMIUM", bias="neutral",
        thesis="Options are cheap and the chart is coiled — pay for the move, either direction.",
        playbook="", vega="long", theta="negative", risk="defined",
        legs=legs, expiry=exp, dte=dte, profit_zone="outside",
        risk_form={"basis": "debit"}, manage=_manage_long(),
    )
    debit = net_cost(legs)
    if debit is not None:
        per_share = debit / 100
        plan.max_loss = debit
        plan.max_profit = None                     # unlimited to the upside
        plan.breakevens = [round(call.strike - per_share, 2), round(call.strike + per_share, 2)]
    return plan


def _long_strangle(view: OptionView, sigma: float) -> Plan | None:
    exp, dte = view.expiry, view.days_to_expiry
    call = pick_quote(view, exp, "call", view.spot * (1 + STRANGLE_SIGMA * sigma))
    put = pick_quote(view, exp, "put", view.spot * (1 - STRANGLE_SIGMA * sigma))
    legs = [x for x in (make_leg("buy", call, exp), make_leg("buy", put, exp)) if x]
    if len(legs) < 2 or call.strike <= put.strike:
        return None
    plan = Plan(
        key="long_strangle", name="Long Strangle", action="BUY_PREMIUM", bias="neutral",
        thesis="Cheap options plus a coiled chart — the budget version of the both-ways bet.",
        playbook="", vega="long", theta="negative", risk="defined",
        legs=legs, expiry=exp, dte=dte, profit_zone="outside",
        risk_form={"basis": "debit"}, manage=_manage_long(),
    )
    debit = net_cost(legs)
    if debit is not None:
        per_share = debit / 100
        plan.max_loss = debit
        plan.breakevens = [round(put.strike - per_share, 2), round(call.strike + per_share, 2)]
    return plan


def _debit_vertical(view: OptionView, sigma: float, bullish: bool) -> Plan | None:
    exp, dte = view.expiry, view.days_to_expiry
    right = "call" if bullish else "put"
    long_q = pick_quote(view, exp, right, view.spot)
    if long_q is None:
        return None
    target = view.spot * (1 + DEBIT_SHORT_SIGMA * sigma * (1 if bullish else -1))
    short_q = pick_quote(view, exp, right, target, exclude={long_q.strike})
    legs = [x for x in (make_leg("buy", long_q, exp), make_leg("sell", short_q, exp)) if x]
    if len(legs) < 2:
        return None
    width = abs(short_q.strike - long_q.strike)
    plan = Plan(
        key="bull_call_spread" if bullish else "bear_put_spread",
        name="Bull Call Spread" if bullish else "Bear Put Spread",
        action="BUY_PREMIUM", bias="bullish" if bullish else "bearish",
        thesis=("Options are reasonably priced and the break is "
                + ("up" if bullish else "down")
                + " — pay a capped premium for a capped move."),
        playbook="", vega="long", theta="negative", risk="defined",
        legs=legs, expiry=exp, dte=dte,
        profit_zone="above" if bullish else "below",
        risk_form={"basis": "debit"}, manage=_manage_long(),
    )
    debit = net_cost(legs)
    if debit is not None and debit > 0:
        plan.max_loss = debit
        plan.max_profit = round(width * 100 - debit, 2)
        be = long_q.strike + (debit / 100) * (1 if bullish else -1)
        plan.breakevens = [round(be, 2)]
    return plan


def _credit_vertical(view: OptionView, sigma: float, bullish: bool) -> Plan | None:
    """Bull put spread (bullish) / bear call spread (bearish)."""
    exp, dte = view.expiry, view.days_to_expiry
    right = "put" if bullish else "call"
    sign = -1 if bullish else 1
    short_q = pick_quote(view, exp, right, view.spot * (1 + sign * SHORT_SIGMA * sigma))
    if short_q is None:
        return None
    step = strike_step(chain_side(view, exp, right))
    wing_target = wing_strike(view.spot, short_q.strike, step, below=bullish)
    long_q = pick_quote(view, exp, right, wing_target, exclude={short_q.strike})
    legs = [x for x in (make_leg("sell", short_q, exp), make_leg("buy", long_q, exp)) if x]
    if len(legs) < 2:
        return None
    width = abs(short_q.strike - long_q.strike)
    plan = Plan(
        key="bull_put_spread" if bullish else "bear_call_spread",
        name="Bull Put Spread" if bullish else "Bear Call Spread",
        action="SELL_PREMIUM", bias="bullish" if bullish else "bearish",
        thesis=("Premium is rich and the lean is "
                + ("up" if bullish else "down")
                + " — get paid to be right about a direction you don't need to nail."),
        playbook="", vega="short", theta="positive", risk="defined",
        legs=legs, expiry=exp, dte=dte,
        profit_zone="above" if bullish else "below",
        risk_form={"basis": "credit"}, manage=_manage_credit(),
    )
    net = net_cost(legs)
    if net is not None and net < 0 and width > 0:
        credit = -net
        plan.max_profit = round(credit, 2)
        plan.max_loss = round(width * 100 - credit, 2)
        plan.credit_to_width = round(credit / (width * 100), 3)
        be = short_q.strike + (credit / 100) * (-1 if bullish else 1)
        plan.breakevens = [round(be, 2)]
    return plan


def _iron_condor(view: OptionView, sigma: float) -> Plan | None:
    exp, dte = view.expiry, view.days_to_expiry
    short_put = pick_quote(view, exp, "put", view.spot * (1 - SHORT_SIGMA * sigma))
    short_call = pick_quote(view, exp, "call", view.spot * (1 + SHORT_SIGMA * sigma))
    if short_put is None or short_call is None or short_call.strike <= short_put.strike:
        return None
    put_step = strike_step(chain_side(view, exp, "put"))
    call_step = strike_step(chain_side(view, exp, "call"))
    long_put = pick_quote(view, exp, "put",
                     wing_strike(view.spot, short_put.strike, put_step, below=True),
                     exclude={short_put.strike})
    long_call = pick_quote(view, exp, "call",
                      wing_strike(view.spot, short_call.strike, call_step, below=False),
                      exclude={short_call.strike})
    legs = [x for x in (make_leg("sell", short_put, exp), make_leg("buy", long_put, exp),
                        make_leg("sell", short_call, exp), make_leg("buy", long_call, exp)) if x]
    if len(legs) < 4:
        return None
    plan = Plan(
        key="iron_condor", name="Iron Condor", action="SELL_PREMIUM", bias="neutral",
        thesis="Premium is rich with no directional edge — collect it inside a defined-risk box.",
        playbook="", vega="short", theta="positive", risk="defined",
        legs=legs, expiry=exp, dte=dte, profit_zone="inside",
        risk_form={"basis": "credit"}, manage=_manage_credit(),
    )
    net = net_cost(legs)
    width = max(short_put.strike - long_put.strike, long_call.strike - short_call.strike)
    if net is not None and net < 0 and width > 0:
        credit = -net
        plan.max_profit = round(credit, 2)
        plan.max_loss = round(width * 100 - credit, 2)
        plan.credit_to_width = round(credit / (width * 100), 3)
        plan.breakevens = [round(short_put.strike - credit / 100, 2),
                           round(short_call.strike + credit / 100, 2)]
    return plan


def _short_strangle(view: OptionView, sigma: float) -> Plan | None:
    exp, dte = view.expiry, view.days_to_expiry
    put = pick_quote(view, exp, "put", view.spot * (1 - SHORT_SIGMA * sigma))
    call = pick_quote(view, exp, "call", view.spot * (1 + SHORT_SIGMA * sigma))
    legs = [x for x in (make_leg("sell", put, exp), make_leg("sell", call, exp)) if x]
    if len(legs) < 2 or call.strike <= put.strike:
        return None
    plan = Plan(
        key="short_strangle", name="Short Strangle", action="SELL_PREMIUM", bias="neutral",
        thesis="Premium is rich and liquid — collect the most of it, at open-ended risk.",
        playbook="", vega="short", theta="positive", risk="undefined",
        legs=legs, expiry=exp, dte=dte, profit_zone="inside",
        risk_form={"basis": "credit"}, manage=_manage_credit(),
    )
    net = net_cost(legs)
    if net is not None and net < 0:
        credit = -net
        plan.max_profit = round(credit, 2)
        plan.max_loss = None                       # open-ended
        plan.breakevens = [round(put.strike - credit / 100, 2),
                           round(call.strike + credit / 100, 2)]
    return plan


def _calendar(view: OptionView, sigma: float) -> Plan | None:
    """Sell the front expiry, buy the same strike further out."""
    exps = [e for e in view.chain if e != view.expiry]
    if not exps:
        return None
    back = exps[0]
    front_q = pick_quote(view, view.expiry, "call", view.spot)
    back_q = pick_quote(view, back, "call", front_q.strike if front_q else view.spot)
    if front_q is None or back_q is None or back_q.strike != front_q.strike:
        return None
    legs = [x for x in (make_leg("sell", front_q, view.expiry), make_leg("buy", back_q, back)) if x]
    if len(legs) < 2:
        return None
    back_dte = next((e["dte"] for e in view.expiries if e["date"] == back), None)
    plan = Plan(
        key="calendar_spread", name="Calendar Spread", action="NEUTRAL_INCOME", bias="neutral",
        thesis="The near expiry is priced richer than the far one — sell the front, own the back.",
        playbook="", vega="long", theta="positive", risk="defined",
        legs=legs, expiry=back, dte=back_dte, profit_zone="inside",
        risk_form={"basis": "credit"}, manage=_manage_calendar(),
    )
    debit = net_cost(legs)
    if debit is not None and debit > 0:
        plan.max_loss = debit
        plan.max_profit = None                     # depends on where vol lands at front expiry
        # Rough profit zone: the position works while price stays near the strike.
        plan.breakevens = [round(front_q.strike * (1 - sigma * 0.6), 2),
                           round(front_q.strike * (1 + sigma * 0.6), 2)]
    return plan


def _covered_call(view: OptionView, sigma: float) -> Plan | None:
    exp, dte = view.expiry, view.days_to_expiry
    call = pick_quote(view, exp, "call", view.spot * (1 + SHORT_SIGMA * sigma))
    call_leg = make_leg("sell", call, exp)
    if call_leg is None:
        return None
    shares = Leg(action="own", right="share", strike=None, expiry=None, qty=100,
                 mid=view.spot, bid=None, ask=None, iv=None, open_interest=None,
                 label=f"Own 100 shares at ~{view.spot:,.2f}")
    plan = Plan(
        key="covered_call", name="Covered Call", action="SELL_PREMIUM", bias="neutral",
        thesis="Rich premium against shares you already hold — income, with the upside capped.",
        playbook="", vega="short", theta="positive", risk="defined",
        legs=[shares, call_leg], expiry=exp, dte=dte, profit_zone="above",
        risk_form={"basis": "covered"}, manage=_manage_credit(),
    )
    if call.mid is not None:
        credit = round(call.mid * 100, 2)
        plan.net = -credit
        plan.max_profit = round((call.strike - view.spot) * 100 + credit, 2)
        plan.max_loss = round(view.spot * 100 - credit, 2)   # if it goes to zero
        plan.breakevens = [round(view.spot - call.mid, 2)]
    return plan


def _stand_aside(reason: str) -> Plan:
    return Plan(
        key="stand_aside", name="Stand aside", action="STAND_ASIDE", bias="neutral",
        thesis=reason, playbook=PLAYBOOK["stand_aside"], vega="flat", theta="flat",
        risk="none", risk_form={"tier": "n/a", "note": _RISK_FORM["none"][1]},
    )


def _no_data(reason: str) -> Plan:
    return Plan(
        key="no_data", name="Not priced", action="NO_DATA", bias="neutral",
        thesis=reason, playbook=PLAYBOOK["no_data"], vega="flat", theta="flat",
        risk="none", risk_form={"tier": "n/a", "note": _RISK_FORM["none"][1]},
    )


_BUILDERS = {
    "long_straddle": lambda v, s: _long_straddle(v, s),
    "long_strangle": lambda v, s: _long_strangle(v, s),
    "bull_call_spread": lambda v, s: _debit_vertical(v, s, True),
    "bear_put_spread": lambda v, s: _debit_vertical(v, s, False),
    "bull_put_spread": lambda v, s: _credit_vertical(v, s, True),
    "bear_call_spread": lambda v, s: _credit_vertical(v, s, False),
    "iron_condor": lambda v, s: _iron_condor(v, s),
    "short_strangle": lambda v, s: _short_strangle(v, s),
    "calendar_spread": lambda v, s: _calendar(v, s),
    "covered_call": lambda v, s: _covered_call(v, s),
}


# ------------------------------------------------------------ decision layer

def directional_bias(row: dict) -> tuple[str, str]:
    """(direction, strength). A released squeeze is the only *strong* signal;
    the momentum lean is a tiebreaker, never a thesis."""
    if row.get("squeeze_fired") and row.get("fired_dir") in ("up", "down"):
        return ("bullish" if row["fired_dir"] == "up" else "bearish"), "strong"
    lean = row.get("lean", "Neutral")
    if lean == "Bullish":
        return "bullish", "weak"
    if lean == "Bearish":
        return "bearish", "weak"
    return "neutral", "none"


def _earnings_inside(row: dict, dte: int | None) -> bool:
    days = row.get("earnings_in_days")
    if days is None or (isinstance(days, float) and math.isnan(days)) or days < 0:
        return False
    window = dte if dte else round(float(row.get("horizon_days", 10)) * 1.4)
    return float(days) <= window


def _choose(view: OptionView, row: dict, bias: str, strength: str,
            earnings_inside: bool, allow_undefined: bool
            ) -> tuple[str, list[str], list[dict], str]:
    """(primary key, alternative keys, [{'name','reason'}] to avoid, stand-aside reason).

    The reason travels with the decision: standing aside on a 24%-wide market and
    standing aside on fairly-priced options are different calls, and the card has
    to say which one it is."""
    state = view.premium_state
    score = float(row.get("score") or 0)
    directional = strength == "strong" or (strength == "weak" and score >= MIN_SCORE_FOR_FAIR_IV)
    bullish = bias == "bullish"

    # A market you can't get filled in is not a trade. "poor" means we *have*
    # depth data and it's bad; "unknown" means the feed gave us nothing, which
    # is a reason to keep the structure simple, not to refuse outright.
    if view.liquidity == "poor":
        wide = (f"the at-the-money bid/ask is ~{view.atm_spread_pct:.0f}% of mid"
                if view.atm_spread_pct is not None else "the at-the-money market is wide")
        return "stand_aside", [], [
            {"name": "Any multi-leg spread here",
             "reason": wide[0].upper() + wide[1:] +
                       " — slippage on entry and exit would cost more than the edge."}], (
            f"The options are too illiquid to trade: {wide}. Premium looks "
            f"{view.premium_state}, but you would give the edge back on the fills.")
    # Two legs max when depth is thin: every extra leg crosses another spread.
    two_leg_only = view.liquidity in ("fair", "unknown")

    if state == "cheap":
        avoid = [{"name": "Selling premium (condors, credit spreads)",
                  "reason": f"IV rank {view.iv_rank:.0f} — you'd collect too little for the risk."
                            if view.iv_rank is not None else
                            "Options are cheap here — selling them collects too little for the risk."}]
        if strength == "strong":
            primary = "bull_call_spread" if bullish else "bear_put_spread"
            alts = ["long_straddle", "long_strangle"]
        else:
            primary = "long_straddle"
            alts = ["long_strangle" if primary == "long_straddle" else "long_straddle"]
            if bias != "neutral":
                alts.append("bull_call_spread" if bullish else "bear_put_spread")
        return primary, alts, avoid, ""

    if state == "rich":
        avoid = [{"name": "Long straddles / strangles",
                  "reason": (f"IV rank {view.iv_rank:.0f} and the market is pricing a "
                             f"{view.implied_move_pct:.1f}% move vs {view.hist_move_pct:.1f}% realized "
                             "— you'd be paying up for the move you want.")
                            if view.iv_rank is not None else
                            "Premium is rich — buying it here means overpaying for the move."}]
        put_side = "bull_put_spread" if (bullish or view.skew_label == "put_skew") else "bear_call_spread"
        if directional:
            primary = "bull_put_spread" if bullish else "bear_call_spread"
            alts = [] if two_leg_only else ["iron_condor"]
        elif two_leg_only:
            # Half a condor: same short-vega trade, half the fills to chase.
            primary = put_side
            alts = ["bear_call_spread" if put_side == "bull_put_spread" else "bull_put_spread"]
            avoid.append({"name": "Iron condor",
                          "reason": "Four legs in a thin market — you'd pay the spread four times. "
                                    "Take one side instead."})
        else:
            primary = "iron_condor"
            alts = [put_side]
        if allow_undefined and view.liquidity == "good" and not earnings_inside:
            alts.append("short_strangle")
        else:
            avoid.append({"name": "Short strangle (naked)",
                          "reason": "Open-ended risk"
                                    + (" into an earnings gap." if earnings_inside else
                                       " — keep the wings on unless the chain is deep and you accept it.")})
        alts.append("covered_call")
        return primary, alts, avoid, ""

    # --- fair premium: no volatility edge either way ------------------------
    if view.term_structure == "backwardation" and strength != "strong":
        return "calendar_spread", ["iron_condor"], [
            {"name": "Long straddle",
             "reason": "The front month is the expensive part — buying it fights the term structure."}], ""
    if strength == "strong":
        primary = "bull_call_spread" if bullish else "bear_put_spread"
        return primary, ["bull_put_spread" if bullish else "bear_call_spread"], [], ""
    if score >= MIN_SCORE_FOR_FAIR_IV and row.get("squeeze_on"):
        return ("long_strangle", ["long_straddle"],
                [{"name": "Selling premium",
                  "reason": "A squeeze this tight can expand fast — bad time to be short vega."}], "")
    return "stand_aside", [], [], (
        "Premium is fairly priced and the chart isn't coiled enough to pay for a position.")


def _confidence(view: OptionView, row: dict, strength: str, plan: Plan,
                earnings_inside: bool) -> float:
    """0..1 — how much the inputs agree, not how likely the trade is to win."""
    if plan.action in ("STAND_ASIDE", "NO_DATA"):
        return 0.0
    # How far premium sits from the fair band (the primary edge).
    dist = abs(view.premium_score - 50) / 50
    conf = 0.30 + 0.35 * min(dist, 1.0)
    conf += 0.15 * min(float(row.get("score") or 0) / 100, 1.0)
    conf += {"strong": 0.10, "weak": 0.03, "none": 0.06}[strength]
    conf += {"good": 0.10, "fair": 0.04, "poor": -0.15, "unknown": 0.0}[view.liquidity]
    if view.iv_rank is None:
        conf -= 0.08                 # ranking IV without history is a weaker read
    if earnings_inside:
        conf -= 0.10
    if plan.net is None:
        conf -= 0.12                 # couldn't price the legs
    return round(max(0.0, min(1.0, conf)), 2)


def _why(view: OptionView, row: dict, bias: str, strength: str) -> list[str]:
    out = []
    if view.iv_rank is not None:
        out.append(f"IV rank {view.iv_rank:.0f}/100 ({view.premium_state} premium, blended score {view.premium_score:.0f}).")
    else:
        out.append(f"Premium score {view.premium_score:.0f}/100 — {view.premium_state}.")
    out.append(f"Options price a {view.implied_move_pct:.1f}% move over the horizon vs "
               f"{view.hist_move_pct:.1f}% realized"
               + (f" ({view.iv_hv_ratio:.2f}× IV/HV)." if view.iv_hv_ratio else "."))
    if row.get("squeeze_fired"):
        out.append(f"The squeeze just released to the {row.get('fired_dir') or 'side'} — that release is the trigger.")
    elif row.get("squeeze_on"):
        out.append(f"Squeeze on for {int(row.get('squeeze_days') or 0)} days — still building, not yet fired.")
    out.append(f"Setup score {float(row.get('score') or 0):.0f}/100"
               + (f", direction {bias} ({strength})." if bias != "neutral" else ", no directional edge."))
    if view.term_structure in ("backwardation", "contango"):
        out.append(f"Term structure in {view.term_structure} ({view.term_slope:+.1%} front→back)."
                   if view.term_slope is not None else f"Term structure in {view.term_structure}.")
    if view.skew_label == "put_skew":
        out.append(f"Puts bid over calls by {view.skew:.1f} vol points — the put side pays more to sell.")
    elif view.skew_label == "call_skew":
        out.append(f"Calls bid over puts by {abs(view.skew):.1f} vol points — upside is the expensive side.")
    return out


def _warnings(view: OptionView, row: dict, plan: Plan, earnings_inside: bool) -> list[str]:
    out = []
    days = row.get("earnings_in_days")
    if earnings_inside and days is not None and not (isinstance(days, float) and math.isnan(days)):
        if plan.vega == "long":
            out.append(f"Earnings in {int(days)} days, inside this expiry: part of the premium is event "
                       "premium, and IV collapses the morning after the print. Being right on direction "
                       "may still lose money.")
        elif plan.vega == "short":
            out.append(f"Earnings in {int(days)} days, inside this expiry: the IV crush is the trade, "
                       "but a gap through your short strike is the risk. Keep the wings on and size small.")
        else:
            out.append(f"Earnings in {int(days)} days, inside this expiry — know the catalyst before entering.")
    if view.liquidity == "poor":
        out.append(f"Thin options market (ATM spread ~{view.atm_spread_pct:.0f}% of mid"
                   f"{f', OI {view.atm_open_interest:,}' if view.atm_open_interest is not None else ''}) "
                   "— the bid/ask will eat a multi-leg spread. Use limit orders at mid, or skip it."
                   if view.atm_spread_pct is not None else
                   "Thin options market — the bid/ask will eat a multi-leg spread.")
    elif view.liquidity == "unknown":
        out.append("No bid/ask depth on the ATM contract — treat the pricing below as indicative.")
    if plan.risk == "undefined":
        out.append("Undefined risk: the loss on this structure is open-ended. Only trade it with a hard "
                   "mental stop and margin you can afford to lose.")
    if plan.credit_to_width is not None and plan.credit_to_width < MIN_CREDIT_TO_WIDTH:
        out.append(f"The credit is only {plan.credit_to_width:.0%} of the spread width — thin compensation. "
                   "Widen the wings or pass.")
    if plan.net is None and plan.legs:
        out.append("Some legs had no two-sided market, so the net price, max loss and probability "
                   "below could not be computed. Price it in your broker before deciding.")
    if row.get("note"):
        out.append(f"Data note: {row['note']}.")
    return out


def _order_text(plan: Plan) -> str:
    """The exact order, in words."""
    if plan.action in ("STAND_ASIDE", "NO_DATA"):
        return plan.thesis
    parts = [leg.label for leg in plan.legs]
    text = "; ".join(parts)
    if plan.net is not None:
        text += (f" — net debit ${plan.net:,.2f}" if plan.net > 0
                 else f" — net credit ${-plan.net:,.2f}") + " per spread"
    return text


def _headline(plan: Plan, view: OptionView, ticker: str) -> str:
    verb = {
        "BUY_PREMIUM": "BUY premium",
        "SELL_PREMIUM": "SELL premium",
        "NEUTRAL_INCOME": "Collect time decay",
        "STAND_ASIDE": "STAND ASIDE",
        "NO_DATA": "Not priced",
    }[plan.action]
    if plan.action == "STAND_ASIDE":
        return f"{ticker}: stand aside — {plan.thesis[0].lower()}{plan.thesis[1:]}"
    if plan.action == "NO_DATA":
        return f"{ticker}: {plan.thesis}"
    rank = f"IV rank {view.iv_rank:.0f}" if view.iv_rank is not None else f"premium {view.premium_score:.0f}/100"
    return f"{ticker}: {verb} — {rank}, {view.premium_state} → {plan.name}"


# ------------------------------------------------------------------ entry point

def recommend(row: dict, view: OptionView | None, risk_budget: float = 500.0,
              allow_undefined_risk: bool = False) -> Recommendation:
    """The single instruction for one ticker: what to do, and with which legs."""
    ticker = str(row.get("ticker", "?"))

    if view is None:
        plan = _no_data("No option chain was priced for this ticker in this run "
                        "(only the top-ranked names are priced).")
        return Recommendation(
            ticker=ticker, action=plan.action, headline=_no_data_headline(ticker),
            detail=plan.thesis, confidence=0.0, premium_state="unknown", premium_score=None,
            bias=directional_bias(row)[0], bias_strength=directional_bias(row)[1], plan=plan.as_dict(),
            why=[f"Setup score {float(row.get('score') or 0):.0f}/100 from price action alone — "
                 "no implied-volatility read, so no premium call can be made."],
            warnings=["Without IV there is no way to say whether options here are cheap or rich. "
                      "Raise `options.top_n` in the config to price more names."],
        )

    bias, strength = directional_bias(row)
    sigma = sigma_to_expiry(view, view.days_to_expiry)
    earnings_inside = _earnings_inside(row, view.days_to_expiry)

    primary_key, alt_keys, avoid, aside_reason = _choose(view, row, bias, strength,
                                                         earnings_inside, allow_undefined_risk)

    def build(key: str) -> Plan | None:
        builder = _BUILDERS.get(key)
        if builder is None:
            return None
        plan = builder(view, sigma)
        return _finish(plan, view, sigma, risk_budget) if plan else None

    plan = build(primary_key) if primary_key != "stand_aside" else None
    if plan is None:
        reason = aside_reason or (
            f"Could not build a usable {primary_key.replace('_', ' ')} from the live chain "
            "(missing strikes, or no two-sided market at the strikes this setup needs).")
        plan = _stand_aside(reason)
        plan.sizing = {"risk_budget": risk_budget, "contracts": 0}

    alternatives = [p.as_dict() for k in alt_keys if (p := build(k)) is not None][:3]

    conf = _confidence(view, row, strength, plan, earnings_inside)
    return Recommendation(
        ticker=ticker,
        action=plan.action,
        headline=_headline(plan, view, ticker),
        detail=_order_text(plan),
        confidence=conf,
        premium_state=view.premium_state,
        premium_score=view.premium_score,
        bias=bias,
        bias_strength=strength,
        plan=plan.as_dict(),
        alternatives=alternatives,
        avoid=avoid,
        why=_why(view, row, bias, strength),
        warnings=_warnings(view, row, plan, earnings_inside),
    )


def _no_data_headline(ticker: str) -> str:
    return f"{ticker}: not priced — no IV read this run"


def recommend_all(rows: list[dict], views: dict[str, OptionView],
                  risk_budget: float = 500.0,
                  allow_undefined_risk: bool = False) -> dict[str, dict]:
    """{ticker: recommendation dict} for every scanned row."""
    out: dict[str, dict] = {}
    for row in rows:
        ticker = str(row.get("ticker", ""))
        if not ticker:
            continue
        out[ticker] = recommend(row, views.get(ticker), risk_budget,
                                allow_undefined_risk).as_dict()
    return out
