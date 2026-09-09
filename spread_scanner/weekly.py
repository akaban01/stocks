"""Weekly bars — the history the Repeat test walks.

The dashboard's Repeat test asks one question over and over: *if I bought this
name in the same week every year and gave the trade N weeks, how many of those
years **ended** past my target?* Answering it needs a bar series fine enough to
place an entry on a chosen week and coarse enough to ship — so this module
reduces the same daily download the charts use to one **ISO week**
(Monday–Sunday) per row, keeping the high, the low and the close.

Three fields, because the verdict and the colour beside it need different ones:

* **close** — where the name actually *finished* the window, which is the
  verdict. "Eight weeks, +1%" asks whether the close of week eight is 1% above
  the entry; a spike in week three that gave itself back is not an answer to
  it. It is also what a vertical spread settles against.
* **high / low** — whether the target was ever *touched* while the trade was on.
  Reported beside the verdict, never as it: it says the price was there, not
  that you were still in the trade when it was. That is the number that matters
  if you take profit early, and it is never the smaller of the two.

Two honesty rules, the same ones :mod:`spread_scanner.seasonality` applies to
months:

* **The week in progress is dropped.** A Wednesday high is not the week's high,
  and a trial that read one would find hits that had not happened yet. As there,
  ``BDay`` knows weekends but not holidays, so a week whose last session is
  followed by a weekday holiday is dropped rather than half-counted.
* **Every ticker sits on one shared, gapless week axis.** ``weeks`` is a
  contiguous run of ISO weeks, so "eight weeks later" is eight positions later
  for every name — never eight *rows* that quietly span a missing month. A name
  with no bars in a week gets ``null`` there, and the frontend refuses to run a
  window containing one rather than closing the gap for it.

The first week of a series is kept even when it is partial: unlike a monthly
*return*, a week's high, low and close are real prices whatever number of
sessions produced them, and an entry never reads its own week's close anyway
(see ``reference.entry`` in the payload).

This is a second view of the download ``charts.json`` already carries, not a
second download — same frames, different reduction, written to its own file so
the Charts tab does not pay for it.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd

from .report import SCHEMA_VERSION, write_json

# Below this a name has no history to repeat anything over — six months inside a
# ten-year window means it listed the other month. It still draws a price card;
# it just does not reach a tool whose shortest question spans weeks.
MIN_WEEKS = 26

# How many judged years a name needs before its finish rate is ranked against
# others. The same floor, for the same reason, as `seasonality.MIN_YEARS`: two
# years at 100% is not a better answer than ten at 70%, and a table sorted by
# rate will put it on top unless something stops it. Names under the floor are
# still shown with their record — they are just never ranked by it.
MIN_YEARS = 3

# What the numbers mean, shipped with them. The trial itself runs in the
# browser — it has to, since every control re-runs it — so the *definitions*
# ship from here rather than being restated in JavaScript, the same way
# scan.json carries the copy for the fields it explains.
REFERENCE = {
    "entry": "You buy as the chosen week opens, at the last close before it. That price is the "
             "baseline for the whole trial, so the buy week's own move counts toward the target.",
    "window": "A hold of N weeks means the buy week plus the N−1 after it. The trade is measured "
              "from the entry price to the close of the last week in that window.",
    "result": "A year counts as finished if the trade *ends* past the target: the close of the "
              "last week in the window is at or beyond the entry price moved by your percentage. "
              "Eight weeks at +1% asks whether the name is 1% up at the end of week eight, not "
              "whether it was ever 1% up along the way. A vertical spread settles against that "
              "close, so this is the number the structure actually pays on.",
    "touch": "Touched is the looser test, reported beside the verdict rather than deciding it: the "
             "weekly high for an upside target, the weekly low for a downside one, at any point "
             "inside the window. Touched, not held — it says the price was there, not that you "
             "were still in the trade when it was — so it is the number that matters if you take "
             "profit early, and it is never the smaller of the two.",
    "incomplete": "A year whose window runs past the last complete week has no exit yet, so it is "
                  "reported as still open and counted in neither column — including when the "
                  "target is already behind it, because a name can be past the target in week "
                  "three and back under it by week eight. Admitting one would move the rate in a "
                  "single direction, and the newest year would quietly hold the headline up. A "
                  "year with no bars for the entry week, or a gap inside the window, is skipped "
                  "and said to be skipped.",
    "ranking": "A name needs at least three judged years before its finish rate is ranked against "
               "the others. Two years at 100% is not a better answer than ten at 70%, and a "
               "shorter history is not a stronger one.",
    "prices": "Split- and dividend-adjusted closes, so a target is a target in today's money. "
              "Nothing here prices an option: the target is a level the *stock* has to finish "
              "past, and finishing past it is necessary for a spread to pay, not sufficient.",
}


# ---- one ticker ------------------------------------------------------------

def _mondays(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """The Monday that opens each timestamp's ISO week."""
    return index.normalize() - pd.to_timedelta(index.weekday, unit="D")


def _monday(when: pd.Timestamp) -> pd.Timestamp:
    return _mondays(pd.DatetimeIndex([when]))[0]


