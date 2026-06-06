#!/usr/bin/env python3
"""Entry point: download data, scan, write reports.

    python run.py                  # use config.yaml
    python run.py --config x.yaml  # use a different config
    python run.py --tickers AAPL,MSFT,NVDA   # ad-hoc one-off scan
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make Unicode (emoji, σ) safe to print on Windows' cp1252 console.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
import yaml

from spread_scanner import alerts, data, halal, report, scanner

DEFAULT_PARAMS = {
    "horizon_days": 10,
    "history_period": "6mo",
    "bb_length": 20,
    "bb_mult": 2.0,
    "kc_length": 20,
    "kc_mult": 1.5,
    "atr_length": 14,
    "vol_lookback": 20,
    "percentile_lookback": 120,
}


def load_config(path: str) -> dict:
    cfg_path = Path(path)
    if not cfg_path.exists():
        return {"tickers": [], "params": {}, "output": {}}
    with cfg_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Short-term volatility-squeeze spread scanner")
    ap.add_argument("--config", default="config.yaml", help="path to config YAML")
    ap.add_argument("--tickers", help="comma-separated tickers, overrides config")
    ap.add_argument("--outdir", help="output directory, overrides config")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    params = {**DEFAULT_PARAMS, **(cfg.get("params") or {})}
    out_cfg = cfg.get("output") or {}
    outdir = args.outdir or out_cfg.get("dir", "output")
    top = int(out_cfg.get("top", 25))

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = cfg.get("tickers") or []

    if not tickers:
        print("No tickers to scan. Add some to config.yaml or pass --tickers.", file=sys.stderr)
        return 2

    # Halal safety-net screen: drop any prohibited-industry names before scanning.
    if (cfg.get("halal_screen") or {}).get("live_sector_filter"):
        print("Running halal sector screen...")
        tickers, dropped = halal.filter_tickers(tickers)
        for t, reason in dropped:
            print(f"  excluded {t}: {reason}")

    # Snapshot previous scores (for "newly crossed" alert detection) before overwriting.
    prev_scores: dict[str, float] = {}
    prev_csv = Path(outdir) / "signals.csv"
    if prev_csv.exists():
        try:
            prev = pd.read_csv(prev_csv)
            prev_scores = dict(zip(prev["ticker"], prev["score"]))
        except Exception:
            pass

    print(f"Downloading {len(tickers)} tickers ({params['history_period']})...")
    raw = data.download(tickers, period=params["history_period"])
    print(f"Got data for {len(raw)}/{len(tickers)} tickers.")

    missing = sorted(set(t.upper() for t in tickers) - set(raw))
    if missing:
        print(f"No data for: {', '.join(missing)}")

    df = scanner.scan(raw, params)
    report_path = report.write_reports(df, outdir, params, top=top)

    print(f"\nWrote {report_path}")
    if not df.empty:
        cols = ["rank", "ticker", "price", "score", "squeeze_days", "em_pct", "lean"]
        print("\nTop setups:")
        print(df[cols].head(min(top, 10)).to_string(index=False))

    alert_cfg = cfg.get("alerts") or {}
    if alert_cfg.get("enabled") and not df.empty:
        alerts.maybe_alert(df, float(alert_cfg.get("score_threshold", 60)), prev_scores)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
