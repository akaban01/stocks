"""calibrate.py's refit schedule: reuse the committed fit until it is due."""

from __future__ import annotations

import datetime as dt
import json

import calibrate
from spread_scanner import backtest

WEIGHTS = {"compression": 0.3, "vol_room": 0.5, "squeeze": 0.2}


def _cal(tmp_path, as_of: str, ok: bool = True, method=backtest.CALIBRATION_METHOD):
    path = tmp_path / "calibration.json"
    path.write_text(json.dumps({"ok": ok, "as_of": as_of, "weights": WEIGHTS,
                                "method_version": method}), encoding="utf-8")
    return path


def test_recent_fit_is_reused_and_written_to_the_weights_file(tmp_path):
    cal = _cal(tmp_path, "2026-09-01")
    wfile = tmp_path / "weights.json"
    out = calibrate.reuse_recent_fit(cal, wfile, 30, dt.date(2026, 9, 20))
    assert out == {"weights": WEIGHTS, "as_of": "2026-09-01"}
    written = json.loads(wfile.read_text(encoding="utf-8"))
    assert written["weights"] == WEIGHTS and written["as_of"] == "2026-09-01"


def test_stale_failed_or_missing_fit_is_refitted(tmp_path):
    wfile = tmp_path / "weights.json"
    today = dt.date(2026, 9, 20)
    assert calibrate.reuse_recent_fit(_cal(tmp_path, "2026-08-01"), wfile, 30, today) is None
    assert calibrate.reuse_recent_fit(_cal(tmp_path, "2026-09-10", ok=False), wfile, 30, today) is None
    assert calibrate.reuse_recent_fit(tmp_path / "nope.json", wfile, 30, today) is None
    assert calibrate.reuse_recent_fit(_cal(tmp_path, "2026-09-19"), wfile, 0, today) is None
    assert not wfile.exists()


def test_a_fit_from_an_older_method_is_refitted_not_reused(tmp_path):
    wfile = tmp_path / "weights.json"
    today = dt.date(2026, 9, 20)
    assert calibrate.reuse_recent_fit(_cal(tmp_path, "2026-09-19", method=None), wfile, 30, today) is None
    assert calibrate.reuse_recent_fit(_cal(tmp_path, "2026-09-19", method=1), wfile, 30, today) is None
