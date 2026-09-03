"""Options / implied-volatility layer — the input the strategy engine runs on.

The scanner tells you a move is *loading*. This module tells you what the
**option market is charging** for that move, which is what decides whether you
should be a **buyer** or a **seller** of premium:

    low IV  -> options are cheap  -> BUY premium  (straddles, debit spreads)
    high IV -> options are rich   -> SELL premium (credit spreads, condors)

For each ticker we read the option chain and derive:

* **ATM IV** — average call/put implied vol at the strike nearest spot.
* **Implied move** — ``ATM IV × √(horizon/252)``, directly comparable to the
  scanner's historical (HV-based) expected move.
* **IV Rank / IV Percentile** — where today's IV sits versus the trailing year.
  Free data sources don't publish historical IV, so we rank IV against the
  ticker's own trailing **realized-vol** history: IV is a forecast of forward
  realized vol, so the realized-vol distribution is the honest yardstick. Both
  numbers are therefore *proxies* and read a little high, because implied vol
  carries a persistent risk premium over realized (see ``vrp``).
* **VRP** — the volatility risk premium, ``IV − HV`` in vol points, plus the
  ``IV/HV`` ratio. This is the part of "rich" that isn't a regime call.
* **Term structure** — front-expiry IV vs a further-dated one. Contango (back >
  front) is the normal state; **backwardation** means the market is pricing an
  imminent event, which favours selling the front month / calendars.
* **Skew** — OTM put IV minus OTM call IV. Fat put skew makes put-side credit
  spreads pay better than the call side.
* **Liquidity** — ATM bid/ask spread and open interest. A four-legged condor in
  a 20%-wide market is a losing trade before it starts, so this gates how many
  legs the strategy engine is allowed to recommend.
* **Chain snapshot** — real strikes and quotes near the money, kept in memory so
  the strategy engine can price exact legs instead of guessing.

Network cost is a handful of calls per ticker, so callers should run this only
on the most coiled names (the scanner passes the top N). Everything fails soft:
a ticker without a usable chain simply gets ``None``.
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field

import yfinance as yf

TRADING_DAYS = 252

# Premium-state cutoffs on the 0..100 blended premium score (see `premium_score`).
CHEAP_BELOW = 35.0
RICH_ABOVE = 65.0

# Liquidity tiers, from the ATM bid/ask spread as a % of mid.
SPREAD_GOOD = 6.0
SPREAD_FAIR = 15.0
OI_GOOD = 500
OI_FAIR = 100

# --- the long-dated (LEAPS) expiry -------------------------------------------
# "13 months out" is a target, not a listing: exchanges list LEAPS on January
# cycles plus a few quarterlies, so the nearest real expiry to 395 days can sit
# anywhere from ~9 to ~18 months out. We take the closest listed expiry inside
# that window and report its true DTE rather than pretending it is 13 months.
LONG_TARGET_DAYS = 395
LONG_MIN_DTE = 270
LONG_MAX_DTE = 550


@dataclass
class Quote:
    """One option contract's market snapshot."""
    strike: float
    right: str                # "call" / "put"
    bid: float | None
    ask: float | None
    mid: float | None
    last: float | None
    iv: float | None          # annualized, %
    open_interest: int | None
    volume: int | None

    @property
    def spread_pct(self) -> float | None:
        if self.bid is None or self.ask is None or not self.mid:
            return None
        return (self.ask - self.bid) / self.mid * 100


