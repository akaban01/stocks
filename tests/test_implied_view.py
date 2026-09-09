"""`implied_view` / `screen_options` — chain assembly, trimming, term structure
and the long-dated expiry pick.

The rest of options.py was well covered; this half — the part that talks to
yfinance and assembles what every downstream engine reads — was not covered at
all. The fake below returns the shape `yf.Ticker` returns (an object with
`.options` and `.option_chain(expiry).calls/.puts` as DataFrames), so the
assembly runs for real without a network call.
"""

import datetime as dt
import math

import pandas as pd
import pytest

from spread_scanner import net, options


def _chain_frame(spot, iv_pct, strikes, oi=1500, spread_frac=0.02, no_quotes=False):
    rows = []
    for k in strikes:
        mid = max(0.05, spot - k) + spot * iv_pct / 100 * 0.05
        rows.append({
            "strike": float(k),
            "bid": 0.0 if no_quotes else round(mid * (1 - spread_frac), 2),
            "ask": 0.0 if no_quotes else round(mid * (1 + spread_frac), 2),
            "lastPrice": round(mid, 2),
            "impliedVolatility": iv_pct / 100,
            "openInterest": 0 if no_quotes else oi,
            "volume": 0 if no_quotes else oi // 4,
        })
    return pd.DataFrame(rows)


class FakeChain:
    def __init__(self, calls, puts):
        self.calls, self.puts = calls, puts


class FakeTicker:
    """`yf.Ticker` as this module uses it. `ivs` maps expiry -> ATM IV%, so a
    term structure and a separately-quoted LEAPS chain can be described."""

    def __init__(self, spot, ivs, strikes=None, oi=1500, spread_frac=0.02,
                 fail_on=(), no_quotes=(), raise_expiries=0):
        self.spot, self.ivs = spot, ivs
        self.strikes = strikes or [spot + i * 5 for i in range(-12, 13)]
        self.oi, self.spread_frac = oi, spread_frac
        self.fail_on, self.no_quotes = set(fail_on), set(no_quotes)
        self.raise_expiries = raise_expiries
        self.calls_made = []

    @property
    def options(self):
        if self.raise_expiries:
            self.raise_expiries -= 1
            raise OSError("429 Too Many Requests")
        return tuple(sorted(self.ivs))

    def option_chain(self, expiry):
        self.calls_made.append(expiry)
        if expiry in self.fail_on:
            raise OSError("chain fetch failed")
        iv = self.ivs[expiry]
        quiet = expiry in self.no_quotes
        side = _chain_frame(self.spot, iv, self.strikes, self.oi, self.spread_frac, quiet)
        return FakeChain(side.copy(), side.copy())


@pytest.fixture(autouse=True)
def _never_sleep(monkeypatch):
    """Retries are exercised here; the backoff is not."""
    monkeypatch.setattr(net.time, "sleep", lambda s: None)


def _exp(days):
    return (dt.date.today() + dt.timedelta(days=days)).isoformat()


def _view(monkeypatch, tk, **kw):
    monkeypatch.setattr(options.yf, "Ticker", lambda ticker: tk)
    kw.setdefault("horizon_days", 10)
    kw.setdefault("hv_annual", 30.0)
    kw.setdefault("hv_history", [25.0 + (i % 20) for i in range(252)])
    return options.implied_view("TEST", tk.spot, 5.0, **kw)


@pytest.fixture
def standard():
    """Front ~21d, back ~60d in mild contango, and a LEAPS chain quoted lower."""
    return FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 44.0, _exp(400): 32.0})


# --------------------------------------------------------------- what it reads

def test_the_view_is_assembled_off_the_real_chain(monkeypatch, standard):
    v = _view(monkeypatch, standard)
    assert v.ticker == "TEST" and v.spot == 200.0
    assert v.iv_annual == 40.0
    assert v.days_to_expiry == 21
    assert v.expiry == _exp(21)
    # Implied move is the annual IV scaled to the horizon, comparable 1:1 with
    # the scanner's realized-vol move.
    assert v.implied_move_pct == pytest.approx(40.0 * math.sqrt(10 / 252), abs=0.01)
    assert v.iv_hv_ratio == pytest.approx(40.0 / 30.0, abs=0.01)
    assert v.vrp == pytest.approx(10.0, abs=0.01)
    assert v.liquidity == "good"


def test_the_front_expiry_covers_the_horizon(monkeypatch):
    """It must expire *after* the move it is meant to price."""
    tk = FakeTicker(100.0, {_exp(3): 40.0, _exp(20): 40.0, _exp(70): 41.0})
    v = _view(monkeypatch, tk, horizon_days=10)
    assert v.days_to_expiry == 20


def test_term_structure_comes_from_the_back_months_own_iv(monkeypatch, standard):
    v = _view(monkeypatch, standard)
    assert v.term_slope == pytest.approx((44.0 - 40.0) / 40.0, abs=1e-4)
    assert v.term_structure == "contango"


