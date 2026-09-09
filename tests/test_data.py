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


def _ohlcv(n=10, start=100.0, end=None):
    """`n` business days of bars ending today (or at `end`)."""
    idx = pd.bdate_range(end=end or pd.Timestamp.today().normalize(), periods=n)
    close = pd.Series(np.linspace(start, start * 1.1, n), index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 1e6}, index=idx)


def _multi(tickers, n=10, empty=()):
    """What yfinance returns for a multi-ticker request: columns keyed
    (ticker, field). A ticker in `empty` is present but all-NaN — which is
    exactly how a failed one comes back, since `_download_one` catches the
    error and files `utils.empty_df()` under that symbol."""
    frames = {t: _ohlcv(n, 100.0 + 50 * i) for i, t in enumerate(tickers)}
    for t in empty:
        frames[t] = _ohlcv(n) * np.nan
    return pd.concat(frames, axis=1)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """Every backoff in here is exercised; none of them is waited on."""
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
    assert set(out) == {"AAA"}, "a name that is simply dead stays dropped"


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

def test_a_transient_failure_is_retried(monkeypatch, capsys):
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


def test_a_persistent_failure_still_raises(monkeypatch):
    def dead(**kw):
        raise OSError("network down")
    monkeypatch.setattr(data.yf, "download", dead)
    with pytest.raises(OSError):
        data.download(["AAA"])


# ------------------------------------- the failure yfinance does not raise on

def test_a_ticker_that_came_back_empty_is_re_requested(monkeypatch, capsys):
    """`yf.download` does not raise when a ticker fails — `_download_one` catches
    everything, files an empty frame under that symbol and returns normally. So
    wrapping the call in `net.retry` never sees a rate-limited name: the only
    signal a caller gets is that the ticker is missing from the result."""
    calls = []

    def flaky(**kw):
        calls.append(list(kw["tickers"]))
        # First pass: BBB "fails" the way yfinance reports it — absent, no raise.
        return _multi(["AAA"] if len(calls) == 1 else ["BBB"])
    monkeypatch.setattr(data.yf, "download", flaky)

    out = data.download(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}
    assert calls == [["AAA", "BBB"], ["BBB"]], "only the missing name is re-requested"
    assert "came back empty" in capsys.readouterr().out


def test_the_re_request_is_bounded(monkeypatch):
    """A name that is dead stays dead: one extra request, not three."""
    calls = []

    def always_partial(**kw):
        calls.append(list(kw["tickers"]))
        return _multi(["AAA"], empty=["DEAD"]) if "AAA" in kw["tickers"] else _ohlcv() * np.nan
    monkeypatch.setattr(data.yf, "download", always_partial)
    out = data.download(["AAA", "DEAD"])
    assert set(out) == {"AAA"}
    assert len(calls) == 1 + data.MISSING_RETRIES


def test_an_all_nan_column_block_counts_as_missing(monkeypatch):
    """A failed ticker is not absent from the frame — it is present and empty."""
    monkeypatch.setattr(data.yf, "download",
                        lambda **kw: _multi(["AAA"], empty=["BBB"]))
    assert set(data.download(["AAA", "BBB"])) == {"AAA"}


def test_a_single_missing_ticker_comes_back_on_the_flat_path(monkeypatch):
    """The re-request is one ticker, so yfinance returns flat columns — the
    shape the multi-ticker guard refuses to file. It is filed here because the
    request really was for one name."""
    calls = []

    def flaky(**kw):
        calls.append(list(kw["tickers"]))
        return _multi(["AAA"]) if len(calls) == 1 else _ohlcv()
    monkeypatch.setattr(data.yf, "download", flaky)
    out = data.download(["AAA", "BBB"])
    assert set(out) == {"AAA", "BBB"}


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
    frames = {"AAA": _ohlcv(n=600)}          # ~2.5y of bars ending today
    out = data.slice_period(frames, "1y")
    assert len(out["AAA"]) < 600
    # The last bar is untouched and the window really is about a year.
    assert out["AAA"].index[-1] == frames["AAA"].index[-1]
    span = (out["AAA"].index[-1] - out["AAA"].index[0]).days
    assert 360 <= span <= 366


def test_slice_period_measures_from_today_not_from_the_frames_last_bar():
    """A ticker that stopped trading two years ago must not hand back a year of
    stale bars that score exactly like live ones. Asking Yahoo for `1y` of it
    returned nothing, and slicing has to mean the same thing."""
    today = pd.Timestamp("2026-09-09")
    stale = _ohlcv(n=600, end=today - pd.Timedelta(days=730))
    assert data.slice_period({"DEAD": stale}, "1y", now=today) == {}
    # And it is the window, not the frame, that decides: a longer window reaches
    # back far enough to find it.
    assert set(data.slice_period({"DEAD": stale}, "10y", now=today)) == {"DEAD"}


def test_slice_period_leaves_an_unparseable_period_alone():
    frames = {"AAA": _ohlcv(n=50)}
    assert data.slice_period(frames, "max") is frames
