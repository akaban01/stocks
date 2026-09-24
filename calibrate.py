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

from run import DEFAULT_PARAMS, load_config
from spread_scanner import backtest, data, report, universe


def reuse_recent_fit(cal_file: Path, weights_file: Path, refit_days: int,
                     today) -> dict | None:
    """Rewrite `weights_file` from the committed calibration when it is recent.

    weights.json is gitignored, so on a fresh CI checkout it never exists — but
    data/calibration.json is committed and carries the same weights and stamp.
    Returns {"weights", "as_of"} when it reused the fit, None when a refit is due
    (no calibration, a failed one, or one older than `refit_days`)."""
    import datetime as dt
    import json

    if refit_days <= 0:
        return None
    try:
        cal = json.loads(cal_file.read_text(encoding="utf-8"))
        as_of = dt.date.fromisoformat(str(cal["as_of"]))
        weights = cal["weights"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (not cal.get("ok") or not isinstance(weights, dict)
            or cal.get("method_version") != backtest.CALIBRATION_METHOD
            or (today - as_of).days >= refit_days):
        return None
    weights_file.write_text(json.dumps({
        "weights": weights, "as_of": as_of.isoformat(),
        "history_years": cal.get("history_years"), "universe": cal.get("universe"),
        "reused": True,
    }, indent=2), encoding="utf-8")
    return {"weights": weights, "as_of": as_of.isoformat()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Calibrate Setup-Score weights")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--years", type=int, default=None)
    ap.add_argument("--train-frac", type=float, default=None)
    ap.add_argument("--weights-file", default=None)
    ap.add_argument("--tickers")
    ap.add_argument("--force", action="store_true",
                    help="refit even if the last fit is younger than calibration.refit_days")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    params = {**DEFAULT_PARAMS, **(cfg.get("params") or {})}
    outdir = Path((cfg.get("output") or {}).get("dir", "public"))

    # Defaults come from the `calibration:` block in config.yaml, so the daily
    # workflow and a local run calibrate the same way and write to the same
    # file run.py and backtest.py read.
    cal_cfg = cfg.get("calibration") or {}
    args.years = int(args.years if args.years is not None else cal_cfg.get("years", 5))
    args.train_frac = float(args.train_frac if args.train_frac is not None
                            else cal_cfg.get("train_frac", 0.7))
    args.weights_file = args.weights_file or cal_cfg.get("weights_file", "weights.json")
    refit_days = int(cal_cfg.get("refit_days", 30))

    import datetime as dt
    import json

    cal_file = outdir / "data" / "calibration.json"
    if not (args.force or args.tickers):
        reused = reuse_recent_fit(cal_file, Path(args.weights_file), refit_days, dt.date.today())
        if reused:
            print(f"Reusing the fit from {reused['as_of']} (refit every {refit_days} days; "
                  f"--force to refit now) -> {reused['weights']}")
            return 0

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    else:
        # The names the scan actually ran on, after the halal screen.
        tickers, source = universe.for_validation(cfg, outdir)
        print(f"Universe: {len(tickers)} names from {source}.")

    print(f"Calibrating on {len(tickers)} tickers over {args.years}y...")
    raw = data.download(tickers, period=f"{args.years}y")
    recs, _ = backtest.run_backtest(raw, params)
    if recs.empty:
        print("Not enough data to calibrate.", file=sys.stderr)
        return 2

    c = backtest.calibrate_weights(recs, train_frac=args.train_frac,
                                   embargo=int(params["horizon_days"]))

    # weights.json is the live "model" the scanner loads each run. The same
    # stamp goes into the published calibration, so the dashboard can say
    # whether the scan beside it actually used this fit.
    as_of = dt.date.today().isoformat()
    payload = backtest.calibration_payload(c, years=args.years, universe=len(raw),
                                           as_of=as_of)
    weights_path = Path(args.weights_file)
    weights_path.write_text(json.dumps({
        "weights": c["weights"],
        "as_of": as_of,
        "history_years": args.years,
        "n_bars": int(c["n"]),
        "universe": len(raw),
    }, indent=2), encoding="utf-8")

    cal_path = report.write_json(cal_file, payload)
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
