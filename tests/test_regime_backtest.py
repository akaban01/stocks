"""The market-regime backtest (regime_backtest/): timing, costs, cash, the
monthly SMA, and the loaders' parsing. Network-free, like the rest of the suite."""

import numpy as np
import pandas as pd
import pytest

from regime_backtest import data, engine, events, metrics


def _bdays(n, start="2020-01-01"):
    return pd.bdate_range(start, periods=n)


def _zero_cash(idx):
    return pd.Series(0.0, index=idx)


# --- the monthly 10-month SMA ---------------------------------------------------

def test_monthly_sma_uses_exactly_ten_month_end_closes():
    idx = pd.bdate_range("2019-01-01", "2020-03-31")
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.normal(0, 5, len(idx)).cumsum(), index=idx)
    me = engine.month_end_closes(close)

    # One row per month, each the last trading day's close.
    assert len(me) == 15
    assert all(close.loc[d] == me.loc[d] for d in me.index)
    assert all(close.index[close.index.get_loc(d) + 1].month != d.month for d in me.index[:-1])

    sig = engine.monthly_sma_signal(close)
    # Undefined until the 10th month-end, defined (no NaN holes) from it on.
    tenth = me.index[9]
    assert sig.loc[:tenth].iloc[:-1].isna().all()
    assert sig.loc[tenth:].notna().all()

    # At each month-end the decision equals close > mean of exactly the last 10.
    for k in range(9, len(me)):
        expect = float(me.iloc[k] > me.iloc[k - 9:k + 1].mean())
        assert sig.loc[me.index[k]] == expect
    # And the 10-value mean is not the 9- or 11-value one (the test can tell).
    assert me.iloc[3:13].mean() != me.iloc[4:13].mean()


def test_monthly_signal_ignores_intra_month_prices_and_is_held_all_month():
    idx = pd.bdate_range("2019-01-01", "2020-03-31")
    close = pd.Series(np.linspace(100, 160, len(idx)), index=idx)
    me = engine.month_end_closes(close)
    noisy = close.copy()
    mid = ~noisy.index.isin(me.index)
    noisy[mid] = noisy[mid] * np.where(np.arange(mid.sum()) % 2, 0.5, 1.5)
    a, b = engine.monthly_sma_signal(close), engine.monthly_sma_signal(noisy)
    pd.testing.assert_series_equal(a, b)
    # Constant between month-ends.
    for d0, d1 in zip(me.index[9:], me.index[10:]):
        assert a.loc[d0:d1].iloc[:-1].nunique() == 1


def test_an_unfinished_final_month_is_not_a_month_end():
    idx = pd.bdate_range("2020-01-01", "2020-03-17")
    close = pd.Series(1.0, index=idx)
    me = engine.month_end_closes(close)
    assert list(me.index) == [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-28")]


# --- no look-ahead ---------------------------------------------------------------

def test_a_same_day_oracle_signal_earns_nothing():
    """The signal says "in" exactly on the days SPY rose. A look-ahead bug
    (holding it on day t instead of t+1) would never be in on a down day and
    would compound to an absurd number; the engine must not."""
    idx = _bdays(2000)
    rng = np.random.default_rng(42)
    ret = pd.Series(rng.choice([0.01, -0.01], size=len(idx)), index=idx)
    ret.iloc[0] = np.nan
    close = 100 * (1 + ret.fillna(0)).cumprod()
    signal = (close.pct_change() > 0).astype(float)      # known at the close of t

    cheat = (1 + ret.where(signal == 1, 0.0).fillna(0)).prod()
    assert cheat > 1e4                                   # what the bug would produce

    out = engine.run(signal, ret, _zero_cash(idx), cost_bp=0)
    total = (1 + out["ret"]).prod()
    assert total < 10
    # Exactly: the return on day t is the signal from t-1 times day t's return.
    expect = (signal.shift(1) * ret).loc[out.index]
    np.testing.assert_allclose(out["ret"].to_numpy(), expect.to_numpy())


def test_fred_series_get_one_extra_day_of_lag():
    idx = _bdays(6, "2024-01-01")
    s = pd.Series([1.0, 2.0, 3.0], index=[idx[0], idx[2], idx[4]])
    a = engine.align_fred(s, idx, lag=1)
    # The value dated idx[2] is first usable at the close of idx[3].
    assert np.isnan(a.iloc[0])
    assert list(a.iloc[1:]) == [1.0, 1.0, 2.0, 2.0, 3.0]


def test_rolling_signals_use_only_past_data():
    idx = _bdays(400)
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 1, len(idx)).cumsum(), index=idx)
    full = engine.sma_signal(close, 200)
    for cut in (250, 300, 399):
        part = engine.sma_signal(close.iloc[:cut], 200)
        pd.testing.assert_series_equal(part, full.iloc[:cut])
    z_full = engine.credit_velocity_z(close, window=60)
    z_part = engine.credit_velocity_z(close.iloc[:200], window=60)
    pd.testing.assert_series_equal(z_part, z_full.iloc[:200])


