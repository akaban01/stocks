"""The webhook message says what to do, not just that something moved."""

import pandas as pd

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
    # The blended premium score, named as itself. It is not IV rank, and the
    # message used to say it was.
    assert "premium 88/100 rich" in msg
    assert "IV rank" not in msg
    assert "credit" in msg
    assert "Not financial advice" in msg


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


def test_staged_alerts_are_not_sent_until_asked(tmp_path, monkeypatch):
    """Nothing leaves the machine at scan time: the run stages, and the send
    happens after the scan has been validated."""
    sent = []
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    monkeypatch.setattr(alerts, "_post", lambda url, msg: sent.append(msg))
    df = _rows("AAA")
    payload = alerts.build_alert(df, 60.0, {}, None)
    assert payload["tickers"] == ["AAA"]

    path = alerts.stage(payload, tmp_path / "alert.json")
    assert path.exists() and sent == []

    assert alerts.send_staged(path) == 1
    assert len(sent) == 1
    # The file is consumed, so a later run can never re-send yesterday's alert.
    assert not path.exists()
    assert alerts.send_staged(path) == 0


def test_nothing_crossing_stages_nothing():
    df = _rows("AAA")
    assert alerts.build_alert(df, 60.0, {"AAA": 70.0}) is None


def test_a_failing_webhook_never_breaks_the_run(monkeypatch, capsys):
    monkeypatch.setenv("ALERT_WEBHOOK_URL", "https://hooks.slack.test/x")
    def boom(url, msg):
        raise OSError("network down")
    monkeypatch.setattr(alerts, "_post", boom)
    assert alerts.maybe_alert(_rows("AAA"), 60.0, {}) == 0
    assert "failed to send" in capsys.readouterr().out
