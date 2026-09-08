"""The yfinance shape-normalization — the layer most likely to break silently.

Nothing here touches the network: `yf.download` is replaced with frames of the
shapes it actually returns. That is the point. The failure this module exists to
catch is a *shape* change upstream, and a shape change is exactly what a mock
can reproduce and a live call cannot be relied on to.
"""

import numpy as np
import pandas as pd
import pytest

from spread_scanner import data, net


def _ohlcv(n=10, start=100.0):
    idx = pd.bdate_range("2025-01-01", periods=n)
    close = pd.Series(np.linspace(start, start * 1.1, n), index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1e6}, index=idx)


def _multi(tickers, n=10):
    """What yfinance returns for a multi-ticker request: columns keyed (ticker, field)."""
    frames = {t: _ohlcv(n, 100.0 + 50 * i) for i, t in enumerate(tickers)}
    return pd.concat(frames, axis=1)


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)


# ------------------------------------------------------------------ the shapes

def test_multi_ticker_frame_is_split_by_ticker(monkeypatch):
    monkeypatch.setattr(data.yf, "download", lambda **kw: _multi(["AAA", "BBB"]))
    out = data.download(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert list(out["AAA"].columns) == ["Open", "High", "Low", "Close", "Volume"]
    # The frames are not the same data under two names.
    assert out["AAA"]["Close"].iloc[0] != out["BBB"]["Close"].iloc[0]


def test_a_ticker_missing_from_the_response_is_skipped(monkeypatch):
    monkeypatch.setattr(data.yf, "download", lambda **kw: _multi(["AAA"]))
    out = data.download(["AAA", "GONE"])
    assert set(out) == {"AAA"}


def test_single_ticker_flat_frame_is_filed_under_that_ticker(monkeypatch):
    monkeypatch.setattr(data.yf, "download", lambda **kw: _ohlcv())
    out = data.download(["AAA"])
    assert set(out) == {"AAA"}
    assert len(out["AAA"]) == 10


def test_a_flat_frame_for_a_multi_ticker_request_is_dropped_not_mislabelled(monkeypatch, capsys):
    """The frame carries no ticker anywhere. Filing it under `tickers[0]` is a
    guess with a company name on it — one survivor in the batch, or a yfinance
    shape change, and a whole price history would be published as the wrong
    company's."""
    monkeypatch.setattr(data.yf, "download", lambda **kw: _ohlcv())
    out = data.download(["AAA", "BBB", "CCC"])
    assert out == {}
    assert "flat columns" in capsys.readouterr().out


def test_empty_ticker_list_makes_no_call(monkeypatch):
    def boom(**kw):
        raise AssertionError("should not have been called")
    monkeypatch.setattr(data.yf, "download", boom)
    assert data.download([]) == {}
    assert data.download(["", "  "]) == {}


def test_tickers_are_upper_cased_before_the_request(monkeypatch):
    seen = {}

    def capture(**kw):
        seen.update(kw)
        return _multi(["AAA"])
    monkeypatch.setattr(data.yf, "download", capture)
    data.download([" aaa "])
    assert seen["tickers"] == ["AAA"]
    assert seen["auto_adjust"] is True and seen["group_by"] == "ticker"


def test_all_nan_rows_are_dropped(monkeypatch):
    frame = _multi(["AAA"])
    frame.iloc[0, :] = np.nan
    monkeypatch.setattr(data.yf, "download", lambda **kw: frame)
    assert len(data.download(["AAA"])["AAA"]) == 9


# ------------------------------------------------------------------- retrying

def test_a_transient_failure_is_retried(monkeypatch, no_sleep, capsys):
    """A single 429 from Yahoo used to drop every ticker in the batch for the
    day, with the whole scan built on nothing."""
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise OSError("429 Too Many Requests")
        return _multi(["AAA"])
    monkeypatch.setattr(data.yf, "download", flaky)
    assert set(data.download(["AAA"])) == {"AAA"}
    assert calls["n"] == 3
    assert "retrying" in capsys.readouterr().out


def test_a_persistent_failure_still_raises(monkeypatch, no_sleep):
    def dead(**kw):
        raise OSError("network down")
    monkeypatch.setattr(data.yf, "download", dead)
    with pytest.raises(OSError):
        data.download(["AAA"])


def test_retry_returns_the_first_success_without_sleeping():
    slept = []
    assert net.retry(lambda: 7, sleep=slept.append) == 7
    assert slept == []


# -------------------------------------------------------- one download, sliced

def test_period_days_parses_what_yfinance_speaks():
    assert data.period_days("1y") == 365
    assert data.period_days("6mo") == 180
    assert data.period_days("30d") == 30
    assert data.period_days("max") is None      # unparseable -> caller falls back
    assert data.period_days("ytd") is None
    assert data.period_days("") is None


def test_slice_period_keeps_only_the_tail():
    frames = {"AAA": _ohlcv(n=600)}
    out = data.slice_period(frames, "1y")
    assert len(out["AAA"]) < 600
    # The last bar is untouched and the window really is about a year.
    assert out["AAA"].index[-1] == frames["AAA"].index[-1]
    span = (out["AAA"].index[-1] - out["AAA"].index[0]).days
    assert 360 <= span <= 366


def test_slice_period_leaves_an_unparseable_period_alone():
    frames = {"AAA": _ohlcv(n=50)}
    assert data.slice_period(frames, "max") is frames