# --- costs -------------------------------------------------------------------------

def test_costs_are_charged_once_per_side_per_switch():
    idx = _bdays(8)
    zero = pd.Series(0.0, index=idx)
    # Held positions (signal shifted one day): 1 1 0 0 1 1 1 from idx[1].
    signal = pd.Series([1, 1, 0, 0, 1, 1, 1, 1], index=idx, dtype=float)
    out = engine.run(signal, zero, zero, cost_bp=10)
    # Initial purchase + out + back in = 3 sides, each 10 bp, on the day each takes effect.
    charged = out["ret"][out["ret"] < 0]
    assert len(charged) == 3
    np.testing.assert_allclose(charged.to_numpy(), -0.001)
    assert out["turnover"].sum() == 3
    np.testing.assert_allclose((1 + out["ret"]).prod(), (1 - 0.001) ** 3)
    assert metrics.summarize(out)["switches"] == 2

    free = engine.run(signal, zero, zero, cost_bp=0)
    assert (free["ret"] == 0).all()


def test_cost_is_applied_to_the_days_return_not_added_twice():
    idx = _bdays(3)
    r = pd.Series([np.nan, 0.02, 0.0], index=idx)
    out = engine.run(pd.Series(1.0, index=idx), r, _zero_cash(idx), cost_bp=5)
    np.testing.assert_allclose(out["ret"].iloc[0], 1.02 * (1 - 0.0005) - 1)
    assert out["ret"].iloc[1] == 0.0


# --- cash ---------------------------------------------------------------------------

def test_days_in_cash_earn_the_tbill_rate():
    idx = pd.bdate_range("2024-01-01", periods=15)
    tbill = pd.Series(3.6, index=pd.date_range("2023-12-01", "2024-02-01"))
    cash = engine.cash_returns(tbill, idx)
    # A weekday after a weekday earns one day; a Monday earns the weekend too.
    assert cash.loc["2024-01-03"] == pytest.approx(0.036 / 360)
    assert cash.loc["2024-01-08"] == pytest.approx(3 * 0.036 / 360)

    spy = pd.Series(0.05, index=idx)                     # SPY up 5% every day
    signal = pd.Series([1, 1, 1, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1], index=idx, dtype=float)
    out = engine.run(signal, spy, cash, cost_bp=0)
    out_days = out[out["pos"] == 0]
    assert len(out_days) == 5
    np.testing.assert_allclose(out_days["ret"].to_numpy(), cash.loc[out_days.index].to_numpy())
    np.testing.assert_allclose(out[out["pos"] == 1]["ret"].to_numpy(), 0.05)


def test_cash_uses_the_previous_published_rate():
    idx = pd.bdate_range("2024-01-01", periods=4)
    tbill = pd.Series([1.0, 1.0, 9.0, 9.0], index=idx)
    cash = engine.cash_returns(tbill, idx)
    # 9% is published on idx[2]; it is first known (lagged) on idx[3].
    assert cash.iloc[2] == pytest.approx(0.01 / 360)
    assert cash.iloc[3] == pytest.approx(0.09 / 360)


# --- credit hysteresis ------------------------------------------------------------

def test_hysteresis_exits_above_and_reenters_only_below_the_lower_threshold():
    m = pd.Series([np.nan, 0, 130, 100, 60, 30, 10, 40, 140, np.nan, 20])
    sig = engine.hysteresis(m, exit_above=125, reenter_below=25)
    assert list(sig.iloc[1:]) == [1, 0, 0, 0, 0, 1, 1, 0, 0, 1]
    assert np.isnan(sig.iloc[0])


