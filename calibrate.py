#!/usr/bin/env python3
"""Calibrate the Setup-Score weights against the expansion outcome.

Measures each feature's exceed-rate lift on a train split, derives weights, and
validates the separation out-of-sample. Writes weights.json (the live "model"
the scanner loads each run) and public/data/calibration.json.

    python calibrate.py            # config universe + params, 5y
    python calibrate.py --years 6
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
    ap = argparse.ArgumentParser(description="Calibrate Setup-Score weights")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--train-frac", type=float, default=0.7)
    ap.add_argument("--weights-file", default="weights.json")
    ap.add_argument("--tickers")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    params = {**DEFAULT_PARAMS, **(cfg.get("params") or {})}
    outdir = Path((cfg.get("output") or {}).get("dir", "public"))

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif (cfg.get("universe") or {}).get("source") == "etf":
        uni = cfg["universe"]
        tickers = universe.fetch_halal_universe(uni.get("etfs") or ["SPUS"],
                                                 int(uni.get("max_holdings", 30))) or (cfg.get("tickers") or [])
    else:
        tickers = cfg.get("tickers") or []

    print(f"Calibrating on {len(tickers)} tickers over {args.years}y...")
    raw = data.download(tickers, period=f"{args.years}y")
    recs, _ = backtest.run_backtest(raw, params)
    if recs.empty:
        print("Not enough data to calibrate.", file=sys.stderr)
        return 2

    c = backtest.calibrate_weights(recs, train_frac=args.train_frac)
    payload = backtest.calibration_payload(c, years=args.years, universe=len(raw))

    # weights.json is the live "model" the scanner loads each run.
    import datetime as dt
    import json
    weights_path = Path(args.weights_file)
    weights_path.write_text(json.dumps({
        "weights": c["weights"],
        "as_of": dt.date.today().isoformat(),
        "history_years": args.years,
        "n_bars": int(c["n"]),
        "universe": len(raw),
    }, indent=2), encoding="utf-8")

    cal_path = report.write_json(outdir / "data" / "calibration.json", payload)
    print(f"Wrote {weights_path} and {cal_path}")
    if payload.get("ok"):
        sep = payload["separation"]
        print(f"  heuristic separation  {sep['heuristic']['separation_pts']:+.0f} pts")
        print(f"  calibrated separation {sep['calibrated']['separation_pts']:+.0f} pts (out-of-sample)")
        print(f"  {payload['verdict']['text']}")
    print(f"Active weights -> {c['weights']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