@dataclass
class OptionView:
    """Everything we know about a ticker's option market right now."""
    ticker: str
    spot: float

    # --- headline volatility ------------------------------------------------
    iv_annual: float            # ATM implied vol, annualized %
    hv_annual: float | None     # realized vol, annualized %
    implied_move_pct: float     # implied move over the horizon, % of spot
    hist_move_pct: float        # historical expected move over the horizon, %
    iv_rank: float | None       # 0..100, IV within its trailing range (proxy)
    iv_percentile: float | None # 0..100, % of trailing readings below IV (proxy)
    vrp: float | None           # IV − HV, vol points
    iv_hv_ratio: float | None   # IV / HV
    verdict: str                # cheap / fair / rich  (implied vs historical move)
    premium_score: float        # 0..100 blended richness of premium
    premium_state: str          # cheap / fair / rich  (the blended call)

    # --- structure ----------------------------------------------------------
    term_slope: float | None    # (back IV − front IV) / front IV
    term_structure: str         # contango / flat / backwardation / unknown
    skew: float | None          # OTM put IV − OTM call IV, vol points
    skew_label: str             # put_skew / balanced / call_skew / unknown

    # --- tradability --------------------------------------------------------
    atm_spread_pct: float | None
    atm_open_interest: int | None
    liquidity: str              # good / fair / poor / unknown

    # --- the chain we priced off -------------------------------------------
    expiry: str
    days_to_expiry: int

    # --- the long-dated expiry the LEAPS spreads are built on ----------------
    long_expiry: str | None = None
    long_dte: int | None = None
    long_iv: float | None = None          # ATM IV on that expiry, annualized %
    long_spread_pct: float | None = None  # ATM bid/ask as % of mid, that expiry
    long_open_interest: int | None = None
    long_liquidity: str = "unknown"
    expiries: list[dict] = field(default_factory=list)   # [{"date","dte"}]
    # {expiry: {"call": {strike: Quote}, "put": {strike: Quote}}} — in-memory
    # only; the strategy engine prices legs off it, it never reaches the JSON.
    chain: dict = field(default_factory=dict, repr=False)

    def as_dict(self) -> dict:
        """JSON-safe view (drops the raw chain)."""
        return {
            "iv_annual": self.iv_annual,
            "hv_annual": self.hv_annual,
            "implied_move_pct": self.implied_move_pct,
            "hist_move_pct": self.hist_move_pct,
            "iv_rank": self.iv_rank,
            "iv_percentile": self.iv_percentile,
            "vrp": self.vrp,
            "iv_hv_ratio": self.iv_hv_ratio,
            "verdict": self.verdict,
            "premium_score": self.premium_score,
            "premium_state": self.premium_state,
            "term_slope": self.term_slope,
            "term_structure": self.term_structure,
            "skew": self.skew,
            "skew_label": self.skew_label,
            "atm_spread_pct": self.atm_spread_pct,
            "atm_open_interest": self.atm_open_interest,
            "liquidity": self.liquidity,
            "expiry": self.expiry,
            "days_to_expiry": self.days_to_expiry,
            "expiries": self.expiries,
            "long_expiry": self.long_expiry,
            "long_dte": self.long_dte,
            "long_iv": self.long_iv,
            "long_spread_pct": self.long_spread_pct,
            "long_open_interest": self.long_open_interest,
            "long_liquidity": self.long_liquidity,
        }


# --------------------------------------------------------------------- helpers

def _f(v) -> float | None:
    """float() that turns NaN / None / junk into None."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _i(v) -> int | None:
    x = _f(v)
    return int(x) if x is not None else None


def _dte(expiry: str, today: dt.date | None = None) -> int | None:
    try:
        return (dt.date.fromisoformat(expiry) - (today or dt.date.today())).days
    except (ValueError, TypeError):
        return None


def _nearest_expiry(expiries: list[str], target_days: int) -> tuple[str, int] | None:
    """Pick the expiry closest to `target_days` calendar days out (future only)."""
    dated = [(e, d) for e in expiries if (d := _dte(e)) is not None and d >= 1]
    if not dated:
        return None
    return min(dated, key=lambda ed: abs(ed[1] - target_days))


def _long_expiry(expiries: list[str], target_days: int = LONG_TARGET_DAYS,
                 lo: int = LONG_MIN_DTE, hi: int = LONG_MAX_DTE) -> tuple[str, int] | None:
    """The listed expiry closest to `target_days`, restricted to [lo, hi] days.

    Returns None when the name simply has no LEAPS that far out — common for
    small caps and for most ETFs' back months. Better to say "no long-dated
    chain" than to build a 13-month plan on a 4-month contract."""
    dated = [(e, d) for e in expiries if (d := _dte(e)) is not None and lo <= d <= hi]
    if not dated:
        return None
    return min(dated, key=lambda ed: abs(ed[1] - target_days))


def _quotes(leg, right: str) -> dict[float, Quote]:
    """Turn one side of a yfinance option chain into {strike: Quote}."""
    out: dict[float, Quote] = {}
    if leg is None or getattr(leg, "empty", True):
        return out
    for row in leg.to_dict("records"):
        strike = _f(row.get("strike"))
        if strike is None:
            continue
        bid, ask = _f(row.get("bid")), _f(row.get("ask"))
        last = _f(row.get("lastPrice"))
        if bid is not None and ask is not None and ask >= bid > 0:
            mid = round((bid + ask) / 2, 4)
        else:
            mid = last if (last or 0) > 0 else None
        iv = _f(row.get("impliedVolatility"))
        out[strike] = Quote(
            strike=strike, right=right, bid=bid, ask=ask, mid=mid, last=last,
            iv=round(iv * 100, 2) if iv and iv > 0 else None,
            open_interest=_i(row.get("openInterest")),
            volume=_i(row.get("volume")),
        )
    return out


