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

from run import DEFAULT_PARAMS, load_config
from spread_scanner import backtest, data, iv_history, report, scanner, universe


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Backtest the Setup Score")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--years", type=int, default=5, help="years of history to test")
    ap.add_argument("--tickers", help="comma-separated tickers, overrides config")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--weights-file", default=None,
                    help="calibrated weights to score with (default: config, else weights.json)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    params = {**DEFAULT_PARAMS, **(cfg.get("params") or {})}
    outdir = Path(args.outdir or (cfg.get("output") or {}).get("dir", "public"))

    # Load the same calibrated weights run.py loads. Without this the live score
    # and the backtested score were two different functions the moment
    # weights.json existed — which is exactly what scanner.py's comment says
    # cannot happen.
    cal_cfg = cfg.get("calibration") or {}
    loaded = scanner.load_weights_file(
        args.weights_file or cal_cfg.get("weights_file", "weights.json"))
    weights, weights_meta = loaded if loaded else (dict(scanner.SCORE_WEIGHTS), None)
    if weights_meta:
        print(f"Scoring with calibrated weights ({weights_meta.get('as_of', '?')}): {weights}")
    else:
        print(f"Scoring with the built-in weights: {weights}")

    # Universe: explicit override, else fetched ETF holdings, else config list.
    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif (cfg.get("universe") or {}).get("source") == "etf":
        uni = cfg["universe"]
        tickers = universe.from_config(uni, outdir)[0]
        tickers = tickers or (cfg.get("tickers") or [])
    else:
        tickers = cfg.get("tickers") or []

    if not tickers:
        print("No tickers to backtest.", file=sys.stderr)
        return 2

    print(f"Backtesting {len(tickers)} tickers over {args.years}y...")
    raw = data.download(tickers, period=f"{args.years}y")
    print(f"Got data for {len(raw)}/{len(tickers)} tickers.")

    recs, stats = backtest.run_backtest(raw, params, weights=weights)
    payload = backtest.backtest_payload(stats, params, n_tickers=len(raw), years=args.years,
                                        weights=weights,
                                        weights_as_of=(weights_meta or {}).get("as_of"))
    # The test that matters for the trades: forward moves against the implied
    # move the scan logged on each date. Empty until the history matures.
    opt_cfg = cfg.get("options") or {}
    ivh = iv_history.load(outdir / (opt_cfg.get("iv_history_file") or "data/iv_history.csv"))
    payload["implied"] = iv_history.implied_backtest(ivh, raw, int(params["horizon_days"]))
    path = report.write_json(outdir / "data" / "backtest.json", payload)
    print(f"\nWrote {path}\n")

    if payload.get("ok"):
        print(f"{payload['bars']:,} signal-bars · band coverage {payload['coverage_pct']:.0f}% "
              f"(theory 68%)")
        for b in payload["buckets"].values():
            print(f"  {b['label']:<22} {b['bars']:>7,} bars · expand {b['expansion']:.2f}× "
                  f"· broke band {b['broke_band_pct']:.0f}%")
        print(f"\n{payload['verdict']['text']}")
        print(payload["verdict"]["long_band_text"])
    else:
        print(payload.get("note", "No results."))
    imp = payload["implied"]
    print("Against implied vol: " + (imp["text"] if imp.get("ok") else imp["note"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
