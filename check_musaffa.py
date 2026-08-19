#!/usr/bin/env python3
"""Check the first N tickers from ``config.yaml`` against Musaffa's free screener.

Musaffa (https://musaffa.com) is a dedicated Shariah-compliance stock screener.
The scanner already runs a *quantitative* AAOIFI-style formula
(see ``spread_scanner/halal.py``), but the README flags it as an approximation
to re-verify against a dedicated screener before trading. This script does that
for the top-of-watchlist names.

It uses Musaffa's **free, public** per-stock pages
(``https://musaffa.com/stock/<TICKER>/``) — the same verdict any visitor sees
without logging in — rather than the paid B2B API. Each page embeds the
compliance status and 0–5 ranking in a JSON state blob, which we parse. No API
key or account is required; robots.txt permits ``/stock/`` paths.

This is a courtesy read of a handful of public pages once in a while — keep it
that way (small watchlist, not a bulk crawler) and leave the polite delay in.

Usage
-----
    python check_musaffa.py            # first 15 from config.yaml
    python check_musaffa.py -n 25      # first 25
    python check_musaffa.py --tickers AAPL,MSFT,NVDA
    python check_musaffa.py --json     # machine-readable results
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

import yaml

STOCK_URL = "https://musaffa.com/stock/{ticker}/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (spread-scanner halal-check)"}
# Musaffa's status enum -> a short human label.
_STATUS_LABEL = {
    "COMPLIANT": "Halal",
    "NON_COMPLIANT": "Not Halal",
    "DOUBTFUL": "Doubtful",
    "QUESTIONABLE": "Doubtful",
    "NON_RATED": "Unrated",
    "NOT_RATED": "Unrated",
}


def load_tickers(config_path: str, limit: int) -> list[str]:
    """First `limit` tickers from ``config.yaml``'s ``tickers:`` list."""
    with Path(config_path).open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    tickers = [str(t).strip().upper() for t in (cfg.get("tickers") or []) if str(t).strip()]
    return tickers[:limit]


def _as_of(html: str) -> str:
    """Month the verdict is stated as of, e.g. 'August 2026' ('' if absent).

    Deliberately reads the page's own "As of <Month> <Year>, ... is classified
    as ..." prose, which is scoped to the *compliance* call. The date fields in
    the state blob (`priceLastUpdated`, `datetime`) are price timestamps and
    would be misleading here — a stale verdict on a freshly-priced stock would
    still show today's date."""
    m = re.search(r"As of ([A-Z][a-z]+ \d{4}), [^<]{0,120}?classified as", html)
    return m.group(1) if m else ""


def _extract(html: str, ticker: str) -> dict:
    """Pull (status, ranking, name, as_of) for `ticker` out of the page.

    Anchors on the ``stock-overview:<TICKER>`` object so we read the subject
    stock's fields, not a related name the page also embeds. Returns whatever
    it can find; missing fields come back as None."""
    anchor = re.search(re.escape(f"stock-overview:{ticker.upper()}"), html)
    window = html[anchor.start(): anchor.start() + 6000] if anchor else html

    status_m = re.search(r'"shariahCompliantStatus"\s*:\s*"([A-Z_]+)"', window)
    rank_m = re.search(r'"compliantRanking"\s*:\s*(\d+)', window)
    name_m = re.search(r'"name"\s*:\s*"([^"]+)"', window)

    status = status_m.group(1) if status_m else None
    return {
        "ticker": ticker.upper(),
        "status_raw": status,
        "status": _STATUS_LABEL.get(status, status or "unknown"),
        "ranking": int(rank_m.group(1)) if rank_m else None,
        "name": name_m.group(1) if name_m else "",
        "as_of": _as_of(html),
    }


def check_ticker(ticker: str, timeout: int = 30) -> dict:
    """Fetch and parse one ticker's public Musaffa page. Fails soft."""
    url = STOCK_URL.format(ticker=ticker.upper())
    req = urllib.request.Request(url, headers=_HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001 — one bad name shouldn't abort the batch
        return {"ticker": ticker.upper(), "status_raw": None,
                "status": f"error ({type(exc).__name__})", "ranking": None,
                "name": "", "as_of": ""}
    return _extract(html, ticker)


def _stale_months(as_of: str, months: int = 2) -> bool:
    """True when `as_of` ('August 2026') is more than `months` behind today.

    Compliance is restated as financials land, so a verdict that stops moving
    is the signal that something is off — worth flagging, not just printing."""
    try:
        d = dt.datetime.strptime(as_of, "%B %Y")
    except (ValueError, TypeError):
        return False
    now = dt.date.today()
    return (now.year - d.year) * 12 + (now.month - d.month) > months


def check_all(tickers: list[str], delay: float = 1.0) -> list[dict]:
    """Check each ticker in turn, pausing between requests to stay polite."""
    results = []
    for i, t in enumerate(tickers):
        if i:
            time.sleep(delay)
        res = check_ticker(t)
        rank = "-" if res["ranking"] is None else f"{res['ranking']}/5"
        as_of = res.get("as_of") or "—"
        res["stale"] = _stale_months(res.get("as_of", ""))
        if res["stale"]:
            as_of = f"⚠ {as_of}"
        print(f"  {res['ticker']:<6} {res['status']:<11} {rank:<5} "
              f"{as_of:<16} {res['name']}")
        results.append(res)
    return results


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml", help="path to config YAML")
    ap.add_argument("-n", "--limit", type=int, default=15,
                    help="how many tickers from the top of the list (default 15)")
    ap.add_argument("--tickers", help="comma-separated tickers, overrides --config/-n")
    ap.add_argument("--delay", type=float, default=1.0,
                    help="seconds to wait between page requests (default 1.0)")
    ap.add_argument("--json", action="store_true",
                    help="print results as JSON instead of a table")
    args = ap.parse_args(argv)

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = load_tickers(args.config, args.limit)

    if not tickers:
        print("No tickers to check.", file=sys.stderr)
        return 2

    print(f"Checking {len(tickers)} tickers on Musaffa (free public pages): "
          f"{', '.join(tickers)}\n")
    print(f"  {'Ticker':<6} {'Status':<11} {'Rank':<5} {'As of':<16} Company")
    print("  " + "-" * 72)
    results = check_all(tickers, delay=args.delay)

    stale = [r["ticker"] for r in results if r.get("stale")]
    if stale:
        print(f"\n  ⚠ Verdict not restated in over 2 months: {', '.join(stale)} "
              f"— re-check on musaffa.com before relying on it.")

    if args.json:
        print("\n" + json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
