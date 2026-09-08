"""Retry-with-backoff for the network edges.

Every remote call in this project talks to a free, unauthenticated endpoint —
Yahoo's chart and option APIs, a public holdings page, a webhook — on an
unattended daily schedule with nobody watching it fail. A single 429 or a reset
connection is the ordinary case, not the exceptional one, and until this module
existed it silently dropped a ticker for the day.

The policy is deliberately small: a handful of attempts, exponential backoff,
and the last exception re-raised so callers keep whatever fail-soft behaviour
they already had. Nothing here retries a *result* — an empty holdings page or a
chain with no quotes is an answer, and asking again will get the same one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

ATTEMPTS = 3
BASE_DELAY = 1.5      # seconds; doubles each attempt (1.5s, 3s)


def retry(fn: Callable[[], T], *, attempts: int = ATTEMPTS, base_delay: float = BASE_DELAY,
          label: str = "", sleep: Callable[[float], None] | None = None) -> T:
    """Call `fn`, retrying on any exception with exponential backoff.

    Re-raises the final exception, so a caller that already swallows failures
    keeps doing exactly that — just after the endpoint has had a real chance.
    `sleep` is injectable, and resolved at call time rather than bound as a
    default, so a test can replace it either way and never actually wait."""
    sleep = sleep or time.sleep
    last: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            return fn()
        except Exception as exc:                        # noqa: BLE001 — re-raised below
            last = exc
            if attempt == attempts - 1:
                break
            delay = base_delay * (2 ** attempt)
            if label:
                print(f"  ! {label} failed ({type(exc).__name__}: {exc}); "
                      f"retrying in {delay:.0f}s")
            sleep(delay)
    raise last                                          # type: ignore[misc]
