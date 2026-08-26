#!/usr/bin/env python3
"""Run the Setup-Score backtest and write public/data/backtest.json.

    python backtest.py                       # config universe + params, 5y
    python backtest.py --years 3
    python backtest.py --tickers AAPL,NVDA,MSFT
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from spread_scanner import backtest, data, report, universe
from run import DEFAULT_PARAMS, load_config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the Setup Score")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--years", type=int, default=5, help="years of history to test")
    ap.add_argument("--tickers", help="comma-separated tickers, overrides config")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    params = {**DEFAULT_PARAMS, **(cfg.get("params") or {})}
    outdir = Path(args.outdir or (cfg.get("output") or {}).get("dir", "public"))

    # Universe: explicit override, else fetched ETF holdings, else config list.
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif (cfg.get("universe") or {}).get("source") == "etf":
        uni = cfg["universe"]
        tickers = universe.fetch_halal_universe(uni.get("etfs") or ["SPUS"],
                                                 int(uni.get("max_holdings", 30)))
        tickers = tickers or (cfg.get("tickers") or [])
    else:
        tickers = cfg.get("tickers") or []

    if not tickers:
        print("No tickers to backtest.", file=sys.stderr)
        return 2

    print(f"Backtesting {len(tickers)} tickers over {args.years}y...")
    raw = data.download(tickers, period=f"{args.years}y")
    print(f"Got data for {len(raw)}/{len(tickers)} tickers.")

    recs, stats = backtest.run_backtest(raw, params)
    payload = backtest.backtest_payload(stats, params, n_tickers=len(raw), years=args.years)
    path = report.write_json(outdir / "data" / "backtest.json", payload)
    print(f"\nWrote {path}\n")

    if payload.get("ok"):
        print(f"{payload['bars']:,} signal-bars · band coverage {payload['coverage_pct']:.0f}% "
              f"(theory 68%)")
        for key, b in payload["buckets"].items():
            print(f"  {b['label']:<22} {b['bars']:>7,} bars · expand {b['expansion']:.2f}× "
                  f"· broke band {b['broke_band_pct']:.0f}%")
        print(f"\n{payload['verdict']['text']}")
    else:
        print(payload.get("note", "No results."))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
