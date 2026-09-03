#!/usr/bin/env python3
"""Entry point: screen the universe, scan it, read the option chains, decide.

Writes JSON only — ``<outdir>/data/scan.json`` (signals + IV read + one explicit
strategy per ticker), ``signals.csv`` and ``charts.json``. The dashboard in
``public/`` is hand-written and reads those; nothing here generates HTML.

    python run.py                  # use config.yaml
    python run.py --config x.yaml  # use a different config
    python run.py --tickers AAPL,MSFT,NVDA   # ad-hoc one-off scan
    python run.py --outdir /tmp/scan         # write somewhere else
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

from spread_scanner import (alerts, charts, data, halal, indicators, leaps, options,
                            report, scanner, strategy, universe)

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


def _hv_context(raw: dict, params: dict) -> tuple[dict[str, float], dict[str, list[float]]]:
    """Trailing realized volatility per ticker: today's reading and the last
    year of readings. The options layer ranks implied vol against these, since
    free data sources publish no implied-vol history."""
    now: dict[str, float] = {}
    hist: dict[str, list[float]] = {}
    for ticker, df in raw.items():
        if df is None or "Close" not in df:
            continue
        try:
            hv = indicators.historical_volatility(df["Close"].dropna(), params["vol_lookback"]) * 100
        except Exception:
            continue
        hv = hv.dropna()
        if hv.empty:
            continue
        now[ticker] = float(hv.iloc[-1])
        hist[ticker] = [float(v) for v in hv.tail(252)]
    return now, hist


def _headline_rows(df: pd.DataFrame, recs: dict[str, dict], limit: int) -> list[str]:
    """The console version of the dashboard: the actual instruction per name."""
    icons = {"BUY_PREMIUM": "BUY ", "SELL_PREMIUM": "SELL", "NEUTRAL_INCOME": "DCAY",
             "STAND_ASIDE": "WAIT", "NO_DATA": "  - "}
    out = []
    for _, r in df.head(limit).iterrows():
        rec = recs.get(r["ticker"]) or {}
        action = rec.get("action", "NO_DATA")
        plan = (rec.get("plan") or {}).get("name", "—")
        extra = ""
        if action in ("BUY_PREMIUM", "SELL_PREMIUM", "NEUTRAL_INCOME"):
            net = (rec.get("plan") or {}).get("net")
            if net is not None:
                extra = f"  {'debit' if net > 0 else 'credit'} ${abs(net):,.0f}"
        out.append(f"[{icons.get(action, '    ')}] {r['ticker']:<6} score {r['score']:>5.1f}  "
                   f"{plan}{extra}")
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Short-term volatility-squeeze spread scanner")
    ap.add_argument("--config", default="config.yaml", help="path to config YAML")
    ap.add_argument("--tickers", help="comma-separated tickers, overrides config")
    ap.add_argument("--outdir", help="output directory, overrides config")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    params = {**DEFAULT_PARAMS, **(cfg.get("params") or {})}
    out_cfg = cfg.get("output") or {}
    outdir = args.outdir or out_cfg.get("dir", "public")
    top = int(out_cfg.get("top", 25))

    # Load daily-calibrated score weights (written by calibrate.py); falls back
    # to the hardcoded scanner.SCORE_WEIGHTS if the file is missing/invalid.
    cal_cfg = cfg.get("calibration") or {}
    weights_meta = scanner.apply_weights_file(cal_cfg.get("weights_file", "weights.json"))
    if weights_meta:
        print(f"Score weights (calibrated {weights_meta.get('as_of', '?')}): {scanner.SCORE_WEIGHTS}")

    # ---- Determine the scan universe ----------------------------------------
    uni_cfg = cfg.get("universe") or {}
    screen_details: dict = {}

    if args.tickers:
        tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    elif uni_cfg.get("source") == "etf":
        etfs = uni_cfg.get("etfs") or ["SPUS"]
        cap = int(uni_cfg.get("max_holdings", 30))
        print(f"Fetching halal universe from {', '.join(etfs)} (top {cap})...")
        tickers = universe.fetch_halal_universe(etfs, max_holdings=cap)
        print(f"  got {len(tickers)} holdings: {', '.join(tickers) or '(none)'}")
        if not tickers and uni_cfg.get("fallback_to_config", True):
            print("  fetch empty — falling back to config tickers.")
            tickers = cfg.get("tickers") or []
    else:
        tickers = cfg.get("tickers") or []

    if not tickers:
        print("No tickers to scan. Add some to config.yaml or pass --tickers.", file=sys.stderr)
        return 2

    # ---- Halal screening ----------------------------------------------------
    hs = cfg.get("halal_screen") or {}
    formula = hs.get("financial_formula") or {}
    if formula.get("enabled"):
        recv = formula.get("max_receivables_ratio", None)
        print("Running halal financial-ratio formula (industry + debt/cash ratios)...")
        kept, dropped, screen_details = halal.screen_universe(
            tickers,
            max_debt=float(formula.get("max_debt_ratio", 0.33)),
            max_cash=float(formula.get("max_cash_ratio", 0.33)),
            max_receivables=(float(recv) if recv is not None else None),
        )
        for t, reason in dropped:
            print(f"  rejected {t}: {reason}")
        if str(formula.get("mode", "filter")) == "filter":
            tickers = kept
            print(f"  {len(kept)} compliant, {len(dropped)} rejected.")
    elif hs.get("live_sector_filter"):
        print("Running halal sector screen...")
        tickers, dropped = halal.filter_tickers(tickers)
        for t, reason in dropped:
            print(f"  excluded {t}: {reason}")

    if not tickers:
        print("No halal-compliant tickers left to scan.", file=sys.stderr)
        return 2

    # Snapshot previous scores (for "newly crossed" alert detection) before overwriting.
    prev_scores: dict[str, float] = {}
    prev_csv = Path(outdir) / "data" / "signals.csv"
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

    # Attach the halal financial-ratio + earnings columns from the screen (if it ran).
    if not df.empty and screen_details:
        df["debt_ratio"] = df["ticker"].map(lambda t: getattr(screen_details.get(t), "debt_ratio", None))
        df["cash_ratio"] = df["ticker"].map(lambda t: getattr(screen_details.get(t), "cash_ratio", None))
        df["earnings_in_days"] = df["ticker"].map(lambda t: getattr(screen_details.get(t), "earnings_in_days", None))

    # ---- Options / IV layer -------------------------------------------------
    # Read the option chain for the most coiled names: IV rank, the IV-vs-HV
    # risk premium, term structure, skew and liquidity. This is what decides
    # whether you should be buying or selling premium.
    opt_cfg = cfg.get("options") or {}
    views: dict[str, options.OptionView] = {}
    if opt_cfg.get("enabled") and not df.empty:
        head = df.head(int(opt_cfg.get("top_n", 15)))
        rows = list(zip(head["ticker"], head["price"], head["em_pct"]))
        hv_now, hv_hist = _hv_context(raw, params)
        print(f"Reading option chains for the top {len(rows)} names (IV rank, term structure, skew)...")
        long_cfg = opt_cfg.get("long_dated") or {}
        views = options.screen_options(rows, horizon_days=int(params["horizon_days"]),
                                       margin=float(opt_cfg.get("margin", 0.15)),
                                       hv_annual=hv_now, hv_history=hv_hist,
                                       long_dated=bool(long_cfg.get("enabled", True)),
                                       long_target_days=int(long_cfg.get("target_days",
                                                                         options.LONG_TARGET_DAYS)))
        for col, attr in (("implied_move_pct", "implied_move_pct"), ("vol_verdict", "verdict"),
                          ("iv_annual", "iv_annual"), ("iv_rank", "iv_rank"),
                          ("premium_score", "premium_score"), ("premium_state", "premium_state"),
                          ("liquidity", "liquidity")):
            df[col] = df["ticker"].map(lambda t, a=attr: getattr(views.get(t), a, None))
        usable = sum(1 for v in views.values()
                     if (v.iv_annual or 0) >= report.MIN_PLAUSIBLE_IV)
        print(f"  priced {len(views)} names ({usable} with usable quotes).")
        if views and usable * 2 < len(views):
            # Outside US market hours the feed returns every contract with a
            # floor IV and no bid, ask or open interest. The scan is still
            # written so a local run can be inspected, but CI will refuse to
            # publish it over the last good one.
            print("  ! The option feed returned contracts with no quotes in them — this is "
                  "what it does outside US market hours. This scan will not be published.",
                  file=sys.stderr)

    # ---- Strategy engine ----------------------------------------------------
    # One explicit instruction per ticker: buy premium, sell premium or stand
    # aside — with the exact legs, net price, risk and management rules.
    strat_cfg = cfg.get("strategy") or {}
    scan_rows = df.to_dict("records") if not df.empty else []
    risk_budget = float(strat_cfg.get("risk_budget_usd", 500))
    recs = strategy.recommend_all(
        scan_rows,
        views,
        risk_budget=risk_budget,
        allow_undefined_risk=bool(strat_cfg.get("allow_undefined_risk", False)),
    )
    if recs:
        df["action"] = df["ticker"].map(lambda t: (recs.get(t) or {}).get("action"))
        df["strategy"] = df["ticker"].map(
            lambda t: ((recs.get(t) or {}).get("plan") or {}).get("name"))

    # ---- Long-dated (≈13-month) spreads -------------------------------------
    # The same chain, a different question: if you wanted this name for the next
    # year, which spread expresses it. Reuses the directional read the near-term
    # engine already made, so the two tabs never disagree about the lean.
    biases = {str(r.get("ticker")): strategy.directional_bias(r) for r in scan_rows}
    long_blocks = leaps.long_spreads_all(
        scan_rows, views,
        risk_budget=float(strat_cfg.get("long_risk_budget_usd", risk_budget * 5)),
        biases=biases,
    )
    if long_blocks:
        print(f"Built {sum(len(b['candidates']) for b in long_blocks.values())} long-dated spreads "
              f"across {len(long_blocks)} names.")

    scan_path = report.write_scan(
        df, outdir, params,
        weights=scanner.SCORE_WEIGHTS,
        weights_as_of=(weights_meta or {}).get("as_of"),
        recommendations=recs,
        option_views=views,
        long_spreads=long_blocks,
        universe={"scanned": int(len(df)), "requested": len(tickers),
                  "source": (uni_cfg.get("source") if not args.tickers else "cli"),
                  "etfs": uni_cfg.get("etfs") or [], "top": top},
        playbook={**strategy.PLAYBOOK, **leaps.PLAYBOOK},
    )
    print(f"\nWrote {scan_path}")

    if not df.empty:
        print("\nWhat to do:")
        for rec in _headline_rows(df, recs, limit=min(top, 12)):
            print(f"  {rec}")

    # Per-ticker price history for the frontend to draw. Best-effort: a failure
    # here must never break the main scan.
    charts_cfg = cfg.get("charts") or {}
    if charts_cfg.get("enabled", True):
        cperiod = str(charts_cfg.get("history_period", "5y"))
        try:
            print(f"Collecting price history for the charts ({cperiod})...")
            craw = data.download(tickers, period=cperiod)
            if not craw:                       # reuse the scan download if the fetch came back empty
                craw = raw
            charts_path = charts.write_charts(craw, outdir, period_label=cperiod)
            print(f"Wrote {charts_path}")
        except Exception as exc:               # noqa: BLE001 — charts are optional, log and move on
            print(f"Charts skipped ({type(exc).__name__}: {exc})", file=sys.stderr)

    alert_cfg = cfg.get("alerts") or {}
    if alert_cfg.get("enabled") and not df.empty:
        alerts.maybe_alert(df, float(alert_cfg.get("score_threshold", 60)), prev_scores,
                           recommendations=recs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
