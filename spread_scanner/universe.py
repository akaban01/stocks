"""Build the scan universe from Shariah-compliant ETF holdings.

Pulls each fund's published holdings (top names by weight) and unions them into
a deduplicated ticker list. The free data endpoint returns the top ~25 holdings
per fund, which is plenty for a short-term scanner and keeps the downstream
per-ticker fundamentals screen fast.

Always fail-safe: on any network/parse error it returns an empty list so the
caller can fall back to the curated config watchlist.
"""

from __future__ import annotations

import json
import urllib.request

# Holdings JSON for any US-listed ETF, no API key required.
_ENDPOINT = "https://stockanalysis.com/api/symbol/e/{sym}/holdings"
_HEADERS = {"User-Agent": "Mozilla/5.0 (spread-scanner)"}


def _valid_ticker(t: str) -> bool:
    t = t.strip().upper()
    return bool(t) and len(t) <= 6 and all(c.isalpha() or c in ".-" for c in t)


def fetch_etf_holdings(symbol: str, timeout: int = 20) -> list[tuple[str, float]]:
    """Return [(ticker, weight_pct)] for one ETF — best-effort, [] on failure."""
    url = _ENDPOINT.format(sym=symbol.upper())
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.load(resp)
    except Exception as exc:
        print(f"  ! could not fetch {symbol} holdings: {type(exc).__name__}")
        return []

    holdings = ((payload.get("data") or {}).get("holdings")) or []
    out: list[tuple[str, float]] = []
    for h in holdings:
        ticker = str(h.get("s", "")).lstrip("$").strip().upper()
        if not _valid_ticker(ticker):
            continue  # skip cash / "$N/A" / other non-equity lines
        try:
            weight = float(str(h.get("as", "")).replace("%", "").strip())
        except ValueError:
            weight = 0.0
        out.append((ticker, weight))
    return out


def fetch_halal_universe(symbols: list[str], max_holdings: int = 30) -> list[str]:
    """Union holdings across one or more Shariah ETFs, keep the highest-weight
    names first, dedup, and cap at `max_holdings`. [] if every fetch failed."""
    weight_by_ticker: dict[str, float] = {}
    for sym in symbols:
        for ticker, weight in fetch_etf_holdings(sym):
            weight_by_ticker[ticker] = max(weight_by_ticker.get(ticker, 0.0), weight)

    ranked = sorted(weight_by_ticker, key=lambda t: weight_by_ticker[t], reverse=True)
    return ranked[:max_holdings] if max_holdings else ranked
