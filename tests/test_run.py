"""End-to-end wiring: run.py from CLI args to the JSON on disk, no network."""

import json

import numpy as np
import pandas as pd
import pytest

import run
from spread_scanner import data, halal, leaps, options
from conftest import make_view

TICKERS = ["AAA", "BBB", "CCC"]


def _ohlcv(seed, n=320):
    rng = np.random.RandomState(seed)
    px = (40 + seed * 30) * np.exp(np.cumsum(rng.normal(0.0004, 0.013, n)))
    close = pd.Series(px, index=pd.bdate_range("2024-01-02", periods=n))
    return pd.DataFrame({"Open": close, "High": close * 1.008, "Low": close * 0.992,
                         "Close": close, "Volume": 1e6})


@pytest.fixture
def offline(monkeypatch):
    """Stub every network edge: prices, the Shariah screen and the option chains."""
    monkeypatch.setattr(data, "download",
                        lambda tickers, period="1y", interval="1d":
                        {t: _ohlcv(i) for i, t in enumerate(tickers)})

    def fake_screen(tickers, **kw):
        details = {t: halal.ScreenResult(ticker=t, compliant=True, industry_ok=True,
                                         debt_ratio=0.05, cash_ratio=0.03,
                                         receivables_ratio=None, industry="Semiconductors",
                                         reasons=[], earnings_in_days=40)
                   for t in tickers}
        return list(tickers), [], details
    monkeypatch.setattr(halal, "screen_universe", fake_screen)

    # AAA rich, BBB cheap, CCC never priced (outside top_n in the real thing).
    def fake_options(rows, horizon_days, margin=0.15, hv_annual=None, hv_history=None,
                     long_dated=True, long_target_days=395):
        spec = {"AAA": dict(iv=58, hv=28, iv_rank=88), "BBB": dict(iv=18, hv=30, iv_rank=8)}
        return {t: make_view(t, spot=float(spot), **spec[t])
                for t, spot, _hist in rows if t in spec}
    monkeypatch.setattr(options, "screen_options", fake_options)
    monkeypatch.setattr(run.alerts, "maybe_alert", lambda *a, **k: 0)


@pytest.fixture
def config(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: config}\n"
        "halal_screen: {financial_formula: {enabled: true, mode: filter}}\n"
        "options: {enabled: true, top_n: 3}\n"
        "strategy: {risk_budget_usd: 1000}\n"
        "charts: {enabled: true, history_period: 2y}\n"
        "alerts: {enabled: false}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(TICKERS)}]\n", encoding="utf-8")
    return cfg


def test_full_run_writes_the_whole_payload(offline, config, tmp_path, capsys):
    assert run.main(["--config", str(config)]) == 0
    site = tmp_path / "site"

    scan = json.loads((site / "data" / "scan.json").read_text(encoding="utf-8"))
    assert scan["schema_version"].startswith("2.")
    assert {s["ticker"] for s in scan["signals"]} == set(TICKERS)
    assert scan["counts"] == {"SELL_PREMIUM": 1, "BUY_PREMIUM": 1, "NO_DATA": 1}
    assert scan["universe"]["scanned"] == 3

    by = {s["ticker"]: s for s in scan["signals"]}
    assert by["AAA"]["recommendation"]["action"] == "SELL_PREMIUM"
    assert by["AAA"]["options"]["iv_rank"] == 88
    assert by["AAA"]["recommendation"]["plan"]["legs"]          # real legs, real prices
    assert by["BBB"]["recommendation"]["action"] == "BUY_PREMIUM"
    assert by["CCC"]["options"] is None
    assert by["CCC"]["recommendation"]["action"] == "NO_DATA"

    # The halal ratios and the flattened action columns ride along on every row.
    assert by["AAA"]["debt_ratio"] == 0.05
    assert by["AAA"]["strategy"] == by["AAA"]["recommendation"]["plan"]["name"]

    # The ~13-month spreads are built off the same views, on their own budget.
    assert scan["long_dated"]["tickers"] == 2
    assert scan["long_dated"]["candidates"] > 0
    ld = by["AAA"]["long_dated"]
    assert ld["dte"] > 270 and ld["expiry"] > by["AAA"]["options"]["expiry"]
    assert {c["key"] for c in ld["candidates"]} <= set(leaps.CANDIDATE_ORDER)
    assert by["CCC"]["long_dated"] is None, "an unpriced name has no long-dated chain"

    charts = json.loads((site / "data" / "charts.json").read_text(encoding="utf-8"))
    assert charts["count"] == 3

    csv = pd.read_csv(site / "data" / "signals.csv")
    assert set(csv["ticker"]) == set(TICKERS)
    assert "action" in csv.columns

    # And nothing HTML came out of the backend.
    assert not list(site.glob("**/*.html"))
    assert not list(site.glob("**/*.md"))

    out = capsys.readouterr().out
    assert "What to do:" in out
    assert "[SELL]" in out and "[BUY ]" in out