def weekly_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLC reduced to one row per whole ISO week, indexed by its Monday.

    Columns are ``high``/``low``/``close``. A frame carrying no High/Low falls
    back to the close for both, which keeps a close-only source usable: the
    touch test then never exceeds the weekly close, which is the conservative
    direction to be wrong in.
    """
    empty = pd.DataFrame(columns=["high", "low", "close"], dtype="float64")
    if df is None or "Close" not in getattr(df, "columns", []):
        return empty

    close = pd.to_numeric(df["Close"], errors="coerce").dropna()
    if len(close) < 2:
        return empty
    close.index = pd.to_datetime(close.index)
    if getattr(close.index, "tz", None) is not None:
        # A daily bar has no meaningful time of day, and the zone only gets in
        # the way of the weekday arithmetic below.
        close.index = close.index.tz_localize(None)

    def _side(name: str) -> pd.Series:
        """High or Low, aligned to the close series; the close where it is missing."""
        if name not in df.columns:
            return close
        s = pd.to_numeric(df[name], errors="coerce")
        s.index = pd.to_datetime(s.index)
        if getattr(s.index, "tz", None) is not None:
            s.index = s.index.tz_localize(None)
        return s.reindex(close.index).fillna(close)

    high, low = _side("High"), _side("Low")
    key = _mondays(close.index)
    out = pd.DataFrame({
        "high": high.groupby(key).max(),
        "low": low.groupby(key).min(),
        "close": close.groupby(key).last(),
    })
    out.index.name = None

    # The week in progress is not a week. If another session is still due before
    # the week turns, its high and low are partial — and a trial reading them
    # would score as missed a target that Friday goes on to reach.
    last = close.index[-1]
    if _monday(last + pd.offsets.BDay(1)) == _monday(last):
        out = out.iloc[:-1]
    return out if not out.empty else empty


def iso_week(monday: pd.Timestamp) -> str:
    """``2025-W37`` — the ISO name of the week a Monday opens."""
    cal = monday.isocalendar()
    return f"{cal[0]}-W{cal[1]:02d}"


# ---- the payload -----------------------------------------------------------

def build_weekly(data: dict[str, pd.DataFrame], period_label: str = "",
                 years: int | None = None) -> dict:
    """One shared week axis, plus every ticker's bars aligned onto it."""
    bars = {}
    for ticker in sorted(data):
        weekly = weekly_bars(data[ticker])
        if len(weekly) >= MIN_WEEKS:
            bars[ticker] = weekly

    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    base = {"schema_version": SCHEMA_VERSION, "generated_at": generated,
            "period": period_label, "min_weeks": MIN_WEEKS, "min_years": MIN_YEARS,
            "reference": REFERENCE}
    if not bars:
        return {**base, "weeks": [], "starts": [], "count": 0, "series": []}

    start = min(b.index[0] for b in bars.values())
    end = max(b.index[-1] for b in bars.values())
    if years:
        # Trim from the newest end, and land on a Monday so the axis starts on a
        # real week rather than on a week and a bit.
        start = max(start, _monday(end - pd.DateOffset(years=years)))
    axis = pd.date_range(start, end, freq="W-MON")

    def column(series: pd.Series) -> list:
        return [None if pd.isna(v) else round(float(v), 2) for v in series]

    series = []
    for ticker, weekly in bars.items():
        onto = weekly.reindex(axis)
        present = onto["close"].notna().to_numpy()
        # The floor has to be measured on what is *shipped*, not on what was
        # downloaded. Applied only before the trim, a `years` window let a name
        # whose history ended years ago through with ten weeks in it, on a
        # payload still declaring `min_weeks: 26` — which the page quotes back
        # to the reader. With no trim this is the same test it was.
        if int(present.sum()) < MIN_WEEKS:
            continue
        first = int(present.argmax())
        last = len(present) - 1 - int(present[::-1].argmax())
        series.append({
            "ticker": ticker,
            "first": axis[first].strftime("%Y-%m-%d"),
            "last": axis[last].strftime("%Y-%m-%d"),
            "weeks": int(present.sum()),
            "close": column(onto["close"]),
            "high": column(onto["high"]),
            "low": column(onto["low"]),
        })

    return {
        **base,
        # Parallel arrays, one entry per week: the ISO name and the Monday that
        # opens it. Both, because deriving either from the other is eight lines
        # of off-by-one in every language that has to do it.
        "weeks": [iso_week(m) for m in axis],
        "starts": [m.strftime("%Y-%m-%d") for m in axis],
        "count": len(series),
        "series": series,
    }


def write_weekly(data: dict[str, pd.DataFrame], outdir: str | Path,
                 period_label: str = "", years: int | None = None) -> Path:
    """Write ``<outdir>/data/weekly.json``.

    Compact, unlike every other payload here: this one is almost entirely
    numbers — a decade of weeks for thirty names — and pretty-printing puts each
    of them on its own indented line, four times the bytes for nothing a reader
    gains.
    """
    return write_json(Path(outdir) / "data" / "weekly.json",
                      build_weekly(data, period_label, years), compact=True)
