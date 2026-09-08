import time

import pandas as pd
import pytest

from spread_scanner import halal, net


def test_industry_blocks_prohibited():
    for industry in ["Banks - Diversified", "Insurance - Life", "Tobacco",
                     "Beverages - Brewers", "Gambling", "Aerospace & Defense"]:
        ok, _ = halal._industry_check({"sector": "X", "industry": industry})
        assert ok is False, industry


def test_industry_allows_clean():
    # "Beverages - Non-Alcoholic" is the key regression: must NOT match "alcohol".
    for industry in ["Semiconductors", "Consumer Electronics", "Oil & Gas Integrated",
                     "Beverages - Non-Alcoholic", "Drug Manufacturers - General"]:
        ok, _ = halal._industry_check({"sector": "X", "industry": industry})
        assert ok is True, industry


def test_industry_fails_open_on_missing_data():
    ok, _ = halal._industry_check({})
    assert ok is True


def test_ratio_math():
    assert halal._ratio(33, 100) == 0.33
    assert halal._ratio(0, 100) == 0.0      # zero numerator -> 0, not missing
    assert halal._ratio(10, 0) is None
    assert halal._ratio(None, 100) is None
    assert halal._ratio(50, None) is None


def test_days_to_earnings():
    assert halal._days_to_earnings({"earningsTimestamp": time.time() + 5 * 86400}) in (4, 5)
    assert halal._days_to_earnings({"earningsTimestamp": time.time() - 86400}) is None
    assert halal._days_to_earnings({}) is None


def test_days_to_earnings_picks_soonest_future():
    now = time.time()
    info = {"earningsTimestamp": now - 86400,            # past
            "earningsTimestampStart": now + 10 * 86400,   # future
            "earningsTimestampEnd": now + 20 * 86400}
    assert halal._days_to_earnings(info) in (9, 10)


# ---------------------------------------------------------- the whole screen
#
# `financial_screen`, `screen_universe`, `_receivables` and `earnings_calendar`
# were the untested half of this module: every path that talks to yfinance, and
# the fail-open behaviour that decides whether a fetch error quietly rejects a
# name or quietly keeps it.

class FakeTicker:
    def __init__(self, info=None, balance_sheet=None, raises=0):
        self._info = info or {}
        self.balance_sheet = (pd.DataFrame() if balance_sheet is None else balance_sheet)
        self.raises = raises
        self.info_calls = 0

    def get_info(self):
        self.info_calls += 1
        if self.raises:
            self.raises -= 1
            raise OSError("Yahoo said no")
        return self._info


@pytest.fixture(autouse=True)
def _never_sleep(monkeypatch):
    monkeypatch.setattr(net.time, "sleep", lambda s: None)


def _patch(monkeypatch, tickers):
    monkeypatch.setattr(halal.yf, "Ticker", lambda t: tickers[t])


CLEAN = {"sector": "Technology", "industry": "Semiconductors",
         "marketCap": 1_000_000, "totalDebt": 50_000, "totalCash": 30_000}


def test_a_clean_name_passes_with_its_ratios(monkeypatch):
    _patch(monkeypatch, {"AAA": FakeTicker(CLEAN)})
    res = halal.financial_screen("AAA")
    assert res.compliant is True and res.industry_ok is True
    assert res.debt_ratio == pytest.approx(0.05)
    assert res.cash_ratio == pytest.approx(0.03)
    assert res.reasons == ["ok"]


def test_too_much_debt_fails_and_says_by_how_much(monkeypatch):
    _patch(monkeypatch, {"AAA": FakeTicker({**CLEAN, "totalDebt": 500_000})})
    res = halal.financial_screen("AAA")
    assert res.compliant is False
    assert "debt/mktcap 50%" in res.reasons[0]


def test_a_prohibited_industry_fails_whatever_the_balance_sheet_says(monkeypatch):
    _patch(monkeypatch, {"AAA": FakeTicker({**CLEAN, "industry": "Banks - Diversified"})})
    res = halal.financial_screen("AAA")
    assert res.compliant is False and res.industry_ok is False
    assert "prohibited industry" in res.reasons[0]


