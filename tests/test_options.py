import datetime as dt

from spread_scanner import options


def test_classify_verdict():
    assert options.classify_verdict(5.0, 10.0, 0.15) == "cheap"   # implied << hist
    assert options.classify_verdict(15.0, 10.0, 0.15) == "rich"   # implied >> hist
    assert options.classify_verdict(10.0, 10.0, 0.15) == "fair"
    assert options.classify_verdict(5.0, 0.0, 0.15) == "fair"     # no history -> fair


def test_nearest_expiry_picks_closest_future():
    today = dt.date.today()
    exps = [(today + dt.timedelta(days=d)).isoformat() for d in [1, 7, 14, 30]]
    expiry, days = options._nearest_expiry(exps, target_days=14)
    assert days == 14


def test_nearest_expiry_ignores_past():
    today = dt.date.today()
    exps = [(today - dt.timedelta(days=5)).isoformat(),
            (today + dt.timedelta(days=20)).isoformat()]
    expiry, days = options._nearest_expiry(exps, target_days=14)
    assert days == 20


def test_nearest_expiry_none_when_all_past():
    past = [(dt.date.today() - dt.timedelta(days=3)).isoformat()]
    assert options._nearest_expiry(past, 14) is None
