"""scripts/fetch_site_data.py — the Netlify build step that downloads the large
data files from the site-data branch, and the pointer the workflow writes."""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_site_data.py"


@pytest.fixture
def fsd(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("fetch_site_data", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "DATA", tmp_path)
    monkeypatch.setattr(mod, "POINTER", tmp_path / "site-data.json")
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    monkeypatch.delenv("REPOSITORY_URL", raising=False)
    return mod


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_record_then_fetch_round_trip_at_the_pinned_commit(fsd, tmp_path):
    (tmp_path / "charts.json").write_text('{"c": 1}')
    (tmp_path / "weekly.json").write_text('{"w": 2}')
    assert fsd.main(["--record", "abc123", "charts.json", "weekly.json"]) == 0
    pointer = json.loads((tmp_path / "site-data.json").read_text())
    assert pointer["sha"] == "abc123" and pointer["files"] == {"charts.json": 8, "weekly.json": 8}

    (tmp_path / "charts.json").unlink()
    (tmp_path / "weekly.json").unlink()
    seen = []

    def opener(url, timeout):
        seen.append(url)
        return _Resp(b'{"ok": true}')

    assert fsd.main([], opener=opener) == 0
    assert seen == ["https://raw.githubusercontent.com/akaban01/stocks/abc123/charts.json",
                    "https://raw.githubusercontent.com/akaban01/stocks/abc123/weekly.json"]
    assert json.loads((tmp_path / "weekly.json").read_text()) == {"ok": True}


def test_local_files_are_kept_unless_forced(fsd, tmp_path):
    (tmp_path / "charts.json").write_text('{"local": 1}')
    fsd.write_pointer("abc", ["charts.json"])
    assert fsd.main([], opener=lambda url, timeout: _Resp(b'{"remote": 1}')) == 0
    assert json.loads((tmp_path / "charts.json").read_text()) == {"local": 1}
    assert fsd.main(["--force"], opener=lambda url, timeout: _Resp(b'{"remote": 1}')) == 0
    assert json.loads((tmp_path / "charts.json").read_text()) == {"remote": 1}


def test_a_missing_pointer_or_bad_download_fails_the_build(fsd, tmp_path):
    assert fsd.main([]) == 1                                  # no pointer
    (tmp_path / "charts.json").write_text("{}")
    fsd.write_pointer("abc", ["charts.json"])
    (tmp_path / "charts.json").unlink()
    assert fsd.main([], opener=lambda url, timeout: _Resp(b"<html>not json")) == 1

    def down(url, timeout):
        raise OSError("404")
    with pytest.raises(RuntimeError):
        fsd.main([], opener=down)


def test_repo_comes_from_netlifys_repository_url(fsd, monkeypatch):
    monkeypatch.setenv("REPOSITORY_URL", "https://github.com/someone/fork.git")
    assert fsd.repo_slug() == "someone/fork"
    monkeypatch.setenv("REPOSITORY_URL", "git@github.com:someone/fork")
    assert fsd.repo_slug() == "someone/fork"
