"""End-to-end wiring: run.py from CLI args to the JSON on disk, no network."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import run
from spread_scanner import data, halal, leaps, options
from conftest import make_view

TICKERS = ["AAA", "BBB", "CCC"]


def _ohlcv(seed, n=320):
    """Bars ending today, the way a live download comes back. Not a fixed
    start date: `data.slice_period` measures the scan window from today, so a
    frame pinned to 2024 is a delisted ticker as far as the scan is concerned —
    which is the point of that rule, and would quietly empty this fixture."""
    rng = np.random.RandomState(seed)
    px = (40 + seed * 30) * np.exp(np.cumsum(rng.normal(0.0004, 0.013, n)))
    close = pd.Series(px, index=pd.bdate_range(end=pd.Timestamp.today().normalize(),
                                               periods=n))
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
    # No webhook configured, so nothing can leave even if something is staged.
    # (run.py stages and never sends; send_alerts.py is what posts.)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(halal, "earnings_calendar",
                        lambda tickers: {t: 12 for t in tickers})


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

    # The same download reduced again, for the Repeat test. Every series is
    # exactly as long as the shared week axis — that is what makes "eight weeks
    # later" eight positions later rather than eight rows.
    weekly = json.loads((site / "data" / "weekly.json").read_text(encoding="utf-8"))
    assert weekly["count"] == 3
    axis = len(weekly["weeks"])
    assert axis == len(weekly["starts"]) > 0
    for series in weekly["series"]:
        assert len(series["close"]) == len(series["high"]) == len(series["low"]) == axis
    assert weekly["reference"]["hit"], "the rules the payload is read by ship with it"

    csv = pd.read_csv(site / "data" / "signals.csv")
    assert set(csv["ticker"]) == set(TICKERS)
    assert "action" in csv.columns

    # And nothing HTML came out of the backend.
    assert not list(site.glob("**/*.html"))
    assert not list(site.glob("**/*.md"))

    out = capsys.readouterr().out
    assert "What to do:" in out
    assert "[SELL]" in out and "[BUY ]" in out


def test_weekly_can_be_switched_off(offline, config, tmp_path):
    cfg = tmp_path / "off.yaml"
    cfg.write_text(config.read_text(encoding="utf-8") + "weekly: {enabled: false}\n",
                   encoding="utf-8")
    assert run.main(["--config", str(cfg)]) == 0
    site = tmp_path / "site"
    assert (site / "data" / "charts.json").exists()
    assert not (site / "data" / "weekly.json").exists()


def test_weekly_is_written_even_with_the_charts_off(offline, tmp_path):
    """The two payloads share one download but not one switch.

    They started as one block, so turning the charts off would have taken the
    weekly bars with it — and the Repeat test would have gone blank because of a
    setting about price cards.
    """
    cfg = tmp_path / "nocharts.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: config}\n"
        "options: {enabled: false}\n"
        "charts: {enabled: false, history_period: 2y}\n"
        "weekly: {enabled: true}\n"
        "alerts: {enabled: false}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(TICKERS)}]\n", encoding="utf-8")
    assert run.main(["--config", str(cfg)]) == 0
    site = tmp_path / "site"
    assert not (site / "data" / "charts.json").exists()
    weekly = json.loads((site / "data" / "weekly.json").read_text(encoding="utf-8"))
    assert weekly["count"] == 3


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


def test_the_configured_budget_reaches_the_published_sizing(offline, tmp_path):
    """config.yaml's strategy.risk_budget_usd is what the cards are sized
    against, so a change to it has to show up in the payload — that number is
    also what the dashboard headline quotes back to the reader."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: config}\n"
        "halal_screen: {financial_formula: {enabled: true, mode: filter}}\n"
        "options: {enabled: true, top_n: 3}\n"
        "strategy: {risk_budget_usd: 1000}\n"
        "charts: {enabled: false}\n"
        "alerts: {enabled: false}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(TICKERS)}]\n", encoding="utf-8")
    assert run.main(["--config", str(cfg)]) == 0

    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    budgets = {s["recommendation"]["plan"]["sizing"]["risk_budget"]
               for s in scan["signals"]
               if (s["recommendation"]["plan"].get("sizing") or {}).get("risk_budget")}
    assert budgets == {1000.0}


