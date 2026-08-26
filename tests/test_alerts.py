"""The webhook message says what to do, not just that something moved."""

import pandas as pd
import pytest

from spread_scanner import alerts, strategy
from conftest import make_row, make_view


def _rows(*tickers):
    return pd.DataFrame([make_row(t, score=75.0) for t in tickers])


def test_only_new_crossings_fire():
    df = _rows("AAA", "BBB")
    df.loc[1, "score"] = 40.0
    crossed = alerts._newly_crossed(df, 60.0, {"AAA": 30.0})
    assert list(crossed["ticker"]) == ["AAA"]
    # Already above the threshold last run -> no repeat.
    assert alerts._newly_crossed(df, 60.0, {"AAA": 70.0}).empty


def test_message_carries_the_recommendation():
    df = _rows("AAA")
    recs = strategy.recommend_all(df.to_dict("records"),
                                  {"AAA": make_view("AAA", iv=58, hv=28, iv_rank=88)})
    msg = alerts._format_message(df, 60.0, recs)
    assert "AAA" in msg
    assert "SELL premium" in msg
    assert "Iron Condor" in msg
    assert "IV rank" in msg
    assert "credit" in msg
    assert "not a fatwa" in msg


def test_message_degrades_gracefully_without_a_recommendation():
    msg = alerts._format_message(_rows("AAA"), 60.0, None)
    assert "AAA" in msg
    assert "no IV read this run" in msg


def test_maybe_alert_is_a_noop_without_a_webhook(monkeypatch, capsys):
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    assert alerts.maybe_alert(_rows("AAA"), 60.0) == 0
    assert "not set" in capsys.readouterr().out


def test_maybe_alert_posts_once_for_a_new_crossing(monkeypatch):
    sent = {}
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    monkeypatch.setattr(alerts, "_post", lambda url, msg: sent.update(url=url, msg=msg))
    df = _rows("AAA")
    recs = strategy.recommend_all(df.to_dict("records"),
                                  {"AAA": make_view("AAA", iv=18, hv=30, iv_rank=8)})
    assert alerts.maybe_alert(df, 60.0, {}, recommendations=recs) == 1
    assert "BUY premium" in sent["msg"]
    assert alerts.maybe_alert(df, 60.0, {"AAA": 75.0}) == 0     # no longer new


def test_a_failing_webhook_never_breaks_the_run(monkeypatch, capsys):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    def boom(url, msg):
        raise OSError("network down")
    monkeypatch.setattr(alerts, "_post", boom)
    assert alerts.maybe_alert(_rows("AAA"), 60.0, {}) == 0
    assert "failed to send" in capsys.readouterr().out