def test_combined_signal_is_in_only_when_both_are():
    a = pd.Series([1, 1, 0, np.nan, 1.0])
    b = pd.Series([1, 0, 1, 1, np.nan])
    out = engine.combine_all_in(a, b)
    assert list(out.iloc[:3]) == [1, 0, 0]
    assert out.iloc[3:].isna().all()


# --- metrics and events ------------------------------------------------------------

def test_max_drawdown_and_spells():
    r = pd.Series([0.1, -0.5, 0.2, 1.0])
    assert metrics.max_drawdown(r) == pytest.approx(-0.5)
    assert metrics.spells(pd.Series([1, 1, 0, 1, 0, 0, 1, 1, 1.0])) == [2, 1, 3]


def test_event_clusters_need_a_twenty_day_gap():
    idx = _bdays(100)
    cond = pd.Series(False, index=idx)
    cond.iloc[[5, 6, 20, 41, 60, 85]] = True
    # 20 is 14 after 6; 41 is 21 after 20 -> new; 60 is 19 after 41; 85 is 25 after 60 -> new.
    assert events.first_of_clusters(cond) == [idx[5], idx[41], idx[85]]


def test_resteepening_needs_sixty_inverted_days():
    idx = _bdays(200)
    v = np.full(200, 0.5)
    v[10:40] = -0.2          # 30 days inverted: too short
    v[50:115] = -0.1         # 65 days inverted
    curve = pd.Series(v, index=idx)
    assert events.resteepening_dates(curve) == [idx[115]]


def test_forward_table_measures_recovery_to_the_prior_peak():
    idx = _bdays(10)
    close = pd.Series([100, 80, 85, 70, 90, 100, 105, 106, 107, 108.0], index=idx)
    t = events.forward_table(close, [idx[2]]).iloc[0]
    assert t["below_prior_peak"] == pytest.approx(-0.15)
    assert t["max_dd_before_recovery"] == pytest.approx(70 / 85 - 1)
    assert t["days_to_recover"] == 3


# --- loaders (no network) -----------------------------------------------------------

def test_fred_csv_missing_values_are_dropped():
    text = "observation_date,DTB3\n2024-01-02,5.2\n2024-01-03,.\n2024-01-04,\n2024-01-05,5.1\n"
    s = data.parse_fred_csv(text, "DTB3")
    assert list(s.values) == [5.2, 5.1]
    with pytest.raises(data.DataError):
        data.parse_fred_csv("<html>error</html>\n", "DTB3")


def test_spy_close_from_both_yfinance_shapes():
    idx = pd.bdate_range("2024-01-01", periods=3)
    flat = pd.DataFrame({"Open": [1, 2, 3], "Close": [10.0, 11.0, 12.0]}, index=idx)
    multi = flat.copy()
    multi.columns = pd.MultiIndex.from_product([multi.columns, ["SPY"]], names=["Price", "Ticker"])
    old = flat.assign(**{"Adj Close": [9.0, 10.0, 11.0]})
    assert list(data.close_from_yf(flat)) == [10.0, 11.0, 12.0]
    assert list(data.close_from_yf(multi)) == [10.0, 11.0, 12.0]
    assert list(data.close_from_yf(old)) == [9.0, 10.0, 11.0]


def test_credit_source_order(tmp_path, monkeypatch):
    def fake_get(url):
        if "alfred" in url:     # a truncated vintage, like the real one in 2026
            return b"observation_date,X\n2023-10-02,4.11\n2023-10-03,4.2\n"
        return b"observation_date,BAA10Y\n1986-01-02,2.34\n1986-01-03,2.3\n"
    monkeypatch.setattr(data, "_get", fake_get)
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    c = data.load_credit(data_dir=tmp_path)
    assert c.kind == "baa"
    assert "rejected" in c.log[1] and c.log[2].startswith("3. FRED BAA10Y")

    (tmp_path / "hy_oas_full.csv").write_text("date,value\n1996-12-31,3.1\n1997-01-02,3.2\n")
    c = data.load_credit(data_dir=tmp_path)
    assert c.kind == "hy" and len(c.series) == 2 and len(c.log) == 1


