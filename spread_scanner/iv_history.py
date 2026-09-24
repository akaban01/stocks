"""Implied-volatility history — the record the strategy has to be tested against.

The Setup-Score backtest measures a forward move against the stock's own
*realized*-vol band. That is the chart's thesis, not the trade's: a long
straddle bought on a coiled, cheap name pays only if the stock then moves more
than the option market charged, and free data publishes no history of what it
charged. So the scan writes that history itself, one row per priced name per
day, and `implied_backtest` measures the forward move against it once enough of
it has matured.

Only rows whose at-the-money IV is plausible are written — outside market hours
the feed returns floor IVs with no quotes, and a history poisoned with those
would test the strategy against numbers nobody traded.

The file is CSV and append-only in spirit: a rerun on the same date replaces
that date's rows rather than doubling them.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

import pandas as pd

# `role`: "top" for the names the scan prices to trade, "control" for the
# lowest-scoring names priced only so the test has calm names to compare with.
# Older rows predate the column and read as empty, which means "top".
COLUMNS = ["date", "ticker", "spot", "expiry", "dte", "iv_annual", "implied_move_pct",
           "hist_move_pct", "hv_annual", "premium_score", "premium_state", "score", "role"]

# Fewer matured observations than this and the implied test reports what it has
# without a verdict. Thirty names a day reach it in about two weeks of history
# past the horizon; the interval that comes with it says how little that is.
MIN_MATURED = 60

# ATM straddle ≈ 0.8 × spot × σ√t (Brenner–Subrahmanyam), i.e. √(2/π) of the
# one-sigma move. Used to turn an implied move into what a straddle cost.
STRADDLE_FACTOR = math.sqrt(2 / math.pi)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def rows_from_views(dates: dict[str, str], views: dict, scores: dict[str, float] | None = None,
                    min_iv: float = 0.0, role: str = "top") -> list[dict]:
    """One row per OptionView with a plausible ATM IV.

    `dates` is each ticker's last price bar (ISO date): the close the IV was
    read against, which is where `matured_outcomes` starts the clock. A name
    with no date is skipped rather than stamped with a guess."""
    out = []
    for ticker, v in sorted(views.items()):
        iv = _num(getattr(v, "iv_annual", None))
        if iv is None or iv < min_iv or not dates.get(ticker):
            continue
        out.append({
            "date": dates[ticker], "ticker": ticker, "spot": v.spot, "expiry": v.expiry,
            "dte": v.days_to_expiry, "iv_annual": iv, "implied_move_pct": v.implied_move_pct,
            "hist_move_pct": v.hist_move_pct, "hv_annual": v.hv_annual,
            "premium_score": v.premium_score, "premium_state": v.premium_state,
            "score": (scores or {}).get(ticker),
            "role": role,
        })
    return out


def load(path: str | Path) -> pd.DataFrame:
    """The history as a frame (empty, with the columns, when there is none)."""
    try:
        df = pd.read_csv(path, dtype={"date": str, "ticker": str, "expiry": str,
                                      "premium_state": str})
    except (FileNotFoundError, pd.errors.EmptyDataError):
        return pd.DataFrame(columns=COLUMNS)
    return df


def append(path: str | Path, rows: list[dict]) -> int:
    """Merge `rows` into the history at `path`, replacing any rows already there
    for the same (date, ticker). Returns the number of rows written."""
    if not rows:
        return 0
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    old = load(path)
    new = pd.DataFrame(rows, columns=COLUMNS)
    if not old.empty:
        keys = set(zip(new["date"], new["ticker"]))
        keep = [(d, t) not in keys for d, t in zip(old["date"], old["ticker"])]
        new = pd.concat([old[keep], new], ignore_index=True)
    new = new.sort_values(["date", "ticker"]).reset_index(drop=True)
    new.to_csv(path, index=False, columns=COLUMNS, quoting=csv.QUOTE_MINIMAL)
    return len(rows)


def matured_outcomes(hist: pd.DataFrame, prices: dict[str, pd.DataFrame],
                     horizon: int) -> pd.DataFrame:
    """Join each logged row to the realized move over the next `horizon` bars.

    The entry is the close on the logged date (the scan runs after the close,
    and that is the close its IV was read against); the exit is the close
    `horizon` trading bars later. Rows whose exit has not happened yet are
    dropped — they are not outcomes."""
    out = []
    for ticker, g in hist.groupby("ticker"):
        df = prices.get(ticker)
        if df is None or df.empty or "Close" not in df:
            continue
        close = df["Close"].dropna()
        days = pd.Index([pd.Timestamp(d).date().isoformat() for d in close.index])
        for _, r in g.iterrows():
            hit = days.get_indexer([str(r["date"])[:10]])[0]
            if hit < 0 or hit + horizon >= len(close):
                continue
            entry, exit_ = float(close.iloc[hit]), float(close.iloc[hit + horizon])
            implied = _num(r.get("implied_move_pct"))
            if not entry or implied is None or implied <= 0:
                continue
            move = abs(exit_ / entry - 1) * 100
            cost = STRADDLE_FACTOR * implied
            out.append({
                "date": str(r["date"])[:10], "ticker": ticker,
                "score": _num(r.get("score")), "premium_state": r.get("premium_state"),
                "implied_move_pct": implied, "realized_move_pct": move,
                "beat_implied": move > implied,
                # An ATM straddle held over the horizon, as a return on its cost.
                "straddle_return": (move - cost) / cost,
            })
    return pd.DataFrame(out)


def _group(sub: pd.DataFrame) -> dict:
    if sub.empty:
        return {"n": 0, "beat_implied_pct": None, "avg_straddle_return_pct": None,
                "avg_realized_pct": None, "avg_implied_pct": None}
    return {
        "n": int(len(sub)),
        "beat_implied_pct": round(float(sub["beat_implied"].mean()) * 100, 1),
        "avg_straddle_return_pct": round(float(sub["straddle_return"].mean()) * 100, 1),
        "avg_realized_pct": round(float(sub["realized_move_pct"].mean()), 2),
        "avg_implied_pct": round(float(sub["implied_move_pct"].mean()), 2),
    }


def implied_backtest(hist: pd.DataFrame, prices: dict[str, pd.DataFrame], horizon: int) -> dict:
    """Does the move beat what the option market charged? Payload for backtest.json.

    Buckets by Setup Score and by premium state — the two inputs the strategy
    engine decides on. `avg_straddle_return_pct` is a model straddle bought at
    the logged implied move and held over the horizon: the number a
    "coiled and cheap -> buy premium" rule has to get above zero."""
    logged = 0 if hist is None else int(len(hist))
    base = {"logged_rows": logged, "horizon_days": int(horizon),
            "first_date": (str(hist["date"].min())[:10] if logged else None),
            "min_matured": MIN_MATURED}
    if not logged:
        return {**base, "ok": False, "matured": 0,
                "note": "No implied-volatility history yet — the scan starts logging it on its "
                        "next run, and the first outcomes mature one horizon later."}
    m = matured_outcomes(hist, prices, horizon)
    if m.empty or len(m) < MIN_MATURED:
        return {**base, "ok": False, "matured": int(len(m)),
                "note": f"{len(m)} logged reading(s) have matured so far; the test against "
                        f"implied volatility reports once there are {MIN_MATURED}."}
    score = m["score"].fillna(-1)
    buckets = {
        "all": _group(m),
        "coiled": {"label": "Score ≥ 60", **_group(m[score >= 60])},
        "calm": {"label": "Score < 30", **_group(m[(score >= 0) & (score < 30)])},
        "cheap": {"label": "Premium cheap", **_group(m[m["premium_state"] == "cheap"])},
        "rich": {"label": "Premium rich", **_group(m[m["premium_state"] == "rich"])},
        "coiled_cheap": {"label": "Coiled and cheap (the buy-premium setup)",
                         **_group(m[(score >= 60) & (m["premium_state"] == "cheap")])},
    }
    a, hi, lo = buckets["all"], buckets["coiled"], buckets["calm"]
    # The comparison the score has to win: coiled against calm, both against
    # what their own options charged. The calm side exists because the scan
    # prices a control group of its lowest-scoring names (`role == "control"`).
    if hi["n"] and lo["n"]:
        vs = (f" Coiled names beat their implied move {hi['beat_implied_pct']:.0f}% of the time "
              f"({hi['n']} readings) against {lo['beat_implied_pct']:.0f}% for calm ones "
              f"({lo['n']}).")
    else:
        vs = " No calm-name readings have matured yet, so coiled cannot be compared with calm."
    return {**base, "ok": True, "matured": int(len(m)), "buckets": buckets,
            "text": (f"{a['beat_implied_pct']:.0f}% of {a['n']} matured readings moved more than the "
                     f"option market's implied move; a model straddle bought at that price returned "
                     f"{a['avg_straddle_return_pct']:+.0f}% on average." + vs + " Readings on "
                     "consecutive days overlap, so treat this as descriptive until the history is "
                     "long.")}


def series_by_ticker(hist: pd.DataFrame, last: int = 252) -> dict[str, list[float]]:
    """{ticker: its logged ATM IV readings, oldest first, at most `last` of them}.

    What `options.implied_view` ranks today's IV against once a name has
    `options.MIN_IV_HISTORY` readings. Top and control rows both count: a
    reading is a reading of that name's option market whatever the reason it
    was taken."""
    if hist is None or hist.empty:
        return {}
    out: dict[str, list[float]] = {}
    for ticker, g in hist.sort_values("date").groupby("ticker"):
        vals = [float(v) for v in pd.to_numeric(g["iv_annual"], errors="coerce").dropna()]
        if vals:
            out[str(ticker)] = vals[-last:]
    return out
