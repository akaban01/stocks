"""Score-threshold alerts via a Slack- or Discord-compatible webhook.

Set the webhook URL in the ALERT_WEBHOOK_URL environment variable (a GitHub
Actions secret in CI). The payload shape is auto-detected from the URL:
Discord wants {"content": ...}, Slack wants {"text": ...}.

To avoid spamming the same names every run, alerts fire only on a *new*
crossing — a ticker at or above the threshold now that was below it (or absent)
on the previous run. Each alert carries the strategy engine's actual
recommendation, so the message says what to do rather than just what moved.

Sending is deliberately split from deciding. ``build_alert`` works out what
would be said and ``stage`` writes it to a file; nothing leaves the machine
until ``send`` is called. The scheduled run stages the alert, the workflow
validates the scan it came from, and only a scan good enough to publish gets
its alert posted — otherwise a run whose option feed came back empty, which CI
correctly refuses to publish, had already notified you about it.
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pandas as pd

from .net import retry


def _newly_crossed(df: pd.DataFrame, threshold: float, prev_scores: dict[str, float]) -> pd.DataFrame:
    if df.empty:
        return df
    at_or_above = df[df["score"] >= threshold]
    mask = at_or_above["ticker"].map(lambda t: prev_scores.get(t, 0.0) < threshold)
    return at_or_above[mask]


_VERB = {"BUY_PREMIUM": "🟢 BUY premium", "SELL_PREMIUM": "🔴 SELL premium",
         "NEUTRAL_INCOME": "🟡 Collect decay", "STAND_ASIDE": "⚪ Stand aside",
         "NO_DATA": "⚪ Not priced"}


def _format_message(rows: pd.DataFrame, threshold: float,
                    recs: dict[str, dict] | None = None) -> str:
    """The alert says what to *do*, not just that something is coiled."""
    horizon = int(rows["horizon_days"].iloc[0])
    recs = recs or {}
    lines = [f"📈 *Spread Scanner* — {len(rows)} ticker(s) crossed score ≥ {threshold:g}:"]
    for _, r in rows.iterrows():
        squeeze = f" · 🔒{int(r['squeeze_days'])}d" if r["squeeze_on"] else ""
        lines.append(
            f"• *{r['ticker']}*  score {r['score']:.0f}{squeeze}  "
            f"price {r['price']:,.2f}  ±{r['em_pct']:.1f}%/{horizon}d  "
            f"[{r['down_1sigma']:,.2f} ↔ {r['up_1sigma']:,.2f}]"
        )
        rec = recs.get(r["ticker"])
        if rec:
            plan = rec.get("plan") or {}
            verb = _VERB.get(rec.get("action", ""), rec.get("action", ""))
            # The blended premium score, not IV rank: they are different numbers
            # (see options.premium_score) and this line named the wrong one.
            iv = f" · premium {rec['premium_score']:.0f}/100 {rec['premium_state']}" \
                if rec.get("premium_score") is not None else ""
            lines.append(f"   ↳ {verb}: *{plan.get('name', '—')}*{iv}")
            if plan.get("legs"):
                lines.append(f"   ↳ {rec.get('detail', '')}")
        else:
            lines.append(f"   ↳ lean {r['lean']} (no IV read this run)")
    lines.append("_Not financial advice. Price it in your broker before trading._")
    return "\n".join(lines)


def _post(url: str, message: str) -> None:
    key = "content" if "discord" in url.lower() else "text"
    payload = json.dumps({key: message}).encode("utf-8")

    def _send() -> None:
        req = urllib.request.Request(url, data=payload,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()

    retry(_send, label="alert webhook")


def build_alert(df: pd.DataFrame, threshold: float,
                prev_scores: dict[str, float] | None = None,
                recommendations: dict[str, dict] | None = None) -> dict | None:
    """What this run would say, or None if nothing newly crossed. No I/O."""
    crossed = _newly_crossed(df, threshold, prev_scores or {})
    if crossed.empty:
        return None
    return {
        "threshold": float(threshold),
        "tickers": [str(t) for t in crossed["ticker"]],
        "message": _format_message(crossed, threshold, recommendations),
    }


def stage(payload: dict, path: str | Path) -> Path:
    """Write a pending alert for a later `send`. The file is deliberately not
    inside the published output directory: it is a working file, not data."""
    path = Path(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def send(payload: dict) -> int:
    """POST a prepared alert. Returns the number of tickers notified (0 if the
    webhook is unset or the post failed — an alert must never break a run)."""
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        print("Alerts: ALERT_WEBHOOK_URL not set — skipping.")
        return 0
    tickers = payload.get("tickers") or []
    try:
        _post(url, payload["message"])
    except Exception as exc:
        print(f"Alerts: failed to send ({type(exc).__name__}: {exc})")
        return 0
    print(f"Alerts: notified for {len(tickers)} ticker(s): {', '.join(tickers)}")
    return len(tickers)


def send_staged(path: str | Path, remove: bool = True) -> int:
    """Send an alert staged by `stage`, then delete the file so a later run can
    never re-send it. A missing file is the normal case: nothing crossed.

    The file is consumed only once something has actually been sent. Deleting it
    first meant a webhook that was down took the alert with it; leaving it costs
    nothing, because `run.py` clears any leftover at the start of the next run
    before staging fresh — so a stale message can never be posted either."""
    path = Path(path)
    if not path.exists():
        print("Alerts: nothing staged for this run.")
        return 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        print(f"Alerts: staged file unreadable ({type(exc).__name__}: {exc}).")
        path.unlink(missing_ok=True)          # unreadable is not worth keeping
        return 0
    sent = send(payload)
    if sent and remove:
        path.unlink(missing_ok=True)
    return sent


def maybe_alert(df: pd.DataFrame, threshold: float, prev_scores: dict[str, float] | None = None,
                recommendations: dict[str, dict] | None = None) -> int:
    """Decide and send in one step. Convenience for a local run; the scheduled
    pipeline stages instead, so nothing is posted about a scan that turns out
    not to be publishable."""
    payload = build_alert(df, threshold, prev_scores, recommendations)
    if payload is None:
        print(f"Alerts: no new crossings of score ≥ {threshold:g}.")
        return 0
    return send(payload)
