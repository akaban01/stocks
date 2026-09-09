#!/usr/bin/env python3
"""Send the alert `run.py` staged, if there is one.

Split out of the scan on purpose. `run.py` decides what would be said and
writes it to `alert.json`; this script posts it. In CI that puts the validation
step — the one that refuses to publish a scan whose option feed came back empty
— *between* the two, so a scan that is not good enough to publish is not good
enough to notify about either. It used to notify first.

    python run.py && python send_alerts.py

Always exits 0: a webhook is a notification, not a build step.
"""

from __future__ import annotations

import argparse
import sys

from spread_scanner import alerts


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Post the alert staged by run.py")
    ap.add_argument("--file", default="alert.json", help="staged alert written by run.py")
    ap.add_argument("--keep", action="store_true",
                    help="do not delete the staged file after sending")
    args = ap.parse_args(argv)
    alerts.send_staged(args.file, remove=not args.keep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
