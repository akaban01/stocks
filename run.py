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

from spread_scanner import (
    alerts,
    charts,
    data,
    halal,
    indicators,
    leaps,
    options,
    report,
    scanner,
    strategy,
    universe,
)

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
    ap.add_argument("--alert-file", default="alert.json",
                    help="where to stage the alert for send_alerts.py")
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

    # What the universe was *asked* to be vs. what it turned out to be. When a
    # fetch fails these diverge, and the difference has to reach the payload:
    # a scan silently running on the config fallback looks exactly like one
    # running on live ETF holdings, and did so for weeks.
    universe_fallback = None

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
            universe_fallback = (
                f"Could not read holdings from {', '.join(etfs)}, so this scan "
                "ran on the watchlist in config.yaml instead of the funds' "
                "current holdings.")
            tickers = cfg.get("tickers") or []
    else:
        tickers = cfg.get("tickers") or []

    if not tickers:
        print("No tickers to scan. Add some to config.yaml or pass --tickers.", file=sys.stderr)
        return 2

    # Every ticker enters the pipeline in Yahoo's spelling. Class shares are
    # published as BRK.B and Yahoo only answers to BRK-B, so a dotted holding
    # downloaded nothing and disappeared behind one "No data for:" line.
    tickers = list(dict.fromkeys(universe.to_yahoo(t) for t in tickers))

    # ---- Halal screening ----------------------------------------------------
    hs = cfg.get("halal_screen") or {}
    formula = hs.get("financial_formula") or {}
    screen_mode = "none"
    if formula.get("enabled"):
        recv = formula.get("max_receivables_ratio", None)
        screen_mode = str(formula.get("mode", "filter"))
        print("Running halal financial-ratio formula (industry + debt/cash ratios)...")
        kept, dropped, screen_details = halal.screen_universe(
            tickers,
            max_debt=float(formula.get("max_debt_ratio", 0.33)),
            max_cash=float(formula.get("max_cash_ratio", 0.33)),
            max_receivables=(float(recv) if recv is not None else None),
        )
        for t, reason in dropped:
            print(f"  rejected {t}: {reason}")
        if screen_mode == "filter":
            tickers = kept
            print(f"  {len(kept)} compliant, {len(dropped)} rejected.")
        else:
            # `annotate` keeps the names that failed. That is only defensible if
            # the failure is *visible*: a name that failed the industry screen
            # for being a bank has to arrive at the page saying so, not as a row
            # that looks exactly like a compliant one on a page whose whole
            # premise is a screened watchlist.
            print(f"  annotate mode: keeping all {len(tickers)} names; "
                  f"{len(dropped)} are flagged non-compliant in the payload.")
    elif hs.get("live_sector_filter"):
        screen_mode = "industry"
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

    # One download, not two. The charts want a decade of the same daily bars the
    # scan wants a year of, and the decade is a strict superset — pulling both
    # asked a free endpoint for every price twice a day for nothing. The long
    # frames are kept for the charts step below; the scan runs on a slice.
    opt_cfg = cfg.get("options") or {}
    charts_cfg = cfg.get("charts") or {}
    charts_period = str(charts_cfg.get("history_period", "10y"))
    scan_period = str(params["history_period"])
    long_days = data.period_days(charts_period) if charts_cfg.get("enabled", True) else None
    share_download = bool(long_days and (data.period_days(scan_period) or 0) <= long_days)

    fetch_period = charts_period if share_download else scan_period
    print(f"Downloading {len(tickers)} tickers ({fetch_period})...")
    downloaded = data.download(tickers, period=fetch_period)
    craw = downloaded if share_download else {}
    raw = data.slice_period(downloaded, scan_period) if share_download else downloaded
    if share_download:
        print(f"  the charts ({charts_period}) and the scan ({scan_period}) share this download.")
    print(f"Got data for {len(raw)}/{len(tickers)} tickers.")

    missing = sorted(set(t.upper() for t in tickers) - set(raw))
    if missing:
        print(f"No data for: {', '.join(missing)}")

    df = scanner.scan(raw, params)

    # Attach the halal financial-ratio columns from the screen (if it ran).
    if not df.empty and screen_details:
        df["debt_ratio"] = df["ticker"].map(lambda t: getattr(screen_details.get(t), "debt_ratio", None))
        df["cash_ratio"] = df["ticker"].map(lambda t: getattr(screen_details.get(t), "cash_ratio", None))

    # Earnings dates. The guardrail that flips undefined risk to defined, and
    # every earnings warning on every card, reads one column — so the column has
    # to exist however the universe was screened. It used to be attached only
    # inside the financial-formula branch, which meant `--tickers` runs and
    # sector-filtered runs silently had the check switched off while the page
    # rendered an empty Earnings column that looked like "nothing due".
    top_n = int(opt_cfg.get("top_n", 15))
    earnings = {t: res.earnings_in_days for t, res in screen_details.items()}
    if not df.empty:
        # Only the names that get an option chain can use it, and each lookup is
        # a fundamentals call, so this does not fetch the whole universe.
        need = [t for t in df.head(top_n)["ticker"] if t not in earnings]
        if need and opt_cfg.get("enabled"):
            print(f"Fetching earnings dates for {len(need)} name(s) the screen did not cover...")
            earnings.update(halal.earnings_calendar(need))
        df["earnings_in_days"] = df["ticker"].map(earnings.get)

    # ---- Options / IV layer -------------------------------------------------
    # Read the option chain for the most coiled names: IV rank, the IV-vs-HV
    # risk premium, term structure, skew and liquidity. This is what decides
    # whether you should be buying or selling premium.
    views: dict[str, options.OptionView] = {}
    if opt_cfg.get("enabled") and not df.empty:
        head = df.head(top_n)
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

    # The compliance screen's own verdict, per name. In `filter` mode this is
    # every survivor saying why it survived; in `annotate` mode it is the only
    # thing distinguishing a name that failed from one that passed.
    screens = {t: {"compliant": bool(res.compliant),
                   "industry_ok": bool(res.industry_ok),
                   "industry": res.industry,
                   "debt_ratio": res.debt_ratio,
                   "cash_ratio": res.cash_ratio,
                   "receivables_ratio": res.receivables_ratio,
                   "reasons": list(res.reasons or [])}
               for t, res in screen_details.items()}

    scan_path = report.write_scan(
        df, outdir, params,
        weights=scanner.SCORE_WEIGHTS,
        weights_as_of=(weights_meta or {}).get("as_of"),
        recommendations=recs,
        option_views=views,
        long_spreads=long_blocks,
        screens=screens,
        screen_meta={"mode": screen_mode,
                     "thresholds": {"max_debt_ratio": formula.get("max_debt_ratio", 0.33),
                                    "max_cash_ratio": formula.get("max_cash_ratio", 0.33),
                                    "max_receivables_ratio": formula.get("max_receivables_ratio")}
                     if formula.get("enabled") else {},
                     "earnings_checked": sum(1 for v in earnings.values() if v is not None),
                     "earnings_names": len(earnings)},
        universe={"scanned": int(len(df)), "requested": len(tickers),
                  "source": ("cli" if args.tickers
                             else "config" if universe_fallback
                             else uni_cfg.get("source")),
                  "requested_source": (uni_cfg.get("source") if not args.tickers else "cli"),
                  "fallback": universe_fallback,
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
    if charts_cfg.get("enabled", True):
        cshow = charts_cfg.get("display_years", charts.DEFAULT_DISPLAY_YEARS)
        cshow = int(cshow) if cshow else None
        try:
            print(f"Collecting price history for the charts ({charts_period}, "
                  f"cards show {cshow or 'all'}y)...")
            if not craw:                       # not shareable (or came back empty) — fetch it
                craw = data.download(tickers, period=charts_period) or raw
            charts_path = charts.write_charts(craw, outdir, period_label=charts_period,
                                              display_years=cshow)
            print(f"Wrote {charts_path}")
        except Exception as exc:               # noqa: BLE001 — charts are optional, log and move on
            print(f"Charts skipped ({type(exc).__name__}: {exc})", file=sys.stderr)

    # Stage the alert; `send_alerts.py` posts it. Nothing is sent from here,
    # because at this point the scan has not been validated yet — and a scan CI
    # refuses to publish had already pinged the webhook about it.
    alert_cfg = cfg.get("alerts") or {}
    Path(args.alert_file).unlink(missing_ok=True)
    if alert_cfg.get("enabled") and not df.empty:
        pending = alerts.build_alert(df, float(alert_cfg.get("score_threshold", 60)),
                                     prev_scores, recommendations=recs)
        if pending is None:
            print(f"Alerts: no new crossings of score ≥ "
                  f"{float(alert_cfg.get('score_threshold', 60)):g}.")
        else:
            alerts.stage(pending, args.alert_file)
            print(f"Alerts: staged {len(pending['tickers'])} ticker(s) in {args.alert_file} "
                  "— run send_alerts.py after the scan is validated.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
