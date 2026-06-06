"""Lightweight Shariah industry screen (safety net).

This is **not** a full Shariah-compliance engine. It only checks a ticker's
Yahoo Finance sector/industry against a list of prohibited business activities.
It does NOT perform the AAOIFI financial-ratio screen (interest-bearing debt,
interest income, liquidity) — those ratios change every quarter and must be
checked with a dedicated screener (Zoya, Musaffa) before trading.

The curated watchlist in config.yaml is the primary guarantee of halal-ness;
this guard just catches an obviously-prohibited name if one slips into the list.
"""

from __future__ import annotations

import yfinance as yf

# Substrings matched (case-insensitive) against Yahoo's `sector` / `industry`.
# Conventional finance (interest-based), alcohol, tobacco, gambling, pork,
# adult entertainment, and weapons are the classic exclusions.
HARAM_KEYWORDS = (
    "bank", "insurance", "capital markets", "mortgage", "credit services",
    "financial conglomerates", "savings", "reinsurance",
    "brewer", "winer", "distiller", "alcohol", "beverages - wineries",
    "tobacco", "cannabis",
    "gambling", "casino", "resorts & casinos", "betting",
    "aerospace & defense", "defense",
    "adult", "pornography",
    "pork", "swine",
)


def classify(ticker: str) -> tuple[bool, str]:
    """Return (is_allowed, reason). Fails OPEN: if Yahoo gives us no sector
    info we keep the ticker and say so, rather than silently dropping it."""
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception as exc:  # network / parse hiccup
        return True, f"no screen (info error: {type(exc).__name__})"

    sector = str(info.get("sector") or "")
    industry = str(info.get("industry") or "")
    haystack = f"{sector} {industry}".lower()

    if not haystack.strip():
        return True, "no screen (no sector data)"

    for kw in HARAM_KEYWORDS:
        if kw in haystack:
            return False, f"prohibited industry: {industry or sector}"

    return True, f"ok ({industry or sector})"


def filter_tickers(tickers: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Split tickers into (kept, dropped) where dropped is [(ticker, reason)]."""
    kept, dropped = [], []
    for t in tickers:
        allowed, reason = classify(t)
        if allowed:
            kept.append(t)
        else:
            dropped.append((t, reason))
    return kept, dropped