def test_the_screen_fails_open_on_a_fetch_error(monkeypatch):
    """A name is not rejected because Yahoo hiccupped — only on a clear breach."""
    _patch(monkeypatch, {"AAA": FakeTicker(CLEAN, raises=99)})
    res = halal.financial_screen("AAA")
    assert res.compliant is True
    assert res.debt_ratio is None
    assert "info error" in res.reasons[0]


def test_a_transient_error_is_retried_rather_than_failing_open(monkeypatch):
    tk = FakeTicker(CLEAN, raises=2)
    _patch(monkeypatch, {"AAA": tk})
    res = halal.financial_screen("AAA")
    assert tk.info_calls == 3
    assert res.reasons == ["ok"] and res.debt_ratio == pytest.approx(0.05)


def test_missing_market_cap_leaves_the_ratios_unknown(monkeypatch):
    _patch(monkeypatch, {"AAA": FakeTicker({"sector": "Technology",
                                            "industry": "Semiconductors"})})
    res = halal.financial_screen("AAA")
    assert res.compliant is True                      # unknown is not a breach
    assert res.debt_ratio is None and res.cash_ratio is None


def test_receivables_are_only_read_when_a_limit_is_set(monkeypatch):
    bs = pd.DataFrame({"2025-01-01": [400_000]}, index=["Accounts Receivable"])
    _patch(monkeypatch, {"AAA": FakeTicker(CLEAN, balance_sheet=bs)})
    assert halal.financial_screen("AAA").receivables_ratio is None

    res = halal.financial_screen("AAA", max_receivables=0.33)
    assert res.receivables_ratio == pytest.approx(0.4)
    assert res.compliant is False
    assert "receivables/mktcap 40%" in res.reasons[-1]


def test_a_balance_sheet_without_receivables_is_not_a_failure(monkeypatch):
    _patch(monkeypatch, {"AAA": FakeTicker(CLEAN)})
    res = halal.financial_screen("AAA", max_receivables=0.33)
    assert res.receivables_ratio is None and res.compliant is True


def test_screen_universe_splits_kept_from_dropped_and_keeps_every_verdict(monkeypatch):
    _patch(monkeypatch, {
        "AAA": FakeTicker(CLEAN),
        "BBB": FakeTicker({**CLEAN, "industry": "Banks - Diversified"}),
        "CCC": FakeTicker({**CLEAN, "totalCash": 900_000}),
    })
    kept, dropped, details = halal.screen_universe(["AAA", "BBB", "CCC"])
    assert kept == ["AAA"]
    assert [t for t, _ in dropped] == ["BBB", "CCC"]
    # Every name has a verdict, including the ones that were dropped — that is
    # what `annotate` mode publishes.
    assert set(details) == {"AAA", "BBB", "CCC"}
    assert details["CCC"].compliant is False


def test_earnings_calendar_reads_the_date_without_the_rest_of_the_screen(monkeypatch):
    soon = time.time() + 6 * 86400
    _patch(monkeypatch, {"AAA": FakeTicker({**CLEAN, "earningsTimestamp": soon}),
                         "BBB": FakeTicker(CLEAN),
                         "CCC": FakeTicker(CLEAN, raises=99)})
    out = halal.earnings_calendar(["AAA", "BBB", "CCC"])
    assert out["AAA"] in (5, 6)
    assert out["BBB"] is None                 # no date published
    assert out["CCC"] is None                 # fetch failed — unknown, not zero


def test_filter_tickers_keeps_the_clean_and_reports_the_reason(monkeypatch):
    _patch(monkeypatch, {"AAA": FakeTicker(CLEAN),
                         "BBB": FakeTicker({**CLEAN, "industry": "Tobacco"})})
    kept, dropped = halal.filter_tickers(["AAA", "BBB"])
    assert kept == ["AAA"]
    assert dropped == [("BBB", "prohibited industry: Tobacco")]