def test_shipped_config_still_parses_and_carries_a_budget():
    """The repo's own config.yaml is what every scheduled run uses; a typo in
    it would only surface in production."""
    import yaml
    cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    strat = cfg["strategy"]
    assert isinstance(strat["risk_budget_usd"], (int, float))
    assert strat["risk_budget_usd"] > 0
    # The long-dated budget is deliberately separate and larger: a LEAPS spread
    # costs several times a monthly one and holds the capital for a year.
    assert strat["long_risk_budget_usd"] > strat["risk_budget_usd"]


# ------------------------------------------------- the screen's own verdict

def _screen_config(tmp_path, mode, tickers=TICKERS):
    cfg = tmp_path / f"{mode}.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: config}\n"
        f"halal_screen: {{financial_formula: {{enabled: true, mode: {mode}}}}}\n"
        "options: {enabled: true, top_n: 3}\n"
        "strategy: {risk_budget_usd: 1000}\n"
        "charts: {enabled: false}\n"
        "alerts: {enabled: false}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(tickers)}]\n", encoding="utf-8")
    return cfg


@pytest.fixture
def mixed_screen(monkeypatch):
    """BBB fails the industry screen; the other two pass."""
    def fake_screen(tickers, **kw):
        details = {}
        for t in tickers:
            fails = t == "BBB"
            details[t] = halal.ScreenResult(
                ticker=t, compliant=not fails, industry_ok=not fails,
                debt_ratio=0.41 if fails else 0.05, cash_ratio=0.03,
                receivables_ratio=None,
                industry="Banks—Diversified" if fails else "Semiconductors",
                reasons=["prohibited industry: Banks—Diversified"] if fails else ["ok"],
                earnings_in_days=40)
        kept = [t for t in tickers if details[t].compliant]
        dropped = [(t, "; ".join(details[t].reasons)) for t in tickers if not details[t].compliant]
        return kept, dropped, details
    monkeypatch.setattr(halal, "screen_universe", fake_screen)


def test_annotate_mode_publishes_why_a_name_failed(offline, mixed_screen, tmp_path):
    """`annotate` keeps the names that fail the screen. Before this, only the
    two ratios reached the payload — no verdict, no reason — so a name kept for
    being a bank rendered identically to one that passed, on a page whose whole
    premise is that its contents have been screened."""
    assert run.main(["--config", str(_screen_config(tmp_path, "annotate")),
                     "--alert-file", str(tmp_path / "alert.json")]) == 0
    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))

    assert {s["ticker"] for s in scan["signals"]} == set(TICKERS), "annotate keeps everything"
    by = {s["ticker"]: s for s in scan["signals"]}
    assert by["BBB"]["screen"]["compliant"] is False
    assert by["BBB"]["screen"]["industry_ok"] is False
    assert "Banks" in by["BBB"]["screen"]["reasons"][0]
    assert by["AAA"]["screen"]["compliant"] is True

    assert scan["screen"]["mode"] == "annotate"
    assert scan["screen"]["flagged"] == ["BBB"]
    assert scan["screen"]["flagged_count"] == 1
    assert scan["screen"]["screened"] == 3


def test_filter_mode_drops_the_failing_name_and_still_says_why_the_rest_passed(
        offline, mixed_screen, tmp_path):
    assert run.main(["--config", str(_screen_config(tmp_path, "filter")),
                     "--alert-file", str(tmp_path / "alert.json")]) == 0
    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    assert {s["ticker"] for s in scan["signals"]} == {"AAA", "CCC"}
    assert scan["screen"]["mode"] == "filter"
    assert scan["screen"]["flagged"] == []
    assert all(s["screen"]["compliant"] for s in scan["signals"])


