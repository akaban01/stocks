"""Downloads, with a CSV cache, for SPY and the FRED series the rules read.

Every loader returns a float Series indexed by date and caches what it fetched to
``DATA_DIR``; a rerun reads the cache unless ``refresh=True``. A source that fails
raises ``DataError`` with the reason, and ``run.py`` stops on it — the brief is to
stop rather than quietly substitute a different series.

The one place a fallback is allowed is the credit spread, and the order is fixed
(see ``load_credit``):

1. ``DATA_DIR/hy_oas_full.csv`` supplied by hand (columns ``date,value``, in %).
2. An ALFRED vintage of BAMLH0A0HYM2 dated before April 2026, when FRED still
   published the full history. With ``FRED_API_KEY`` set this goes through the
   ALFRED API (what fredapi's ``get_series_as_of_date`` calls); without one, the
   keyless ``alfredgraph.csv`` endpoint. Either is rejected if it is short.
3. FRED BAA10Y (Moody's Baa minus the 10-year Treasury, daily from 1986).

As of 2026-10 step 2 does not work: FRED dropped the pre-2023 ICE data from the
old vintages too, so every vintage checked (2024-01 through 2026-03) starts on
2023-10-02. The code still tries it, and says so, rather than hard-coding that.
"""

from __future__ import annotations

import io
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from spread_scanner.net import retry

DATA_DIR = Path(__file__).resolve().parent / "data"

FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
ALFRED_CSV = "https://alfred.stlouisfed.org/graph/alfredgraph.csv?id={sid}&vintage_date={vintage}"
ALFRED_API = ("https://api.stlouisfed.org/fred/series/observations?series_id={sid}"
              "&realtime_start={vintage}&realtime_end={vintage}&api_key={key}&file_type=json")

HY_SERIES = "BAMLH0A0HYM2"
HY_VINTAGE = "2026-03-31"          # the last month-end before the April 2026 truncation
# ICE's HY OAS history starts on 1996-12-31. A vintage that does not reach back
# to the late 1990s is the truncated series under another name.
HY_MIN_START = pd.Timestamp("1998-12-31")
TIMEOUT = 60


class DataError(RuntimeError):
    """A required source failed beyond the fallbacks the brief allows."""


@dataclass
class CreditData:
    series: pd.Series      # spread in percent, indexed by observation date
    kind: str              # "hy" (HY OAS) or "baa" (BAA10Y)
    source: str            # human-readable: where it came from
    log: list[str]         # every source tried, in order, and why it was or wasn't used


def _get(url: str) -> bytes:
    def once() -> bytes:
        with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
            return resp.read()
    return retry(once, label=url.split("?")[0])


def parse_fred_csv(text: str, sid: str | None = None) -> pd.Series:
    """FRED/ALFRED graph CSV -> float Series. Missing values are "." on some
    endpoints and blank on others; both become NaN and are dropped."""
    df = pd.read_csv(io.StringIO(text), na_values=[".", ""], keep_default_na=True)
    if df.shape[1] < 2 or not len(df):
        raise DataError(f"FRED CSV for {sid or '?'} has no data columns")
    first = str(df.columns[0]).lower()
    if first not in ("observation_date", "date"):
        raise DataError(f"FRED CSV for {sid or '?'} has an unexpected header: {list(df.columns)}")
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0]), name=sid or str(df.columns[1]))
    s.index.name = "date"
    return s.dropna().sort_index()


def _read_cache(path: Path) -> pd.Series:
    df = pd.read_csv(path, parse_dates=["date"])
    s = pd.Series(df["value"].astype(float).values, index=df["date"], name=path.stem)
    s.index.name = "date"
    return s.dropna().sort_index()