def _write_hy(path, start, value):
    idx = pd.bdate_range(start, periods=300)
    pd.DataFrame({"date": idx.strftime("%Y-%m-%d"), "value": value}).to_csv(
        path / "hy_oas_full.csv", index=False)


def test_a_hand_supplied_hy_file_must_reach_back_far_enough(tmp_path):
    _write_hy(tmp_path, "2010-01-04", 4.5)
    with pytest.raises(data.DataError, match="need history"):
        data.load_credit(data_dir=tmp_path)


def test_a_hand_supplied_hy_file_must_be_in_percent(tmp_path):
    _write_hy(tmp_path, "1997-01-02", 450.0)
    with pytest.raises(data.DataError, match="basis points"):
        data.load_credit(data_dir=tmp_path)


def test_fred_cache_is_reused_unless_refreshed(tmp_path, monkeypatch):
    calls = []

    def fake_get(url):
        calls.append(url)
        return b"observation_date,VIXCLS\n2024-01-02,13.2\n"
    monkeypatch.setattr(data, "_get", fake_get)
    data.load_fred("VIXCLS", data_dir=tmp_path)
    data.load_fred("VIXCLS", data_dir=tmp_path)
    assert len(calls) == 1
    data.load_fred("VIXCLS", refresh=True, data_dir=tmp_path)
    assert len(calls) == 2


def test_run_stops_with_install_hint_when_matplotlib_is_missing(monkeypatch, capsys):
    import sys

    from regime_backtest import run
    monkeypatch.setitem(sys.modules, "matplotlib", None)     # makes `import matplotlib` raise
    monkeypatch.setattr(run, "load_all", lambda **k: pytest.fail("must stop before loading data"))
    assert run.main([]) == 2
    assert "pip install -r requirements-backtest.txt" in capsys.readouterr().err


def _synthetic_inputs(n=2600):
    idx = pd.bdate_range("2000-01-03", periods=n)
    rng = np.random.default_rng(3)
    spy = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n))), index=idx)
    curve = pd.Series(np.where((np.arange(n) > 800) & (np.arange(n) < 900), -0.2, 0.8), index=idx)
    baa = pd.Series(2 + np.cumsum(rng.normal(0, 0.02, n)), index=idx)
    vix = pd.Series(18 + rng.normal(0, 3, n), index=idx)
    return data.Inputs(
        spy=spy, tbill=pd.Series(2.0, index=idx), vix=vix, vix3m=vix + rng.normal(0, 2, n),
        t10y2y=curve, credit=data.CreditData(baa, "baa", "synthetic BAA10Y", ["3. synthetic"]))


def test_report_has_bottom_line_glossary_and_embedded_charts(tmp_path):
    pytest.importorskip("matplotlib")
    from regime_backtest import run

    study = run.build(_synthetic_inputs(), cost_bp=5.0)
    text = run.write_report(study, tmp_path).read_text(encoding="utf-8")
    for needle in ("## Bottom line", "## How to read this", "(equity.png)", "(drawdowns.png)",
                   "(credit_heatmap.png)"):
        assert needle in text
    assert text.index("Bottom line") < text.index("Data sources and coverage")
    for png in ("equity.png", "drawdowns.png", "credit_heatmap.png"):
        assert (tmp_path / png).exists()
    # The credit rules carry the spread they were tested on, everywhere.
    assert "### Credit velocity (BAA10Y)" in text
    assert text.count(run.BAA_VERDICT_NOTE) == 2
    csv = pd.read_csv(tmp_path / "summary.csv")
    credit_rows = csv["strategy"].str.contains("redit velocity")
    assert csv.loc[credit_rows, "strategy"].str.contains(r"\(BAA10Y\)").all()


