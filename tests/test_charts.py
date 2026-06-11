import numpy as np
import pandas as pd

from spread_scanner import charts


def _synth(n=500, seed=0, start="2022-01-03"):
    """~2 years of daily closes on a business-day index."""
    rng = np.random.RandomState(seed)
    px = [100.0]
    for _ in range(n - 1):
        px.append(px[-1] * (1 + rng.normal(0.0005, 0.01)))
    idx = pd.bdate_range(start=start, periods=n)
    close = pd.Series(px, index=idx)
    return pd.DataFrame({"Open": close, "High": close * 1.01,
                         "Low": close * 0.99, "Close": close, "Volume": 1e6})


def test_write_charts_emits_a_card_per_ticker(tmp_path):
    data = {"NVDA": _synth(seed=1), "AAPL": _synth(seed=2)}
    path = charts.write_charts(data, tmp_path, period_label="5y")
    assert path.exists() and path.name == "charts.html"
    html = path.read_text(encoding="utf-8")
    assert "<svg" in html
    assert html.count('class="card"') == 2
    assert "NVDA" in html and "AAPL" in html
    # Year-boundary gridlines should appear (data spans >1 calendar year).
    assert 'class="grid"' in html
    assert 'href="index.html"' in html        # back-nav link


def test_change_pct_matches_known_move():
    idx = pd.bdate_range("2024-01-01", periods=400)
    close = pd.Series(np.linspace(100.0, 200.0, 400), index=idx)
    assert charts._change_pct(close, days=None) == 100.0  # full window doubled
    yoy = charts._change_pct(close, days=365)
    assert yoy is not None and yoy > 0                    # up over the trailing year


def test_skips_series_with_no_close(tmp_path):
    data = {"GOOD": _synth(seed=3), "BARE": pd.DataFrame({"Volume": [1, 2, 3]})}
    html = charts.write_charts(data, tmp_path).read_text(encoding="utf-8")
    assert html.count('class="card"') == 1                # only GOOD charted
    assert "GOOD" in html


def test_empty_input_renders_placeholder(tmp_path):
    html = charts.write_charts({}, tmp_path).read_text(encoding="utf-8")
    assert "No price data available." in html