def test_backwardation_is_read_the_other_way(monkeypatch):
    tk = FakeTicker(200.0, {_exp(21): 55.0, _exp(60): 45.0})
    v = _view(monkeypatch, tk)
    assert v.term_slope < 0
    assert v.term_structure == "backwardation"


def test_the_long_dated_expiry_is_the_one_nearest_thirteen_months(monkeypatch):
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0, _exp(300): 33.0, _exp(400): 32.0})
    v = _view(monkeypatch, tk)
    assert v.long_expiry == _exp(400) and v.long_dte == 400
    assert v.long_iv == 32.0, "the LEAPS chain is read at its own volatility"
    assert v.long_liquidity == "good"


def test_no_leaps_listed_means_no_long_dated_block(monkeypatch):
    """Better than building a 13-month plan on a 4-month contract."""
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0, _exp(120): 39.0})
    v = _view(monkeypatch, tk)
    assert v.long_expiry is None and v.long_dte is None and v.long_iv is None
    assert v.long_liquidity == "unknown"


def test_the_long_chain_is_skipped_when_it_is_switched_off(monkeypatch, standard):
    v = _view(monkeypatch, standard, long_dated=False)
    assert v.long_expiry is None
    assert _exp(400) not in standard.calls_made, "no third call was made"


# ------------------------------------------------------------- strike trimming

def test_each_expiry_is_trimmed_by_its_own_volatility(monkeypatch):
    """A year of sigma reaches far past the front month's strikes. Sizing the
    LEAPS window off the front month's IV cut away exactly the deep-ITM and
    far-OTM legs the long-dated structures are built from."""
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0, _exp(400): 32.0},
                    strikes=[100 + 5 * i for i in range(41)])       # 100 … 300
    v = _view(monkeypatch, tk)

    front = sorted(v.chain[v.expiry]["call"])
    long = sorted(v.chain[v.long_expiry]["call"])
    assert min(long) < min(front) and max(long) > max(front)

    # The window is 3σ of *that* expiry, at that expiry's own IV.
    sig = 32.0 / 100 * math.sqrt(400 / 365)
    assert min(long) >= 200 * (1 - 3 * sig) - 5
    assert max(long) <= 200 * (1 + 3 * sig) + 5


def test_trimming_keeps_the_at_the_money_strikes(monkeypatch, standard):
    v = _view(monkeypatch, standard)
    for expiry in v.chain:
        assert 200.0 in v.chain[expiry]["call"]
        assert 200.0 in v.chain[expiry]["put"]


# ----------------------------------------------------------------- fail-soft

def test_a_name_with_no_expiries_returns_nothing(monkeypatch):
    tk = FakeTicker(200.0, {})
    assert _view(monkeypatch, tk) is None


def test_a_failed_front_chain_returns_nothing(monkeypatch):
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0}, fail_on=[_exp(21)])
    assert _view(monkeypatch, tk) is None


def test_a_failed_back_chain_still_gives_a_view_without_a_term_read(monkeypatch):
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0}, fail_on=[_exp(60)])
    v = _view(monkeypatch, tk)
    assert v is not None and v.iv_annual == 40.0
    assert v.term_slope is None and v.term_structure == "unknown"


def test_the_expiry_list_is_retried_before_giving_up(monkeypatch):
    """One 429 used to drop the name from the scan for the day."""
    monkeypatch.setattr(net.time, "sleep", lambda s: None)
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0}, raise_expiries=2)
    assert _view(monkeypatch, tk) is not None


def test_a_chain_with_no_quotes_in_it_still_reports_its_own_emptiness(monkeypatch):
    """Outside US market hours every contract comes back with no bid, no ask and
    no open interest. The view is still built — the health check downstream is
    what refuses to publish it — but the liquidity read must not claim good."""
    tk = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0},
                    no_quotes=[_exp(21), _exp(60)])
    v = _view(monkeypatch, tk)
    assert v.liquidity in ("poor", "unknown")
    assert v.atm_open_interest in (0, None)


# ------------------------------------------------------------- screen_options

def test_screen_options_maps_tickers_to_views_and_skips_the_unreadable(monkeypatch):
    good = FakeTicker(200.0, {_exp(21): 40.0, _exp(60): 41.0})
    empty = FakeTicker(50.0, {})
    monkeypatch.setattr(options.yf, "Ticker",
                        lambda ticker: {"AAA": good, "BBB": empty}[ticker])
    out = options.screen_options([("AAA", 200.0, 5.0), ("BBB", 50.0, 4.0)],
                                 horizon_days=10,
                                 hv_annual={"AAA": 30.0, "BBB": 20.0},
                                 hv_history={"AAA": [25.0 + i % 20 for i in range(252)]})
    assert set(out) == {"AAA"}
    assert out["AAA"].iv_rank is not None
    assert out["AAA"].premium_state in ("cheap", "fair", "rich")
