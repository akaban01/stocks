#!/usr/bin/env python3
"""Download the large generated data files into public/data/ — Netlify's build step.

charts.json and weekly.json are rebuilt from scratch on every scheduled run and
together are ~1.3 MB. Committed to master they added that much to the history
every day. So the workflow publishes them to the `site-data` branch instead —
one commit, force-pushed each run, so it never accumulates history — and
commits only a small pointer, ``public/data/site-data.json``, naming that
commit.

This script reads the pointer and downloads the files *at that exact commit*.
Pinning the commit rather than the branch means a deploy always gets the files
from the same run as the scan committed next to the pointer, and it sidesteps
the few minutes raw.githubusercontent.com caches a branch URL for.

It fails loudly (non-zero exit) on any problem: Netlify then keeps the last
good deploy live instead of publishing a page whose Charts and Backtest tabs
cannot load.

    python scripts/fetch_site_data.py            # what netlify.toml runs
    python scripts/fetch_site_data.py --force    # overwrite local copies
    python scripts/fetch_site_data.py --record SHA charts.json weekly.json
                                                 # what the workflow runs after
                                                 # pushing the site-data branch
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "public" / "data"
POINTER = DATA / "site-data.json"
DEFAULT_REPO = "akaban01/stocks"
RAW = "https://raw.githubusercontent.com/{repo}/{ref}/{name}"


def repo_slug() -> str:
    """owner/name, from Netlify's REPOSITORY_URL when it is set."""
    url = os.environ.get("REPOSITORY_URL", "")
    if "github.com" in url:
        slug = url.split("github.com", 1)[1].strip("/:")
        if slug.endswith(".git"):
            slug = slug[:-4]
        if slug.count("/") == 1:
            return slug
    return DEFAULT_REPO


def fetch(url: str, attempts: int = 4, opener=urllib.request.urlopen) -> bytes:
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with opener(url, timeout=60) as resp:
                return resp.read()
        except Exception as exc:                      # noqa: BLE001 — retried, then re-raised
            last = exc
            if attempt < attempts - 1:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"could not download {url}: {last}")


def write_pointer(sha: str, names: list[str], path: Path | None = None) -> dict:
    """Record which site-data commit carries which files, with their sizes."""
    path = path or POINTER
    payload = {"sha": sha, "branch": "site-data",
               "files": {n: (DATA / n).stat().st_size for n in names}}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main(argv: list[str] | None = None, opener=urllib.request.urlopen) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--force", action="store_true", help="overwrite files already present")
    ap.add_argument("--record", nargs="+", metavar=("SHA", "FILE"),
                    help="write the pointer for a site-data commit and its files, then exit")
    args = ap.parse_args(argv)

    if args.record:
        if len(args.record) < 2:
            ap.error("--record needs a commit and at least one file")
        payload = write_pointer(args.record[0], args.record[1:])
        print(f"pointer -> {payload['sha'][:12]}: {', '.join(payload['files'])}")
        return 0

    try:
        pointer = json.loads(POINTER.read_text(encoding="utf-8"))
        sha, files = pointer["sha"], list(pointer["files"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {POINTER} is missing or malformed ({exc})", file=sys.stderr)
        return 1

    repo = repo_slug()
    for name in files:
        dest = DATA / name
        if dest.exists() and not args.force:
            print(f"{name}: present locally, kept (--force to replace)")
            continue
        body = fetch(RAW.format(repo=repo, ref=sha, name=name), opener=opener)
        try:
            json.loads(body)
        except ValueError:
            print(f"error: {name} at {sha[:12]} is not valid JSON", file=sys.stderr)
            return 1
        dest.write_bytes(body)
        print(f"{name}: {len(body):,} bytes from {repo}@{sha[:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