def test_a_rule_that_out_returns_buy_and_hold_is_not_said_to_pay_for_it(tmp_path):
    """A timing rule that sidesteps a slow crash has both a shallower drawdown
    and a higher CAGR. The headline must say it returned more, not that it
    "paid for" the drawdown with a negative cost."""
    pytest.importorskip("matplotlib")
    from regime_backtest import run

    inputs = _synthetic_inputs()
    n = len(inputs.spy)
    rng = np.random.default_rng(5)
    drift = np.full(n, 0.0006)
    drift[1200:1600] = -0.0025                 # a slow ~63% bear market the SMA rules can exit
    inputs.spy = pd.Series(100 * np.exp(np.cumsum(drift + rng.normal(0, 0.004, n))),
                           index=inputs.spy.index)
    study = run.build(inputs, cost_bp=5.0)

    full = study.frames[study.cost_bp]
    start, end = run.common_window(full)
    common = run.slice_frames(full, start, end)
    common20 = run.slice_frames(study.frames[20.0], start, end)
    st = {k: metrics.summarize(f) for k, f in common.items()}
    assert st[run.SMA200]["cagr"] > st[run.BH]["cagr"]
    head, _ = run.verdict_parts(run.SMA200, st, common, common20)
    assert "a year more" in head

    text = run.write_report(study, tmp_path).read_text(encoding="utf-8")
    assert "paid for with -" not in text


# --- the dashboard payload ---------------------------------------------------------

def test_dashboard_payload_matches_the_report_and_is_strict_json(tmp_path):
    import json

    from regime_backtest import export, run

    study = run.build(_synthetic_inputs(), cost_bp=5.0)
    path = export.write_payload(study, tmp_path / "regime.json")
    text = path.read_text(encoding="utf-8")
    d = json.loads(text)                      # write_payload used allow_nan=False, so this is real JSON

    assert d["regime_schema"] == export.REGIME_SCHEMA
    assert d["credit"]["label"] == "BAA10Y" and d["credit"]["is_proxy"] is True
    assert set(d["summary"]) == set(d["series"]["equity"]) == {"0", "5", "20"}

    # Every rule's curve sits on the one shared date axis.
    n = len(d["series"]["dates"])
    assert n > 100
    for cost in d["series"]["equity"].values():
        assert all(len(v) == n for v in cost.values())

    credit = [v for v in d["verdicts"] if "(BAA10Y)" in v["rule"]]
    assert len(credit) == 2 and all(v["note"] == run.BAA_VERDICT_NOTE for v in credit)

    # No credit-spread series is published (ICE licence, when HY OAS is in use):
    # the payload carries results and coverage counts, never the series itself.
    assert all(set(r) == {"series", "first", "last", "rows"} for r in d["coverage"])
    n_spread = len(study.inputs.credit.series)

    def list_lengths(x):
        if isinstance(x, dict):
            for v in x.values():
                yield from list_lengths(v)
        elif isinstance(x, list):
            yield len(x)
            for v in x:
                yield from list_lengths(v)
    assert n_spread not in set(list_lengths(d))


def test_dashboard_headlines_are_the_reports_word_for_word(tmp_path):
    pytest.importorskip("matplotlib")
    from regime_backtest import export, run

    study = run.build(_synthetic_inputs(), cost_bp=5.0)
    d = export.build_payload(study, "2026-01-01T00:00:00Z", "test")
    report_md = run.write_report(study, tmp_path).read_text(encoding="utf-8")
    for v in d["verdicts"]:
        assert f"- {v['rule']}: **{v['headline']}**" in report_md


def test_run_writes_the_payload_when_asked(tmp_path, monkeypatch):
    pytest.importorskip("matplotlib")
    from regime_backtest import run

    monkeypatch.setattr(run, "load_all", lambda **k: _synthetic_inputs())
    out = tmp_path / "regime.json"
    assert run.main(["--outdir", str(tmp_path), "--json", str(out)]) == 0
    assert out.exists() and (tmp_path / "report.md").exists()


def test_weekly_equity_samples_each_weeks_last_day():
    from regime_backtest import export

    idx = pd.bdate_range("2024-01-01", periods=12)          # Mon 1 Jan .. Tue 16 Jan
    f = pd.DataFrame({"ret": 0.01, "pos": 1.0, "cash": 0.0}, index=idx)
    out = export.weekly_equity({"5": {"A": f}})
    assert out["dates"] == ["2024-01-05", "2024-01-12", "2024-01-16"]
    assert out["equity"]["5"]["A"] == [pytest.approx(1.01 ** k, rel=1e-3) for k in (5, 10, 12)]