def test_a_name_the_screen_missed_is_published_as_unscreened(offline, tmp_path, monkeypatch):
    """`screen_universe` returns a verdict per ticker, so this needs a join to
    drift. When it does, "we did not check this" has to be said out loud: it is
    a different claim from "this passed", and it is the one that must not be
    silent on a page whose premise is a screened watchlist."""
    def partial_screen(tickers, **kw):
        covered = [t for t in tickers if t != "CCC"]
        details = {t: halal.ScreenResult(t, True, True, 0.05, 0.03, None,
                                         "Semiconductors", ["ok"], 40)
                   for t in covered}
        return list(tickers), [], details
    monkeypatch.setattr(halal, "screen_universe", partial_screen)

    assert run.main(["--config", str(_screen_config(tmp_path, "annotate")),
                     "--alert-file", str(tmp_path / "alert.json")]) == 0
    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    by = {s["ticker"]: s for s in scan["signals"]}

    assert set(by) == set(TICKERS), "the row is published, not dropped"
    assert by["CCC"]["screen"]["compliant"] is None
    assert "not checked" in by["CCC"]["screen"]["reasons"][0]
    assert by["AAA"]["screen"]["compliant"] is True
    assert scan["screen"]["unknown"] == ["CCC"]
    assert scan["screen"]["unknown_count"] == 1
    assert scan["screen"]["flagged"] == [], "unscreened is not the same as failed"


# ------------------------------------------------------- the earnings column

def test_earnings_are_fetched_when_the_financial_screen_never_ran(offline, tmp_path):
    """The guardrail that forces defined risk into a print reads one column.
    Attached only inside the financial-formula branch, a `--tickers` run had the
    check silently switched off while the table's Earnings column — all dashes —
    looked exactly like "nothing due"."""
    cfg = tmp_path / "sector.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: config}\n"
        "halal_screen: {live_sector_filter: false}\n"
        "options: {enabled: true, top_n: 3}\n"
        "strategy: {risk_budget_usd: 1000}\n"
        "charts: {enabled: false}\n"
        "alerts: {enabled: false}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(TICKERS)}]\n", encoding="utf-8")
    assert run.main(["--config", str(cfg), "--alert-file", str(tmp_path / "alert.json")]) == 0

    scan = json.loads((tmp_path / "site" / "data" / "scan.json").read_text(encoding="utf-8"))
    by = {s["ticker"]: s for s in scan["signals"]}
    assert by["AAA"]["earnings_in_days"] == 12
    # And it reaches the plan: 12 days is inside the front expiry, so the naked
    # structure is off the table and the warning is on the card.
    assert by["AAA"]["recommendation"]["plan"]["risk"] == "defined"
    assert any("Earnings in 12 days" in w for w in by["AAA"]["recommendation"]["warnings"])
    assert scan["screen"]["earnings_checked"] == 3


# ------------------------------------------------------------ staged alerts

def test_alerts_are_staged_not_sent(offline, tmp_path, monkeypatch):
    """run.py writes the message; send_alerts.py posts it — after the workflow
    step that decides whether the scan is publishable at all."""
    posted = []
    monkeypatch.setattr(run.alerts, "_post", lambda url, msg: posted.append(msg))
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    cfg = tmp_path / "alerting.yaml"
    cfg.write_text(
        "params: {horizon_days: 10, history_period: 1y, percentile_lookback: 120}\n"
        "universe: {source: config}\n"
        "halal_screen: {financial_formula: {enabled: true, mode: filter}}\n"
        "options: {enabled: true, top_n: 3}\n"
        "strategy: {risk_budget_usd: 1000}\n"
        "charts: {enabled: false}\n"
        "alerts: {enabled: true, score_threshold: 10}\n"
        f"output: {{dir: '{tmp_path / 'site'}', top: 30}}\n"
        f"tickers: [{', '.join(TICKERS)}]\n", encoding="utf-8")
    staged = tmp_path / "alert.json"
    assert run.main(["--config", str(cfg), "--alert-file", str(staged)]) == 0

    assert posted == [], "nothing may be posted before the scan is validated"
    payload = json.loads(staged.read_text(encoding="utf-8"))
    assert set(payload["tickers"]) == set(TICKERS)
    assert "Spread Scanner" in payload["message"]

    import send_alerts
    assert send_alerts.main(["--file", str(staged)]) == 0
    assert len(posted) == 1
    assert not staged.exists()


def test_a_run_with_nothing_crossing_stages_no_alert(offline, config, tmp_path):
    staged = tmp_path / "alert.json"
    staged.write_text('{"tickers": ["STALE"], "message": "yesterday"}', encoding="utf-8")
    assert run.main(["--config", str(config), "--alert-file", str(staged)]) == 0
    # Yesterday's staged message is cleared at the start of the run, so a later
    # send can never post a scan that no longer exists.
    assert not staged.exists()