def _nearest_strike(strikes, target: float) -> float | None:
    strikes = [s for s in strikes if s is not None]
    return min(strikes, key=lambda s: abs(s - target)) if strikes else None


def _atm_iv(calls: dict[float, Quote], puts: dict[float, Quote], spot: float) -> float | None:
    """Average call/put IV at the strike nearest spot (%). None if unusable."""
    ivs = []
    for side in (calls, puts):
        k = _nearest_strike(side, spot)
        if k is None:
            continue
        iv = side[k].iv
        if iv and iv > 0:
            ivs.append(iv)
    return round(sum(ivs) / len(ivs), 2) if ivs else None


# ------------------------------------------------------------- vol regime math

def iv_rank(iv_pct: float, hv_history: list[float] | None) -> float | None:
    """IV Rank (0..100): where IV sits between the low and high of the trailing
    realized-vol range. Classic ``(IV − min) / (max − min)``, clamped."""
    hist = [h for h in (hv_history or []) if h is not None and math.isfinite(h)]
    if len(hist) < 20:
        return None
    lo, hi = min(hist), max(hist)
    if hi <= lo:
        return None
    return round(max(0.0, min(1.0, (iv_pct - lo) / (hi - lo))) * 100, 1)


def iv_percentile(iv_pct: float, hv_history: list[float] | None) -> float | None:
    """IV Percentile (0..100): share of trailing realized-vol readings below IV."""
    hist = [h for h in (hv_history or []) if h is not None and math.isfinite(h)]
    if len(hist) < 20:
        return None
    return round(sum(1 for h in hist if h <= iv_pct) / len(hist) * 100, 1)


def _ratio_component(ratio: float | None) -> float:
    """Map IV/HV onto 0..1 richness. 0.8x -> 0 (cheap), ~1.15x -> 0.5 (normal
    risk premium), 1.6x+ -> 1 (very rich). Piecewise-linear, no data -> 0.5."""
    if ratio is None:
        return 0.5
    points = [(0.80, 0.0), (1.00, 0.30), (1.15, 0.50), (1.35, 0.80), (1.60, 1.0)]
    if ratio <= points[0][0]:
        return 0.0
    if ratio >= points[-1][0]:
        return 1.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= ratio <= x1:
            return y0 + (y1 - y0) * (ratio - x0) / (x1 - x0)
    return 0.5


def _term_component(slope: float | None) -> float:
    """Backwardation (front richer than back) pushes richness up; steep contango
    pulls it down. Flat/unknown is neutral."""
    if slope is None:
        return 0.5
    if slope <= -0.05:
        return 0.85
    if slope >= 0.10:
        return 0.30
    # linear between -0.05 -> 0.85 and 0.10 -> 0.30
    return 0.85 + (0.30 - 0.85) * (slope + 0.05) / 0.15


def premium_score(rank: float | None, ratio: float | None, slope: float | None) -> float:
    """Blend IV rank (regime), IV/HV (risk premium) and term structure into one
    0..100 "how rich is premium here" number. Higher = better to be a seller."""
    rank_c = 0.5 if rank is None else rank / 100
    raw = 0.45 * rank_c + 0.40 * _ratio_component(ratio) + 0.15 * _term_component(slope)
    return round(max(0.0, min(1.0, raw)) * 100, 1)


def premium_state(score: float) -> str:
    if score < CHEAP_BELOW:
        return "cheap"
    if score > RICH_ABOVE:
        return "rich"
    return "fair"


def classify_verdict(implied_pct: float, hist_pct: float, margin: float) -> str:
    """cheap if implied < hist·(1−margin), rich if implied > hist·(1+margin)."""
    if hist_pct and implied_pct < hist_pct * (1 - margin):
        return "cheap"
    if hist_pct and implied_pct > hist_pct * (1 + margin):
        return "rich"
    return "fair"


def classify_term(slope: float | None) -> str:
    if slope is None:
        return "unknown"
    if slope <= -0.03:
        return "backwardation"
    if slope >= 0.03:
        return "contango"
    return "flat"


