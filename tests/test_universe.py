from spread_scanner import universe

SAMPLE = {"status": 200, "data": {"holdings": [
    {"no": 1, "n": "NVIDIA", "s": "$NVDA", "as": "13.92%", "sh": "1"},
    {"no": 2, "n": "Apple", "s": "$AAPL", "as": "11.97%", "sh": "1"},
    {"no": 3, "n": "Cash & Other", "s": "$N/A", "as": "0.50%", "sh": ""},
    {"no": 4, "n": "Berkshire B", "s": "$BRK.B", "as": "1.00%", "sh": "1"},
]}}


def test_parse_holdings_filters_and_parses():
    out = universe._parse_holdings(SAMPLE)
    tickers = [t for t, _ in out]
    assert "NVDA" in tickers
    assert "AAPL" in tickers
    assert "BRK.B" in tickers          # dotted class shares are valid
    assert "N/A" not in tickers        # cash / non-equity line skipped
    assert dict(out)["NVDA"] == 13.92


def test_parse_holdings_empty():
    assert universe._parse_holdings({}) == []
    assert universe._parse_holdings({"data": {}}) == []


def test_valid_ticker():
    assert universe._valid_ticker("AAPL")
    assert universe._valid_ticker("BRK.B")
    assert not universe._valid_ticker("")
    assert not universe._valid_ticker("N/A")        # slash not allowed
    assert not universe._valid_ticker("TOOLONGSYM")  # >6 chars
