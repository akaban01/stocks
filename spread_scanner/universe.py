"""Build the scan universe from Shariah-compliant ETF holdings.

Pulls each fund's published holdings (top names by weight) and unions them into
a deduplicated ticker list. The source page lists the top 25 holdings per fund,
which is plenty for a short-term scanner and keeps the downstream per-ticker
fundamentals screen fast.

Always fail-safe: on any network/parse error it returns an empty list so the
caller can fall back to the curated config watchlist.
"""

from __future__ import annotations

import re
import urllib.request

# The holdings table is rendered into the page itself, so one GET is enough and
# no API key is needed. There used to be a JSON endpoint at
# /api/symbol/e/{sym}/holdings; it now returns 404 for every symbol, which is
# what silently emptied this universe and sent every scan to the config
# fallback. Parsing the page is less pleasant but it is the interface the site
# actually still serves.
_ENDPOINT = "https://stockanalysis.com/etf/{sym}/holdings/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (spread-scanner)"}

_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
# The symbol is the row's link to the stock's own page; the weight is the first
# percentage in the row. Anything without a stock link (header rows, the cash
# line) is not a holding we can scan.
_SYMBOL = re.compile(r'<a[^>]+href="/stocks/([A-Za-z.\-]{1,6})/?"', re.I)
_WEIGHT = re.compile(r">\s*(-?\d+(?:\.\d+)?)\s*%\s*<")


def _valid_ticker(t: str) -> bool:
    t = t.strip().upper()
    return bool(t) and len(t) <= 6 and all(c.isalpha() or c in ".-" for c in t)


def _parse_holdings(html: str) -> list[tuple[str, float]]:
    """Pure: extract [(ticker, weight_pct)] from the holdings page's markup."""
    out: list[tuple[str, float]] = []
    for row in _ROW.findall(html or ""):
        found = _SYMBOL.search(row)
        if not found:
            continue
        ticker = found.group(1).strip().upper()
        if not _valid_ticker(ticker):
            continue
        weight = _WEIGHT.search(row)
        out.append((ticker, float(weight.group(1)) if weight else 0.0))
    return out


def fetch_etf_holdings(symbol: str, timeout: int = 20) -> list[tuple[str, float]]:
    """Return [(ticker, weight_pct)] for one ETF — best-effort, [] on failure."""
    url = _ENDPOINT.format(sym=symbol.strip().lower())
    try:
        req = urllib.request.Request(url, headers=_HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as exc:
        print(f"  ! could not fetch {symbol} holdings: {type(exc).__name__}")
        return []
    holdings = _parse_holdings(html)
    if not holdings:
        # A 200 that yields nothing means the page's shape moved again. Say so:
        # the caller only sees an empty list, and an empty list here used to be
        # indistinguishable from a network failure.
        print(f"  ! {symbol} holdings page returned no rows — the layout may have changed")
    return holdings


def fetch_halal_universe(symbols: list[str], max_holdings: int = 30) -> list[str]:
    """Union holdings across one or more Shariah ETFs, keep the highest-weight
    names first, dedup, and cap at `max_holdings`. [] if every fetch failed."""
    weight_by_ticker: dict[str, float] = {}
    for sym in symbols:
        for ticker, weight in fetch_etf_holdings(sym):
            weight_by_ticker[ticker] = max(weight_by_ticker.get(ticker, 0.0), weight)

    ranked = sorted(weight_by_ticker, key=lambda t: weight_by_ticker[t], reverse=True)
    return ranked[:max_holdings] if max_holdings else ranked
