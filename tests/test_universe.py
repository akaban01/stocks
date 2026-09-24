from spread_scanner import universe

# Trimmed from the real holdings page. The Svelte comment noise is kept
# deliberately: it sits between every cell, so a parser that assumes clean
# markup passes a handwritten fixture and fails the live page.
SAMPLE = """
<table><thead><tr><th>No.</th><th>Symbol</th><th>Name</th><th>% Weight</th></tr></thead>
<tbody><!--[-->
<tr class="svelte-mfd49r"><td class="rrpad">1</td><!--]-->
  <td><!----><a href="/stocks/nvda/" >NVDA</a><!----></td>
  <td class="shr">NVIDIA Corporation</td><td class="svelte-mfd49r">13.92%</td>
  <td class="hide-column-mobile">1,958,602</td></tr>
<tr class="svelte-mfd49r"><td class="rrpad">2</td>
  <td><a href="/stocks/aapl/" >AAPL</a></td>
  <td class="shr">Apple Inc.</td><td>11.97%</td><td>2,101,447</td></tr>
<tr class="svelte-mfd49r"><td class="rrpad">3</td>
  <td><a href="/stocks/brk.b/" >BRK.B</a></td>
  <td class="shr">Berkshire Hathaway</td><td>1.00%</td><td>12,004</td></tr>
<tr class="svelte-mfd49r"><td class="rrpad">4</td>
  <td>$N/A</td><td class="shr">Cash &amp; Other</td><td>0.50%</td><td></td></tr>
</tbody></table>
"""


def test_parse_holdings_filters_and_parses():
    out = universe._parse_holdings(SAMPLE)
    tickers = [t for t, _ in out]
    assert tickers == ["NVDA", "AAPL", "BRK-B"]   # and in the page's own order
    assert "N/A" not in tickers        # cash / non-equity line has no stock link
    assert dict(out)["NVDA"] == 13.92


def test_class_shares_come_out_in_yahoos_spelling():
    """The holdings page writes BRK.B; every Yahoo endpoint wants BRK-B and
    returns nothing for the dotted form, so the dot never leaves this module."""
    assert universe.to_yahoo("BRK.B") == "BRK-B"
    assert universe.to_yahoo("brk.b") == "BRK-B"
    assert universe.to_yahoo("BRK-B") == "BRK-B"       # idempotent
    assert universe.to_yahoo(" nvda ") == "NVDA"
    assert [t for t, _ in universe._parse_holdings(SAMPLE)] == ["NVDA", "AAPL", "BRK-B"]


def test_parse_holdings_takes_the_weight_not_another_number():
    """The weight is the first percentage in the row, not the share count."""
    row = ('<tr><td>1</td><td><a href="/stocks/msft/">MSFT</a></td>'
           '<td>Microsoft</td><td>9.62%</td><td>1,234,567</td></tr>')
    assert universe._parse_holdings(row) == [("MSFT", 9.62)]


def test_parse_holdings_keeps_a_row_whose_weight_is_missing():
    row = '<tr><td><a href="/stocks/amd/">AMD</a></td><td>Advanced Micro</td></tr>'
    assert universe._parse_holdings(row) == [("AMD", 0.0)]


def test_parse_holdings_empty():
    assert universe._parse_holdings("") == []
    assert universe._parse_holdings("<html><body>no table here</body></html>") == []
    # A page that still renders rows but no longer links to stock pages is the
    # shape change that quietly emptied this universe once already.
    assert universe._parse_holdings("<tr><td>NVDA</td><td>13.92%</td></tr>") == []


def test_valid_ticker():
    assert universe._valid_ticker("AAPL")
    assert universe._valid_ticker("BRK.B")
    assert not universe._valid_ticker("")
    assert not universe._valid_ticker("N/A")        # slash not allowed
    assert not universe._valid_ticker("TOOLONGSYM")  # >6 chars


def test_fetch_halal_universe_ranks_by_weight_and_dedups(monkeypatch):
    pages = {
        "SPUS": [("NVDA", 14.15), ("AAPL", 11.84), ("MU", 2.69)],
        "HLAL": [("NVDA", 12.90), ("META", 3.27), ("MU", 2.68)],
    }
    monkeypatch.setattr(universe, "fetch_etf_holdings", lambda s, **k: pages[s])
    out = universe.fetch_halal_universe(["SPUS", "HLAL"], max_holdings=3)
    # Highest weight seen for a name wins, so NVDA carries SPUS's 14.15.
    assert out == ["NVDA", "AAPL", "META"]