def classify_skew(skew: float | None) -> str:
    if skew is None:
        return "unknown"
    if skew >= 3.0:
        return "put_skew"
    if skew <= -3.0:
        return "call_skew"
    return "balanced"


def classify_liquidity(spread_pct: float | None, open_interest: int | None) -> str:
    """Can you actually trade this chain? Wide markets kill multi-leg spreads."""
    if spread_pct is None and open_interest is None:
        return "unknown"
    sp = spread_pct if spread_pct is not None else SPREAD_FAIR
    oi = open_interest if open_interest is not None else OI_FAIR
    if sp <= SPREAD_GOOD and oi >= OI_GOOD:
        return "good"
    if sp <= SPREAD_FAIR and oi >= OI_FAIR:
        return "fair"
    return "poor"


def _skew(calls: dict[float, Quote], puts: dict[float, Quote], spot: float,
          move_frac: float) -> float | None:
    """OTM put IV − OTM call IV, sampled ~1σ either side of spot (a cheap
    stand-in for 25-delta skew, which needs greeks free feeds don't give us)."""
    if move_frac <= 0:
        return None
    put_k = _nearest_strike([k for k in puts if k < spot], spot * (1 - move_frac))
    call_k = _nearest_strike([k for k in calls if k > spot], spot * (1 + move_frac))
    if put_k is None or call_k is None:
        return None
    pv, cv = puts[put_k].iv, calls[call_k].iv
    if not pv or not cv:
        return None
    return round(pv - cv, 2)


# ------------------------------------------------------------------- main entry

