"""Score-threshold alerts via a Slack- or Discord-compatible webhook.

Set the webhook URL in the ALERT_WEBHOOK_URL environment variable (a GitHub
Actions secret in CI). The payload shape is auto-detected from the URL:
Discord wants {"content": ...}, Slack wants {"text": ...}.

To avoid spamming the same names every run, alerts fire only on a *new*
crossing — a ticker at or above the threshold now that was below it (or absent)
on the previous run.
"""

from __future__ import annotations

import json
import os
import urllib.request

import pandas as pd


def _newly_crossed(df: pd.DataFrame, threshold: float, prev_scores: dict[str, float]) -> pd.DataFrame:
    if df.empty:
        return df
    at_or_above = df[df["score"] >= threshold]
    mask = at_or_above["ticker"].map(lambda t: prev_scores.get(t, 0.0) < threshold)
    return at_or_above[mask]


def _format_message(rows: pd.DataFrame, threshold: float) -> str:
    horizon = int(rows["horizon_days"].iloc[0])
    lines = [f"📈 *Spread Scanner* — {len(rows)} ticker(s) crossed score ≥ {threshold:g}:"]
    for _, r in rows.iterrows():
        squeeze = f" · 🔒{int(r['squeeze_days'])}d" if r["squeeze_on"] else ""
        lines.append(
            f"• *{r['ticker']}*  score {r['score']:.0f}{squeeze}  "
            f"price {r['price']:,.2f}  ±{r['em_pct']:.1f}%/{horizon}d  "
            f"[{r['down_1sigma']:,.2f} ↔ {r['up_1sigma']:,.2f}]  {r['lean']}"
        )
    lines.append("_Not financial advice. Both-ways volatility setup._")
    return "\n".join(lines)


def _post(url: str, message: str) -> None:
    key = "content" if "discord" in url.lower() else "text"
    payload = json.dumps({key: message}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def maybe_alert(df: pd.DataFrame, threshold: float, prev_scores: dict[str, float] | None = None) -> int:
    """Send an alert if any ticker newly crossed the threshold. Returns the
    number of tickers alerted (0 if none / no webhook configured)."""
    url = os.environ.get("ALERT_WEBHOOK_URL", "").strip()
    if not url:
        print("Alerts: ALERT_WEBHOOK_URL not set — skipping.")
        return 0

    crossed = _newly_crossed(df, threshold, prev_scores or {})
    if crossed.empty:
        print(f"Alerts: no new crossings of score ≥ {threshold:g}.")
        return 0

    try:
        _post(url, _format_message(crossed, threshold))
        print(f"Alerts: notified for {len(crossed)} ticker(s): {', '.join(crossed['ticker'])}")
        return len(crossed)
    except Exception as exc:
        print(f"Alerts: failed to send ({type(exc).__name__}: {exc})")
        return 0
