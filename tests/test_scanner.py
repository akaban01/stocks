import numpy as np
import pandas as pd

from spread_scanner import scanner

PARAMS = dict(horizon_days=10, bb_length=20, bb_mult=2.0, kc_length=20, kc_mult=1.5,
              atr_length=14, vol_lookback=20, percentile_lookback=120)


def _synth(n=200, seed=0):
    rng = np.random.RandomState(seed)
    px = [100.0]
    for _ in range(n - 1):
        px.append(px[-1] * (1 + rng.normal(0, 0.01)))
    close = pd.Series(px)
    return pd.DataFrame({"Open": close, "High": close * 1.005,
                         "Low": close * 0.995, "Close": close, "Volume": 1e6})


def test_analyze_returns_well_formed_signal():
    sig = scanner.analyze("X", _synth(), PARAMS)
    assert sig is not None
    assert 0 <= sig.score <= 100
    assert sig.down_1sigma < sig.price < sig.up_1sigma
    assert sig.down_2sigma < sig.down_1sigma
    assert sig.em_pct > 0
    assert sig.horizon_days == 10


def test_expected_move_scales_with_sqrt_of_horizon():
    df = _synth(seed=3)
    s10 = scanner.analyze("X", df, {**PARAMS, "horizon_days": 10})
    s40 = scanner.analyze("X", df, {**PARAMS, "horizon_days": 40})
    # 4x the horizon -> ~2x the expected move (sqrt scaling), sigma unchanged.
    assert abs(s40.em_pct / s10.em_pct - 2.0) < 0.01


def test_too_short_history_returns_none():
    assert scanner.analyze("X", _synth(n=10), PARAMS) is None


def test_squeeze_fired_detection(monkeypatch):
    df = _synth(n=80).copy()
    # Force the squeeze to be ON for all but the final bar (released today).
    states = pd.Series([True] * (len(df) - 1) + [False], index=df.index)
    monkeypatch.setattr(scanner.ind, "ttm_squeeze", lambda *a, **k: states)
    # Make the last close clearly higher -> break direction "up".
    df.iloc[-1, df.columns.get_loc("Close")] = df["Close"].iloc[-2] * 1.05
    sig = scanner.analyze("X", df, PARAMS)
    assert sig.squeeze_on is False
    assert sig.squeeze_fired is True
    assert sig.fired_dir == "up"


def test_score_weights_normalized():
    assert abs(sum(scanner.SCORE_WEIGHTS.values()) - 1.0) < 1e-9
    assert set(scanner.SCORE_WEIGHTS) == {"compression", "vol_room", "squeeze"}


def test_score_bounds_and_monotonicity():
    # Max coil (all favorable, long squeeze) -> high; nothing -> low.
    hi = scanner._setup_score(True, 15, 0.0, 0.0)
    lo = scanner._setup_score(False, 0, 1.0, 1.0)
    assert 0 <= lo < hi <= 100
    assert hi == 100  # fully coiled saturates


def test_scan_ranks_by_score():
    data = {"A": _synth(seed=1), "B": _synth(seed=2), "C": _synth(seed=5)}
    out = scanner.scan(data, PARAMS)
    assert list(out["rank"]) == [1, 2, 3]
    assert out["score"].is_monotonic_decreasing