def implied_view(
    ticker: str,
    spot: float,
    hist_move_pct: float,
    horizon_days: int,
    margin: float = 0.15,
    hv_annual: float | None = None,
    hv_history: list[float] | None = None,
    strike_window: float = 3.0,
    fetch_expiries: int = 2,
    long_dated: bool = True,
    long_target_days: int = LONG_TARGET_DAYS,
) -> OptionView | None:
    """Read `ticker`'s option chain and build the full volatility picture.

    `hv_annual` / `hv_history` come from the scanner (annualized %, the trailing
    year of readings) and drive the IV rank / percentile / VRP numbers.
    `strike_window` is how many expiry-sigmas of strikes to keep in the snapshot.
    `long_dated` adds a third chain ~13 months out for the LEAPS spread engine.
    """
    try:
        tk = yf.Ticker(ticker)
        expiries = [e for e in (tk.options or []) if _dte(e) is not None and _dte(e) >= 1]
    except Exception:
        return None
    if not expiries:
        return None

    # Front expiry: just past the horizon, so it still covers the move. Back
    # expiry: ~2 months out, the reference leg for the term-structure read.
    front = _nearest_expiry(expiries, target_days=round(horizon_days * 1.4) + 7)
    if front is None:
        return None
    expiry, dte = front

    chain: dict[str, dict[str, dict[float, Quote]]] = {}
    wanted = [expiry]
    back = _nearest_expiry([e for e in expiries if e != expiry], target_days=60)
    if back and fetch_expiries > 1:
        wanted.append(back[0])

    # One more chain, ~13 months out, for the long-dated spreads. It costs a
    # third call per ticker, so it is opt-out.
    long_exp = _long_expiry(expiries, target_days=long_target_days) if long_dated else None
    if long_exp and long_exp[0] not in wanted:
        wanted.append(long_exp[0])

    for exp in wanted:
        try:
            ch = tk.option_chain(exp)
        except Exception:
            continue
        chain[exp] = {"call": _quotes(ch.calls, "call"), "put": _quotes(ch.puts, "put")}

    if expiry not in chain:
        return None
    calls, puts = chain[expiry]["call"], chain[expiry]["put"]

    iv = _atm_iv(calls, puts, spot)
    if iv is None:
        return None

    # Scale the annualized IV to the scanner's horizon so it lines up 1:1 with
    # the historical expected move.
    implied_move = iv * math.sqrt(horizon_days / TRADING_DAYS)
    # Sigma over the life of the *front expiry* — used for strike windows,
    # probability estimates and the skew sampling points.
    expiry_sigma = iv / 100 * math.sqrt(max(dte, 1) / 365)

    ratio = round(iv / hv_annual, 2) if hv_annual else None
    vrp = round(iv - hv_annual, 2) if hv_annual else None

    # Term structure from the back expiry's own ATM IV.
    slope = None
    if back and back[0] in chain:
        biv = _atm_iv(chain[back[0]]["call"], chain[back[0]]["put"], spot)
        if biv and iv:
            slope = round((biv - iv) / iv, 4)

    atm_k = _nearest_strike(calls, spot)
    atm_q = calls.get(atm_k) if atm_k is not None else None
    atm_spread = round(atm_q.spread_pct, 1) if atm_q and atm_q.spread_pct is not None else None
    atm_oi = atm_q.open_interest if atm_q else None

    rank = iv_rank(iv, hv_history)
    pctile = iv_percentile(iv, hv_history)
    score = premium_score(rank, ratio, slope)

    # Trim the snapshot to strikes the strategy engine could plausibly use.
    # The window is scaled per expiry: a 13-month chain needs far wider strikes
    # than the front month, and trimming it to the front month's sigma would cut
    # away exactly the deep-ITM and far-OTM legs the LEAPS structures need.
    def _trim(exp: str, sides: dict) -> dict:
        exp_dte = _dte(exp) or dte
        sig = iv / 100 * math.sqrt(max(exp_dte, 1) / 365)
        lo, hi = spot * (1 - strike_window * sig), spot * (1 + strike_window * sig)
        return {right: {k: q for k, q in side.items() if lo <= k <= hi}
                for right, side in sides.items()}

    trimmed = {exp: _trim(exp, sides) for exp, sides in chain.items()}

    # The long-dated ATM contract's own liquidity: LEAPS quote much wider than
    # the front month, and that spread is paid twice on a spread you hold for a
    # year. Read it off the real chain rather than assuming the front month's.
    long_expiry = long_dte = long_iv = long_spread = long_oi = None
    long_liq = "unknown"
    if long_exp and long_exp[0] in trimmed:
        long_expiry, long_dte = long_exp
        lcalls, lputs = trimmed[long_expiry]["call"], trimmed[long_expiry]["put"]
        long_iv = _atm_iv(lcalls, lputs, spot)
        lk = _nearest_strike(lcalls, spot)
        lq = lcalls.get(lk) if lk is not None else None
        if lq is not None:
            long_spread = round(lq.spread_pct, 1) if lq.spread_pct is not None else None
            long_oi = lq.open_interest
        long_liq = classify_liquidity(long_spread, long_oi)

    return OptionView(
        ticker=ticker,
        spot=round(spot, 2),
        iv_annual=iv,
        hv_annual=round(hv_annual, 1) if hv_annual else None,
        implied_move_pct=round(implied_move, 2),
        hist_move_pct=round(hist_move_pct, 2),
        iv_rank=rank,
        iv_percentile=pctile,
        vrp=vrp,
        iv_hv_ratio=ratio,
        verdict=classify_verdict(implied_move, hist_move_pct, margin),
        premium_score=score,
        premium_state=premium_state(score),
        term_slope=slope,
        term_structure=classify_term(slope),
        skew=(sk := _skew(calls, puts, spot, expiry_sigma)),
        skew_label=classify_skew(sk),
        atm_spread_pct=atm_spread,
        atm_open_interest=atm_oi,
        liquidity=classify_liquidity(atm_spread, atm_oi),
        expiry=expiry,
        days_to_expiry=dte,
        expiries=[{"date": e, "dte": _dte(e)} for e in expiries[:12]],
        long_expiry=long_expiry,
        long_dte=long_dte,
        long_iv=long_iv,
        long_spread_pct=long_spread,
        long_open_interest=long_oi,
        long_liquidity=long_liq,
        chain=trimmed,
    )


def screen_options(
    rows: list[tuple[str, float, float]],
    horizon_days: int,
    margin: float = 0.15,
    hv_annual: dict[str, float] | None = None,
    hv_history: dict[str, list[float]] | None = None,
    long_dated: bool = True,
    long_target_days: int = LONG_TARGET_DAYS,
) -> dict[str, OptionView]:
    """`rows` = [(ticker, spot, hist_move_pct)]. Returns {ticker: OptionView}."""
    out: dict[str, OptionView] = {}
    for ticker, spot, hist in rows:
        view = implied_view(
            ticker, spot, hist, horizon_days, margin,
            hv_annual=(hv_annual or {}).get(ticker),
            hv_history=(hv_history or {}).get(ticker),
            long_dated=long_dated,
            long_target_days=long_target_days,
        )
        if view is not None:
            out[ticker] = view
    return out
