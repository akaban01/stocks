"""Shariah screening — industry exclusion + financial-ratio formula.

Two layers:

1. **Industry screen** (`classify`): rejects prohibited business activities
   (conventional interest-based finance, alcohol, tobacco, gambling, pork,
   adult entertainment, weapons) from Yahoo's sector/industry labels.

2. **Financial-ratio formula** (`financial_screen`): the quantitative gate used
   by the recognized standards (AAOIFI / S&P Dow Jones Islamic / MSCI Islamic).
   Using market cap as the denominator, a name passes when:
       interest-bearing debt / market cap   < ~33%
       (cash + interest securities) / mktcap < ~33%
       accounts receivable / market cap      < ~33%   (optional)

⚠️ Approximation, not a fatwa. The standards use a trailing-average market cap
and a precise definition of "interest-bearing securities"; we use spot values
from yfinance. The separate "non-compliant income < 5% of revenue" purification
rule needs revenue-segment data free sources don't expose — verify that, and the
final ruling, with a dedicated screener (Zoya, Musaffa).
"""

from __future__ import annotations

from dataclasses import dataclass

import yfinance as yf

# Substrings matched (case-insensitive) against Yahoo's `sector` / `industry`.
HARAM_KEYWORDS = (
    "bank", "insurance", "capital markets", "mortgage", "credit services",
    "financial conglomerates", "savings", "reinsurance",
    # NB: avoid bare "alcohol" — it substring-matches the halal "Non-Alcoholic".
    # The producer industries below already cover actual alcohol businesses.
    "brewer", "winer", "distiller",
    "tobacco", "cannabis",
    "gambling", "casino", "resorts & casinos", "betting",
    "aerospace & defense", "defense",
    "adult", "pornography",
    "pork", "swine",
)


@dataclass
class ScreenResult:
    ticker: str
    compliant: bool
    industry_ok: bool
    debt_ratio: float | None        # interest-bearing debt / market cap
    cash_ratio: float | None        # cash & equivalents / market cap
    receivables_ratio: float | None # accounts receivable / market cap (optional)
    industry: str
    reasons: list[str]
    earnings_in_days: int | None = None  # calendar days to next earnings (from same info call)


# ---------------------------------------------------------------- industry only

def _industry_check(info: dict) -> tuple[bool, str]:
    """(ok, industry_label) from a yfinance info dict. No data -> ok (fail-open)."""
    sector = str(info.get("sector") or "")
    industry = str(info.get("industry") or "")
    haystack = f"{sector} {industry}".lower()
    if not haystack.strip():
        return True, ""
    for kw in HARAM_KEYWORDS:
        if kw in haystack:
            return False, industry or sector
    return True, industry or sector


def classify(ticker: str) -> tuple[bool, str]:
    """Industry-only screen. (is_allowed, reason). Fails open on missing data."""
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception as exc:
        return True, f"no screen (info error: {type(exc).__name__})"
    ok, industry = _industry_check(info)
    if not industry:
        return True, "no screen (no sector data)"
    return (ok, f"ok ({industry})") if ok else (False, f"prohibited industry: {industry}")


def filter_tickers(tickers: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Industry-only split into (kept, dropped[(ticker, reason)])."""
    kept, dropped = [], []
    for t in tickers:
        allowed, reason = classify(t)
        (kept if allowed else dropped).append(t if allowed else (t, reason))
    return kept, dropped


# ------------------------------------------------------------ financial formula

def _ratio(numer, denom) -> float | None:
    """numer/denom, or None when inputs are missing/invalid. 0 numerator -> 0.0."""
    try:
        if numer is None or not denom or float(denom) <= 0:
            return None
        return float(numer) / float(denom)
    except (TypeError, ValueError):
        return None


def _days_to_earnings(info: dict) -> int | None:
    """Calendar days until the next earnings date, from a yfinance info dict.
    Uses whichever earnings timestamp is in the future; None if unknown/past."""
    import time
    now = time.time()
    candidates = [
        info.get("earningsTimestamp"),
        info.get("earningsTimestampStart"),
        info.get("earningsTimestampEnd"),
    ]
    future = [t for t in candidates if isinstance(t, (int, float)) and t > now]
    return int((min(future) - now) // 86400) if future else None


def _receivables(tk: "yf.Ticker") -> float | None:
    """Most recent accounts-receivable from the balance sheet (best-effort)."""
    try:
        bs = tk.balance_sheet
        for key in ("Accounts Receivable", "Receivables", "Net Receivables"):
            if key in bs.index:
                vals = bs.loc[key].dropna()
                if not vals.empty:
                    return float(vals.iloc[0])
    except Exception:
        pass
    return None


def financial_screen(
    ticker: str,
    max_debt: float = 0.33,
    max_cash: float = 0.33,
    max_receivables: float | None = None,
) -> ScreenResult:
    """Full screen (industry + financial ratios) with ONE fundamentals fetch.

    `max_receivables=None` skips the receivables ratio (it needs an extra
    balance-sheet call). Fails OPEN on a fetch error — we don't reject a name
    just because Yahoo hiccupped; we only reject on a clear ratio breach."""
    try:
        tk = yf.Ticker(ticker)
        info = tk.get_info()
    except Exception as exc:
        return ScreenResult(ticker, True, True, None, None, None, "",
                            [f"no screen (info error: {type(exc).__name__})"])

    reasons: list[str] = []
    industry_ok, industry = _industry_check(info)
    if not industry_ok:
        reasons.append(f"prohibited industry: {industry}")

    mktcap = info.get("marketCap")
    debt_ratio = _ratio(info.get("totalDebt"), mktcap)
    cash_ratio = _ratio(info.get("totalCash"), mktcap)
    recv_ratio = _ratio(_receivables(tk), mktcap) if max_receivables is not None else None

    if debt_ratio is not None and debt_ratio > max_debt:
        reasons.append(f"debt/mktcap {debt_ratio:.0%} > {max_debt:.0%}")
    if cash_ratio is not None and cash_ratio > max_cash:
        reasons.append(f"cash/mktcap {cash_ratio:.0%} > {max_cash:.0%}")
    if recv_ratio is not None and recv_ratio > max_receivables:
        reasons.append(f"receivables/mktcap {recv_ratio:.0%} > {max_receivables:.0%}")

    compliant = industry_ok and not any(
        r is not None and r > lim
        for r, lim in [(debt_ratio, max_debt), (cash_ratio, max_cash),
                       (recv_ratio, max_receivables if max_receivables is not None else 1.0)]
    )
    return ScreenResult(ticker, compliant, industry_ok, debt_ratio, cash_ratio,
                        recv_ratio, industry, reasons or ["ok"],
                        earnings_in_days=_days_to_earnings(info))


def screen_universe(
    tickers: list[str],
    max_debt: float = 0.33,
    max_cash: float = 0.33,
    max_receivables: float | None = None,
) -> tuple[list[str], list[tuple[str, str]], dict[str, ScreenResult]]:
    """Run `financial_screen` over a list. Returns (kept, dropped, details)."""
    details: dict[str, ScreenResult] = {}
    kept, dropped = [], []
    for t in tickers:
        res = financial_screen(t, max_debt, max_cash, max_receivables)
        details[t] = res
        if res.compliant:
            kept.append(t)
        else:
            dropped.append((t, "; ".join(res.reasons)))
    return kept, dropped, details
