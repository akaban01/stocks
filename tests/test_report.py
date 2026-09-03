"""The JSON payload — the backend's only output."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from spread_scanner import leaps, report, strategy
from conftest import make_row, make_view

PARAMS = {"horizon_days": 10, "vol_lookback": 20, "percentile_lookback": 120}


def _frame(n=3):
    rows = []
    for i, t in enumerate(["AAA", "BBB", "CCC"][:n]):
        row = make_row(t, rank=i + 1, score=80 - i * 20)
        rows.append(row)
    return pd.DataFrame(rows)


# ------------------------------------------------------------- the payload

def test_scan_payload_has_the_documented_shape(tmp_path):
    df = _frame()
    views = {"AAA": make_view("AAA", iv=58, hv=28, iv_rank=88)}
    recs = strategy.recommend_all(df.to_dict("records"), views)
    path = report.write_scan(df, tmp_path, PARAMS, recommendations=recs,
                             option_views=views, playbook=strategy.PLAYBOOK)

    assert path == tmp_path / "data" / "scan.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == report.SCHEMA_VERSION
    assert payload["horizon_days"] == 10
    assert len(payload["signals"]) == 3
    assert payload["counts"]["SELL_PREMIUM"] == 1
    assert payload["counts"]["NO_DATA"] == 2
    for key in ("actions", "premium_states", "glossary", "playbook"):
        assert payload["reference"][key], f"reference.{key} is empty"
    assert payload["disclaimer"]["risk"]


def test_signals_carry_their_own_options_block_and_recommendation(tmp_path):
    df = _frame()
    views = {"AAA": make_view("AAA", iv=18, hv=30, iv_rank=8)}
    recs = strategy.recommend_all(df.to_dict("records"), views)
    payload = json.loads(
        report.write_scan(df, tmp_path, PARAMS, recommendations=recs,
                          option_views=views).read_text(encoding="utf-8"))

    priced = payload["signals"][0]
    assert priced["ticker"] == "AAA"
    assert priced["options"]["iv_rank"] == 8
    assert "chain" not in priced["options"]           # never serialize the raw chain
    assert priced["recommendation"]["action"] == "BUY_PREMIUM"

    unpriced = payload["signals"][1]
    assert unpriced["options"] is None
    assert unpriced["recommendation"]["action"] == "NO_DATA"


def test_top_actions_lists_only_tradable_names_best_first(tmp_path):
    df = _frame()
    views = {"AAA": make_view("AAA", iv=58, hv=28, iv_rank=88),
             "BBB": make_view("BBB", iv=31, hv=30, iv_rank=45)}
    recs = strategy.recommend_all(df.to_dict("records"), views)
    payload = report.build_scan(df, PARAMS, recommendations=recs, option_views=views)

    tops = payload["top_actions"]
    assert all(t["action"] != "NO_DATA" for t in tops)
    assert all(t["action"] != "STAND_ASIDE" for t in tops)
    confs = [t["confidence"] for t in tops]
    assert confs == sorted(confs, reverse=True)


def test_empty_scan_still_writes_a_valid_payload(tmp_path):
    path = report.write_scan(pd.DataFrame(), tmp_path, PARAMS)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["signals"] == []
    assert payload["counts"] == {}
    assert payload["top_actions"] == []
    assert not (tmp_path / "data" / "signals.csv").exists()   # nothing to tabulate


def test_csv_is_written_alongside_the_json(tmp_path):
    report.write_scan(_frame(), tmp_path, PARAMS)
    csv = pd.read_csv(tmp_path / "data" / "signals.csv")
    assert list(csv["ticker"]) == ["AAA", "BBB", "CCC"]


def test_weights_block_records_provenance(tmp_path):
    payload = report.build_scan(_frame(), PARAMS,
                                weights={"compression": 0.3, "vol_room": 0.5, "squeeze": 0.2},
                                weights_as_of="2026-08-01")
    assert payload["weights"]["source"] == "auto-calibrated"
    assert payload["weights"]["as_of"] == "2026-08-01"
    assert report.build_scan(_frame(), PARAMS)["weights"]["source"] == "default"


# ----------------------------------------------------------- serialization

def test_clean_maps_missing_values_to_null():
    assert report._clean(float("nan")) is None
    assert report._clean(float("inf")) is None
    assert report._clean(np.float64("nan")) is None
    assert report._clean(pd.NA) is None
    assert report._clean(pd.NaT) is None


def test_clean_unwraps_numpy_and_pandas_scalars():
    assert report._clean(np.int64(7)) == 7
    assert report._clean(np.float64(1.5)) == 1.5
    assert report._clean(np.bool_(True)) is True
    assert report._clean(pd.Timestamp("2026-01-02")) == "2026-01-02T00:00:00"


def test_clean_recurses_through_containers():
    out = report._clean({"a": [1, float("nan"), {"b": np.int64(3)}]})
    assert out == {"a": [1, None, {"b": 3}]}


def test_nan_columns_survive_the_round_trip(tmp_path):
    df = _frame()
    df["earnings_in_days"] = [5.0, float("nan"), None]
    df["debt_ratio"] = [0.1, np.nan, 0.3]
    payload = json.loads(
        report.write_scan(df, tmp_path, PARAMS).read_text(encoding="utf-8"))
    assert payload["signals"][0]["earnings_in_days"] == 5.0
    assert payload["signals"][1]["earnings_in_days"] is None
    assert payload["signals"][1]["debt_ratio"] is None
    # No literal NaN in the file — JSON.parse in the browser would choke on it.
    assert "NaN" not in (tmp_path / "data" / "scan.json").read_text(encoding="utf-8")


def test_write_json_creates_missing_directories(tmp_path):
    path = report.write_json(tmp_path / "deep" / "nested" / "x.json", {"ok": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"ok": True}


# --------------------------------------------------- the copy ships with data

def test_every_action_the_engine_emits_has_reference_copy():
    emitted = {"BUY_PREMIUM", "SELL_PREMIUM", "NEUTRAL_INCOME", "STAND_ASIDE", "NO_DATA"}
    assert emitted <= set(report.ACTIONS)
    for meta in report.ACTIONS.values():
        assert meta["label"] and meta["blurb"] and meta["tone"]


def test_every_premium_state_has_a_rule_the_ui_can_show():
    from spread_scanner import options
    states = {options.premium_state(s) for s in (0, 50, 100)} | {"unknown"}
    assert states <= set(report.PREMIUM_STATES)
    for meta in report.PREMIUM_STATES.values():
        assert meta["label"] and meta["rule"] and meta["detail"]


# ------------------------------------------------- the long-dated block

def test_long_dated_blocks_ride_along_on_their_signal():
    df = _frame()
    views = {"AAA": make_view("AAA", iv=58, hv=28, iv_rank=88)}
    recs = strategy.recommend_all(df.to_dict("records"), views)
    blocks = leaps.long_spreads_all(df.to_dict("records"), views,
                                    biases={"AAA": ("bullish", "strong")})
    payload = report.build_scan(df, PARAMS, recommendations=recs, option_views=views,
                                long_spreads=blocks,
                                playbook={**strategy.PLAYBOOK, **leaps.PLAYBOOK})

    by = {s["ticker"]: s for s in payload["signals"]}
    assert by["AAA"]["long_dated"]["candidates"]
    assert by["AAA"]["long_dated"]["preferred"] == "leaps_bull_put"
    # A name with no option view has no long-dated block — not an empty one.
    assert by["BBB"]["long_dated"] is None

    summary = payload["long_dated"]
    assert summary["tickers"] == 1
    assert summary["candidates"] == len(by["AAA"]["long_dated"]["candidates"])
    assert summary["preferred"] == 1
    assert summary["expiries"] == [by["AAA"]["long_dated"]["expiry"]]

    # Every candidate's structure is explained by the shipped playbook copy.
    for c in by["AAA"]["long_dated"]["candidates"]:
        assert payload["reference"]["playbook"][c["key"]]


def test_a_scan_with_no_long_dated_chains_still_has_a_well_formed_summary():
    df = _frame()
    payload = report.build_scan(df, PARAMS)
    assert payload["long_dated"] == {"tickers": 0, "candidates": 0, "preferred": 0,
                                     "expiries": [], "target_days": None}
    assert all(s["long_dated"] is None for s in payload["signals"])


def test_the_shipped_copy_carries_no_religious_framing():
    """The screen itself is unchanged; the dashboard copy is not framed around it."""
    blob = json.dumps(report.build_scan(_frame(), PARAMS)).lower()
    for word in ("halal", "shariah", "sharia", "fatwa", "islamic", "musaffa", "zoya"):
        assert word not in blob, f"{word!r} still ships in the payload"


# ------------------------------------------------- option feed health

def _sig(ticker, iv):
    row = make_row(ticker)
    row["options"] = None if iv is None else {"iv_annual": iv}
    return row


def test_a_working_feed_passes():
    """Real post-close values, taken from the run of 2026-09-02."""
    health = report.option_data_health(
        [_sig(t, iv) for t, iv in
         (("MU", 51.27), ("AMD", 47.58), ("GOOGL", 27.82))])
    assert health["ok"]
    assert health["usable"] == health["priced"] == 3
    assert health["median_iv"] == 47.58      # odd count, so unambiguous


def test_an_empty_feed_is_refused_with_the_numbers_in_the_reason():
    """Real pre-market values, taken from runs 68 and 69: every contract
    present, every one of them empty."""
    health = report.option_data_health(
        [_sig(t, iv) for t, iv in
         (("MU", 0.1), ("AMD", 0.39), ("GOOGL", 0.78), ("QCOM", 0.05))])
    assert not health["ok"]
    assert health["usable"] == 0 and health["priced"] == 4
    assert "0 of 4" in health["reason"]
    assert "outside US market hours" in health["reason"]


def test_a_scan_with_no_option_layer_at_all_is_not_a_failure():
    """Options switched off is not the same as options broken."""
    health = report.option_data_health([_sig("AAA", None), _sig("BBB", None)])
    assert health["ok"] and health["priced"] == 0 and health["median_iv"] is None


def test_a_single_odd_name_does_not_condemn_a_working_feed():
    """The check exists to catch a feed that is down, not a thin contract."""
    health = report.option_data_health(
        [_sig("AAA", 31.0), _sig("BBB", 44.0), _sig("CCC", 0.2)])
    assert health["ok"] and health["usable"] == 2


def test_the_threshold_is_where_it_says_it_is():
    assert report.option_data_health([_sig("AAA", report.MIN_PLAUSIBLE_IV)])["ok"]
    assert not report.option_data_health([_sig("AAA", report.MIN_PLAUSIBLE_IV - 0.01)])["ok"]
