#!/usr/bin/env python3
"""Check the first N tickers from ``config.yaml`` against the Musaffa API.

Musaffa (https://musaffa.com) is a dedicated Shariah-compliance stock screener.
The scanner already runs a *quantitative* AAOIFI-style formula
(see ``spread_scanner/halal.py``), but it is an approximation — the README
recommends re-verifying each name with a dedicated screener before trading.
This script does exactly that: it takes the top-of-watchlist names and asks
Musaffa's B2B API for their compliance verdict.

Usage
-----
    export MUSAFFA_CLIENT_ID=...       # from Musaffa B2B credentials
    export MUSAFFA_SECRET_KEY=...
    python check_musaffa.py            # first 15 from config.yaml
    python check_musaffa.py -n 25      # first 25
    python check_musaffa.py --tickers AAPL,MSFT,NVDA
    python check_musaffa.py --json     # dump raw response instead of a table

Endpoint
--------
POST ``https://platform.musaffa.com/b2b/api/v2/musaffa/stocks/screening-list``
with ``{"stocks": [...]}``. Auth is a per-request token
(``base64(sha512(secretKey + time + json_body))``) plus ``clientId`` and
``time`` (``yyyyMMddHHmmss`` in UTC+5). See https://api.musaffa.com/ for the
full contract.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

BULK_URL = "https://platform.musaffa.com/b2b/api/v2/musaffa/stocks/screening-list"
MUSAFFA_TZ = timezone(timedelta(hours=5))  # Musaffa server clock, per the docs
MAX_BULK = 100                              # API cap per request


def load_tickers(config_path: str, limit: int) -> list[str]:
    """First `limit` tickers from ``config.yaml``'s ``tickers:`` list."""
    with Path(config_path).open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    tickers = [str(t).strip().upper() for t in (cfg.get("tickers") or []) if str(t).strip()]
    return tickers[:limit]


def _sign(secret_key: str, body: str) -> tuple[str, str]:
    """Return ``(time_header, token_header)`` for a given JSON body.

    The token is ``base64(sha512(secretKey + time + body))`` and is only valid
    for a few seconds server-side, so build it right before the request."""
    time_hdr = datetime.now(MUSAFFA_TZ).strftime("%Y%m%d%H%M%S")
    digest = hashlib.sha512((secret_key + time_hdr + body).encode("utf-8")).digest()
    return time_hdr, base64.b64encode(digest).decode("ascii")


def call_musaffa(tickers: list[str], client_id: str, secret_key: str,
                 timeout: int = 30) -> dict:
    """POST the bulk-screening request and return the parsed JSON payload."""
    body = json.dumps({"stocks": tickers}, separators=(",", ":"))
    time_hdr, token_hdr = _sign(secret_key, body)
    req = urllib.request.Request(
        BULK_URL,
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "clientId": client_id,
            "time": time_hdr,
            "token": token_hdr,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Musaffa API error {exc.code}: {detail}") from exc


def _iter_results(payload: dict):
    """Yield per-stock result dicts regardless of top-level envelope shape.

    The API has wrapped its list under ``data``, ``result``, or ``stocks`` in
    different versions of the docs; try each so a schema tweak doesn't break
    us silently."""
    if isinstance(payload, list):
        yield from payload
        return
    for key in ("data", "result", "results", "stocks"):
        val = payload.get(key)
        if isinstance(val, list):
            yield from val
            return
        if isinstance(val, dict):
            for inner in ("stocks", "list", "items"):
                if isinstance(val.get(inner), list):
                    yield from val[inner]
                    return


def _row(item: dict) -> tuple[str, str, str, str]:
    """(ticker, status, ranking, note) tuple pulled out of a result item."""
    ticker = str(item.get("stockName") or item.get("symbol")
                 or item.get("ticker") or "?").upper()
    status = str(item.get("shariahComplianceStatus")
                 or item.get("status") or "unknown")
    ranking = item.get("complianceRanking") or item.get("ranking")
    ranking_str = "-" if ranking in (None, "") else f"{ranking}/5"
    note = str(item.get("companyName") or item.get("name") or "")
    return ticker, status, ranking_str, note


def print_table(payload: dict, requested: list[str]) -> None:
    """Render the response as a two-column status table, one row per ticker."""
    by_ticker: dict[str, tuple[str, str, str, str]] = {}
    for item in _iter_results(payload):
        row = _row(item)
        by_ticker[row[0]] = row

    print(f"\n{'Ticker':<8} {'Status':<12} {'Rank':<6} Company")
    print("-" * 60)
    for t in requested:
        if t in by_ticker:
            _, status, rank, note = by_ticker[t]
            print(f"{t:<8} {status:<12} {rank:<6} {note}")
        else:
            print(f"{t:<8} {'no data':<12} {'-':<6}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default="config.yaml", help="path to config YAML")
    ap.add_argument("-n", "--limit", type=int, default=15,
                    help="how many tickers from the top of the list (default 15)")
    ap.add_argument("--tickers", help="comma-separated tickers, overrides --config/-n")
    ap.add_argument("--json", action="store_true",
                    help="print the raw API JSON instead of a formatted table")
    args = ap.parse_args(argv)

    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = load_tickers(args.config, args.limit)

    if not tickers:
        print("No tickers to check.", file=sys.stderr)
        return 2
    if len(tickers) > MAX_BULK:
        print(f"Musaffa bulk cap is {MAX_BULK}; truncating.", file=sys.stderr)
        tickers = tickers[:MAX_BULK]

    client_id = os.environ.get("MUSAFFA_CLIENT_ID")
    secret_key = os.environ.get("MUSAFFA_SECRET_KEY")
    if not client_id or not secret_key:
        print("Set MUSAFFA_CLIENT_ID and MUSAFFA_SECRET_KEY (B2B credentials from "
              "https://musaffa.com/for-business/) before running.", file=sys.stderr)
        return 2

    print(f"Checking {len(tickers)} tickers with Musaffa: {', '.join(tickers)}")
    payload = call_musaffa(tickers, client_id, secret_key)

    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print_table(payload, tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