def _write_cache(s: pd.Series, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": s.index.strftime("%Y-%m-%d"), "value": s.values}).to_csv(path, index=False)


def load_fred(sid: str, refresh: bool = False, data_dir: Path = DATA_DIR) -> pd.Series:
    path = data_dir / f"fred_{sid}.csv"
    if path.exists() and not refresh:
        return _read_cache(path)
    try:
        s = parse_fred_csv(_get(FRED_CSV.format(sid=sid)).decode("utf-8"), sid)
    except DataError:
        raise
    except Exception as exc:                                # noqa: BLE001
        raise DataError(f"FRED download of {sid} failed: {type(exc).__name__}: {exc}") from exc
    if s.empty:
        raise DataError(f"FRED returned no observations for {sid}")
    _write_cache(s, path)
    return s


def close_from_yf(raw: pd.DataFrame, ticker: str = "SPY") -> pd.Series:
    """The adjusted close out of a yfinance frame, whichever shape it came in.

    yfinance >= 0.2.48 returns MultiIndex columns (field, ticker) even for one
    ticker, and with ``auto_adjust=True`` the adjusted price is ``Close`` (there
    is no "Adj Close"). Older versions return flat columns. An "Adj Close"
    column, where one exists, is preferred — it only exists when the frame was
    not auto-adjusted, and then it is the adjusted one."""
    if raw is None or raw.empty:
        raise DataError(f"yfinance returned no rows for {ticker}")
    cols = raw.columns
    if isinstance(cols, pd.MultiIndex):
        fields = set(cols.get_level_values(0))
        field = "Adj Close" if "Adj Close" in fields else "Close"
        sub = raw[field]
        if isinstance(sub, pd.DataFrame):
            if ticker in sub.columns:
                sub = sub[ticker]
            elif sub.shape[1] == 1:
                sub = sub.iloc[:, 0]
            else:
                raise DataError(f"yfinance frame has no {ticker} column: {list(sub.columns)}")
    else:
        field = "Adj Close" if "Adj Close" in cols else "Close"
        if field not in cols:
            raise DataError(f"yfinance frame has no Close column: {list(cols)}")
        sub = raw[field]
    s = pd.Series(pd.to_numeric(sub, errors="coerce").values,
                  index=pd.to_datetime(raw.index).tz_localize(None), name=ticker)
    s.index.name = "date"
    return s.dropna().sort_index()


def load_spy(refresh: bool = False, data_dir: Path = DATA_DIR) -> pd.Series:
    path = data_dir / "spy.csv"
    if path.exists() and not refresh:
        return _read_cache(path)
    import yfinance as yf

    def once() -> pd.DataFrame:
        df = yf.download("SPY", period="max", interval="1d", auto_adjust=True,
                         progress=False, threads=False)
        if df is None or df.empty:
            raise RuntimeError("empty frame")
        return df
    try:
        raw = retry(once, label="yfinance SPY")
    except Exception as exc:                                # noqa: BLE001
        raise DataError(f"SPY download failed: {type(exc).__name__}: {exc}") from exc
    s = close_from_yf(raw, "SPY")
    if len(s) < 5000:
        raise DataError(f"SPY history is only {len(s)} rows — expected ~8,000 back to 1993")
    _write_cache(s, path)
    return s


def _alfred_hy(log: list[str], refresh: bool, data_dir: Path) -> pd.Series | None:
    path = data_dir / "hy_oas_alfred.csv"
    if path.exists() and not refresh:
        s = _read_cache(path)
        log.append(f"2. ALFRED vintage {HY_VINTAGE}: using cached copy {path.name}")
        return s
    key = os.environ.get("FRED_API_KEY", "").strip()
    try:
        if key:
            url = ALFRED_API.format(sid=HY_SERIES, vintage=HY_VINTAGE, key=urllib.parse.quote(key))
            payload = json.loads(_get(url).decode("utf-8"))
            obs = payload.get("observations") or []
            s = pd.Series(pd.to_numeric([o.get("value") for o in obs], errors="coerce"),
                          index=pd.to_datetime([o.get("date") for o in obs]), name="hy").dropna()
            how = "ALFRED API (FRED_API_KEY)"
        else:
            url = ALFRED_CSV.format(sid=HY_SERIES, vintage=HY_VINTAGE)
            s = parse_fred_csv(_get(url).decode("utf-8"), HY_SERIES)
            how = "keyless alfredgraph.csv (FRED_API_KEY not set)"
    except Exception as exc:                                # noqa: BLE001
        log.append(f"2. ALFRED vintage {HY_VINTAGE}: failed ({type(exc).__name__}: {exc})")
        return None
    if s.empty or s.index.min() > HY_MIN_START:
        start = s.index.min().date() if len(s) else "n/a"
        log.append(f"2. ALFRED vintage {HY_VINTAGE} via {how}: rejected — short history "
                   f"({len(s)} rows, starts {start}; need a start on or before {HY_MIN_START.date()})")
        return None
    log.append(f"2. ALFRED vintage {HY_VINTAGE} via {how}: OK, {len(s)} rows from {s.index.min().date()}")
    s.index.name = "date"
    _write_cache(s, path)
    return s


def load_credit(refresh: bool = False, data_dir: Path = DATA_DIR) -> CreditData:
    log: list[str] = []
    manual = data_dir / "hy_oas_full.csv"
    if manual.exists():
        s = _read_cache(manual)
        if s.empty:
            raise DataError(f"{manual} exists but has no rows (expected columns date,value)")
        if s.index.min() > HY_MIN_START:
            raise DataError(f"{manual.name} starts {s.index.min().date()}; "
                            f"need history from {HY_MIN_START.date()} or earlier")
        if not 0.5 < s.median() < 25:
            raise DataError(f"{manual.name} median is {s.median():.1f}; expected percent "
                            "(e.g. 4.5), not basis points (450)")
        log.append(f"1. {manual.name}: found, {len(s)} rows from {s.index.min().date()}")
        return CreditData(s, "hy", f"ICE BofA HY OAS from local file {manual.name}", log)
    log.append(f"1. {manual.name}: not present")

    s = _alfred_hy(log, refresh, data_dir)
    if s is not None:
        return CreditData(s, "hy", f"ICE BofA HY OAS ({HY_SERIES}), ALFRED vintage {HY_VINTAGE}", log)

    s = load_fred("BAA10Y", refresh, data_dir)
    log.append(f"3. FRED BAA10Y: OK, {len(s)} rows from {s.index.min().date()}")
    return CreditData(s, "baa", "FRED BAA10Y (Moody's Baa corporate yield minus 10-year Treasury)", log)


@dataclass
class Inputs:
    spy: pd.Series
    tbill: pd.Series      # DTB3, % annualized (discount basis)
    vix: pd.Series
    vix3m: pd.Series
    t10y2y: pd.Series
    credit: CreditData


def load_all(refresh: bool = False, data_dir: Path = DATA_DIR) -> Inputs:
    return Inputs(
        spy=load_spy(refresh, data_dir),
        tbill=load_fred("DTB3", refresh, data_dir),
        vix=load_fred("VIXCLS", refresh, data_dir),
        vix3m=load_fred("VXVCLS", refresh, data_dir),
        t10y2y=load_fred("T10Y2Y", refresh, data_dir),
        credit=load_credit(refresh, data_dir),
    )