def test_cli_tickers_override_the_config(offline, config, tmp_path):
    assert run.main(["--config", str(config), "--tickers", "ZZZ,YYY"]) == 0
    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    assert {s["ticker"] for s in scan["signals"]} == {"ZZZ", "YYY"}
    assert scan["universe"]["source"] == "cli"


def test_outdir_flag_wins(offline, config, tmp_path):
    other = tmp_path / "elsewhere"
    assert run.main(["--config", str(config), "--outdir", str(other)]) == 0
    assert (other / "data" / "scan.json").exists()


def test_no_tickers_exits_nonzero(offline, tmp_path):
    cfg = tmp_path / "empty.yaml"
    cfg.write_text("tickers: []\nuniverse: {source: config}\n", encoding="utf-8")
    assert run.main(["--config", str(cfg)]) == 2


def test_options_disabled_still_produces_a_scan(offline, config, tmp_path):
    cfg = tmp_path / "nooptions.yaml"
    cfg.write_text(config.read_text(encoding="utf-8").replace(
        "options: {enabled: true, top_n: 3}", "options: {enabled: false}"), encoding="utf-8")
    assert run.main(["--config", str(cfg)]) == 0
    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    assert scan["counts"] == {"NO_DATA": 3}
    assert all(s["options"] is None for s in scan["signals"])


def test_hv_context_gives_the_options_layer_a_year_of_readings():
    raw = {t: _ohlcv(i) for i, t in enumerate(TICKERS)}
    now, hist = run._hv_context(raw, {"vol_lookback": 20})
    assert set(now) == set(TICKERS)
    for t in TICKERS:
        assert 0 < now[t] < 300                 # annualized %, not a fraction
        assert 200 <= len(hist[t]) <= 252
        assert hist[t][-1] == pytest.approx(now[t])


def test_hv_context_skips_frames_without_closes():
    now, hist = run._hv_context({"BARE": pd.DataFrame({"Volume": [1, 2]})}, {"vol_lookback": 20})
    assert now == {} and hist == {}


def _etf_config(tmp_path, tickers):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: etf, etfs: [SPUS, HLAL], max_holdings: 30,"
        " fallback_to_config: true}\n"
        "halal_screen: {financial_formula: {enabled: true, mode: filter}}\n"
        "options: {enabled: true, top_n: 3}\n"
        "strategy: {risk_budget_usd: 1000}\n"
        "charts: {enabled: false}\n"
        "alerts: {enabled: false}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(tickers)}]\n", encoding="utf-8")
    return cfg


def test_etf_universe_records_the_fallback_when_the_fetch_comes_back_empty(
        offline, tmp_path, monkeypatch):
    """A failed holdings fetch still produces a complete, valid scan — of the
    config watchlist rather than the funds. That substitution was visible only
    in the workflow log, so a dashboard served from the fallback was
    indistinguishable from one served from live holdings."""
    monkeypatch.setattr(run.universe, "fetch_halal_universe", lambda *a, **k: [])
    assert run.main(["--config", str(_etf_config(tmp_path, TICKERS))]) == 0

    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    uni = scan["universe"]
    assert uni["requested_source"] == "etf"
    assert uni["source"] == "config", "the effective source is what actually got scanned"
    assert uni["fallback"] and "SPUS, HLAL" in uni["fallback"]
    assert {s["ticker"] for s in scan["signals"]} == set(TICKERS)


def test_etf_universe_records_no_fallback_when_the_fetch_succeeds(
        offline, tmp_path, monkeypatch):
    monkeypatch.setattr(run.universe, "fetch_halal_universe", lambda *a, **k: list(TICKERS))
    assert run.main(["--config", str(_etf_config(tmp_path, ["ZZZ"]))]) == 0

    uni = json.loads((tmp_path / "site" / "data" / "scan.json")
                     .read_text(encoding="utf-8"))["universe"]
    assert uni["source"] == "etf" and uni["requested_source"] == "etf"
    assert uni["fallback"] is None
