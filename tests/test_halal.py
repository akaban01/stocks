import time

from spread_scanner import halal


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