def test_fetch_halal_universe_empty_when_every_fetch_fails(monkeypatch):
    monkeypatch.setattr(universe, "fetch_etf_holdings", lambda s, **k: [])
    assert universe.fetch_halal_universe(["SPUS", "HLAL"]) == []


ISSUER_CSV = """Date,Account,StockTicker,CUSIP,SecurityName,Shares,Price,MarketValue,Weightings,NetAssets
09/23/2026,SPUS,NVDA,67066G104,NVIDIA Corp,2007686,228.87,459499094.82,14.02%,3276653985.0
09/23/2026,SPUS,BRK.B,000000000,Berkshire,1,1,1,1.50%,3276653985.0
09/23/2026,SPUS,Cash&Other,Cash&Other,Cash & Other,1,1,1,0.10%,3276653985.0
"""


def test_issuer_csv_parses_ticker_and_weight_and_drops_cash():
    rows = universe._parse_issuer_csv(ISSUER_CSV)
    assert rows == [("NVDA", 14.02), ("BRK-B", 1.5)]
    assert universe._parse_issuer_csv("nothing,useful\n1,2\n") == []


def test_issuer_csv_is_tried_before_the_page(monkeypatch):
    calls = []

    def fake_get(url, timeout):
        calls.append(url)
        if url.endswith(".csv"):
            return ISSUER_CSV
        raise AssertionError("the page should not be fetched when the CSV worked")

    monkeypatch.setattr(universe, "_get", fake_get)
    rows = universe.fetch_etf_holdings("SPUS")
    assert rows[0] == ("NVDA", 14.02) and len(calls) == 1


def test_page_is_the_fallback_when_the_csv_is_empty(monkeypatch):
    page = '<tr><td><a href="/stocks/aapl/">AAPL</a></td><td>9.5%</td></tr>'
    monkeypatch.setattr(universe, "_get", lambda url, timeout: "" if url.endswith(".csv") else page)
    assert universe.fetch_etf_holdings("SPUS") == [("AAPL", 9.5)]


def test_last_good_list_is_saved_and_used_when_live_fetch_fails(tmp_path, monkeypatch):
    cache = tmp_path / "universe.json"
    monkeypatch.setattr(universe, "fetch_halal_universe", lambda s, m, csv_urls=None: ["NVDA", "AAPL"])
    assert universe.resolve_universe(["SPUS"], 30, cache) == (["NVDA", "AAPL"], None)

    monkeypatch.setattr(universe, "fetch_halal_universe", lambda s, m, csv_urls=None: [])
    tickers, as_of = universe.resolve_universe(["SPUS"], 30, cache)
    assert tickers == ["NVDA", "AAPL"] and as_of
    # A list saved for other funds, or another cap, is not this universe.
    assert universe.resolve_universe(["HLAL"], 30, cache) == ([], None)
    assert universe.resolve_universe(["SPUS"], 10, cache) == ([], None)


def test_share_classes_of_one_company_take_one_slot(monkeypatch):
    pages = {"SPUS": [("GOOGL", 5.2), ("GOOG", 4.3), ("AAPL", 9.0), ("MSFT", 8.0)]}
    monkeypatch.setattr(universe, "fetch_etf_holdings", lambda s, **k: pages[s])
    # Alphabet's combined 9.5% outranks Apple's 9.0% as one entry.
    assert universe.fetch_halal_universe(["SPUS"], max_holdings=3) == ["GOOGL", "AAPL", "MSFT"]


def test_screened_list_is_what_validation_measures(tmp_path, monkeypatch):
    universe.save_screened(tmp_path / "data" / "screened.json", ["NVDA", "AAPL"], "filter")
    cfg = {"universe": {"source": "etf"}, "tickers": ["X"]}
    monkeypatch.setattr(universe, "from_config", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("must not fetch when a screened list exists")))
    tickers, source = universe.for_validation(cfg, tmp_path)
    assert tickers == ["NVDA", "AAPL"] and "screened" in source


def test_validation_falls_back_without_a_screened_list(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "from_config", lambda uni, outdir: (["NVDA"], None))
    assert universe.for_validation({"universe": {"source": "etf"}}, tmp_path)[0] == ["NVDA"]
    assert universe.for_validation({"tickers": ["X"]}, tmp_path) == (["X"], "the config watchlist")
