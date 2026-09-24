"""check_musaffa.py: which names it checks, and never reading another stock's verdict."""

from __future__ import annotations

import json

import check_musaffa as cm


def _cfg(tmp_path, with_screened=True):
    site = tmp_path / "site"
    if with_screened:
        (site / "data").mkdir(parents=True)
        (site / "data" / "screened.json").write_text(
            json.dumps({"tickers": ["NVDA", "KO", "PG"]}), encoding="utf-8")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"output: {{dir: '{site}'}}\ntickers: [SPUS, HLAL, AAPL]\n", encoding="utf-8")
    return str(cfg)


def test_checks_the_names_the_scan_published(tmp_path):
    assert cm.load_tickers(_cfg(tmp_path), 2) == ["NVDA", "KO"]


def test_falls_back_to_the_config_list_before_any_scan(tmp_path):
    assert cm.load_tickers(_cfg(tmp_path, with_screened=False), 5) == ["SPUS", "HLAL", "AAPL"]


def test_a_page_without_the_stocks_own_block_is_unrated_not_guessed():
    html = '{"stock-overview:MSFT": {"shariahCompliantStatus":"COMPLIANT","compliantRanking":5}}'
    res = cm._extract(html, "KO")
    assert res["status_raw"] is None and res["status"].startswith("Unrated")
    ok = cm._extract(html.replace("MSFT", "KO"), "KO")
    assert ok["status"] == "Halal" and ok["ranking"] == 5
