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
    assert tickers == ["NVDA", "AAPL", "BRK.B"]   # and in the page's own order
    assert "BRK.B" in tickers          # dotted class shares are valid
    assert "N/A" not in tickers        # cash / non-equity line has no stock link
    assert dict(out)["NVDA"] == 13.92


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
