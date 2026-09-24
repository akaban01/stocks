"""Build the scan universe from Shariah-compliant ETF holdings.

Pulls each fund's published holdings (top names by weight) and unions them into
a deduplicated ticker list. Sources, in order:

1. **The issuer's own holdings file** where one is known (`ISSUER_CSV`, or
   `universe.holdings_csv` in the config) — a plain CSV the fund publishes daily
   for its own disclosure, so it is the least likely to change shape.
2. **A third-party holdings page**, parsed from its HTML. This already broke
   once, when its JSON endpoint went away, and it will break again whenever the
   markup moves; it is the fallback, not the source.
3. **The last list that worked** (`load_last_good`), committed beside the scan,
   so one bad fetch day scans yesterday's fund rather than the config watchlist.

Always fail-safe: on any network/parse error it returns an empty list so the
caller can fall back further.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import urllib.request
from pathlib import Path

from .net import retry

# The holdings table is rendered into the page itself, so one GET is enough and
# no API key is needed. There used to be a JSON endpoint at
# /api/symbol/e/{sym}/holdings; it now returns 404 for every symbol, which is
# what silently emptied this universe and sent every scan to the config
# fallback. Parsing the page is less pleasant but it is the interface the site
# actually still serves.
_ENDPOINT = "https://stockanalysis.com/etf/{sym}/holdings/"
_HEADERS = {"User-Agent": "Mozilla/5.0 (spread-scanner)"}

# Issuer-published daily holdings files. Only files that have been checked to
# exist and parse belong here; add others through `universe.holdings_csv`.
ISSUER_CSV = {
    "SPUS": "https://www.sp-funds.com/wp-content/uploads/data/TidalFG_Holdings_SPUS.csv",
}
# Share classes of one company. A fund holding both lists both, and two slots
# of a 30-name universe then go to one business whose classes move together.
# Secondary class -> the class kept (the one with the deeper option market).
SAME_COMPANY = {
    "GOOG": "GOOGL",
    "BRK-A": "BRK-B",
    "FOX": "FOXA",
    "NWS": "NWSA",
    "UA": "UAA",
    "LEN-B": "LEN",
    "BF-A": "BF-B",
    "HEI-A": "HEI",
}

_CSV_TICKER_COLS = ("StockTicker", "Ticker", "Symbol", "ticker", "symbol")
_CSV_WEIGHT_COLS = ("Weightings", "Weight", "% of Net Assets", "weight", "Weight (%)")

_ROW = re.compile(r"<tr\b.*?</tr>", re.S | re.I)
# The symbol is the row's link to the stock's own page; the weight is the first
# percentage in the row. Anything without a stock link (header rows, the cash
# line) is not a holding we can scan.
_SYMBOL = re.compile(r'<a[^>]+href="/stocks/([A-Za-z.\-]{1,6})/?"', re.I)
_WEIGHT = re.compile(r">\s*(-?\d+(?:\.\d+)?)\s*%\s*<")


def _valid_ticker(t: str) -> bool:
    t = t.strip().upper()
    return bool(t) and len(t) <= 6 and all(c.isalpha() or c in ".-" for c in t)


def to_yahoo(ticker: str) -> str:
    """Yahoo's spelling of a ticker: class shares use a hyphen, not a dot.

    The holdings page publishes Berkshire's B shares as ``BRK.B``; every Yahoo
    endpoint this project touches wants ``BRK-B`` and returns nothing at all for
    the dotted form. A dotted holding therefore downloaded no prices, dropped
    out of the scan behind a single "No data for:" line, and was gone. Every
    ticker that enters the pipeline goes through here, and it is idempotent so
    calling it twice costs nothing."""
    return ticker.strip().upper().replace(".", "-")


def _parse_holdings(html: str) -> list[tuple[str, float]]:
    """Extract [(ticker, weight_pct)] from the holdings page's markup, with each
    ticker in Yahoo's spelling (see `to_yahoo`).

    Normalizing here rather than downstream is deliberate: this is the one place
    a dotted class-share symbol enters the program, so it is the one place that
    has to know the page's spelling differs from the price feed's."""
    out: list[tuple[str, float]] = []
    for row in _ROW.findall(html or ""):
        found = _SYMBOL.search(row)
        if not found:
            continue
        ticker = found.group(1).strip().upper()
        if not _valid_ticker(ticker):
            continue
        ticker = to_yahoo(ticker)
        weight = _WEIGHT.search(row)
        out.append((ticker, float(weight.group(1)) if weight else 0.0))
    return out


def _parse_issuer_csv(text: str) -> list[tuple[str, float]]:
    """[(ticker, weight_pct)] from an issuer holdings CSV, in Yahoo's spelling.

    Column names differ between issuers, so the ticker and weight columns are
    looked up from a short list. Cash lines and anything else without a valid
    ticker are dropped."""
    try:
        reader = csv.DictReader(io.StringIO(text or ""))
        fields = reader.fieldnames or []
    except csv.Error:
        return []
    tcol = next((c for c in _CSV_TICKER_COLS if c in fields), None)
    wcol = next((c for c in _CSV_WEIGHT_COLS if c in fields), None)
    if tcol is None:
        return []
    out: list[tuple[str, float]] = []
    for row in reader:
        ticker = (row.get(tcol) or "").strip().upper()
        if not _valid_ticker(ticker):
            continue
        try:
            weight = float(str(row.get(wcol) or "0").replace("%", "").replace(",", "").strip())
        except ValueError:
            weight = 0.0
        out.append((to_yahoo(ticker), weight))
    return out


def _get(url: str, timeout: int) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def fetch_etf_holdings(symbol: str, timeout: int = 20,
                       csv_url: str | None = None) -> list[tuple[str, float]]:
    """Return [(ticker, weight_pct)] for one ETF — best-effort, [] on failure.

    The issuer's CSV first (`csv_url`, else `ISSUER_CSV`), then the holdings
    page."""
    csv_url = csv_url or ISSUER_CSV.get(symbol.strip().upper())
    if csv_url:
        try:
            rows = _parse_issuer_csv(retry(lambda: _get(csv_url, timeout),
                                           label=f"{symbol} issuer holdings"))
        except Exception as exc:
            print(f"  ! could not fetch {symbol} issuer holdings: {type(exc).__name__}")
            rows = []
        if rows:
            return rows
        print(f"  ! {symbol} issuer holdings file gave no rows — trying the holdings page")

    url = _ENDPOINT.format(sym=symbol.strip().lower())
    try:
        html = retry(lambda: _get(url, timeout), label=f"{symbol} holdings")
    except Exception as exc:
        print(f"  ! could not fetch {symbol} holdings: {type(exc).__name__}")
        return []
    holdings = _parse_holdings(html)
    if not holdings:
        # A 200 that yields nothing means the page's shape moved again. Say so:
        # the caller only sees an empty list, and an empty list here used to be
        # indistinguishable from a network failure.
        print(f"  ! {symbol} holdings page returned no rows — the layout may have changed")
    return holdings


def fetch_halal_universe(symbols: list[str], max_holdings: int = 30,
                         csv_urls: dict[str, str] | None = None) -> list[str]:
    """Union holdings across one or more Shariah ETFs, keep the highest-weight
    names first, dedup, and cap at `max_holdings`. [] if every fetch failed."""
    weight_by_ticker: dict[str, float] = {}
    for sym in symbols:
        url = (csv_urls or {}).get(sym.strip().upper())
        for ticker, weight in fetch_etf_holdings(sym, csv_url=url):
            weight_by_ticker[ticker] = max(weight_by_ticker.get(ticker, 0.0), weight)

    weight_by_ticker = merge_share_classes(weight_by_ticker)
    ranked = sorted(weight_by_ticker, key=lambda t: weight_by_ticker[t], reverse=True)
    return ranked[:max_holdings] if max_holdings else ranked


def merge_share_classes(weights: dict[str, float]) -> dict[str, float]:
    """Fold each secondary share class into its primary, summing the weights.

    The combined weight is the company's real weight in the fund, so it ranks
    where the company belongs rather than as two half-weight entries."""
    out: dict[str, float] = {}
    for ticker, weight in weights.items():
        key = SAME_COMPANY.get(ticker, ticker)
        out[key] = out.get(key, 0.0) + weight
    return out


def _cache_key(symbols: list[str], max_holdings: int) -> dict:
    return {"etfs": sorted(s.strip().upper() for s in symbols), "max_holdings": int(max_holdings)}


def save_last_good(path: str | Path, symbols: list[str], max_holdings: int,
                   tickers: list[str], today: dt.date | None = None) -> None:
    """Record a list that was fetched live, for `load_last_good` to fall back to."""
    if not tickers:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**_cache_key(symbols, max_holdings),
                                "as_of": (today or dt.date.today()).isoformat(),
                                "tickers": list(tickers)}, indent=2), encoding="utf-8")


def load_last_good(path: str | Path, symbols: list[str],
                   max_holdings: int) -> tuple[list[str], str] | None:
    """(tickers, as_of) from the last live fetch *of the same funds and cap*, or
    None. A list saved for other funds is not this universe and is ignored."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or {k: data.get(k) for k in ("etfs", "max_holdings")} \
            != _cache_key(symbols, max_holdings):
        return None
    tickers = [t for t in data.get("tickers") or [] if isinstance(t, str) and _valid_ticker(t)]
    return (tickers, str(data.get("as_of") or "an earlier run")) if tickers else None


def resolve_universe(symbols: list[str], max_holdings: int, cache_path: str | Path | None = None,
                     csv_urls: dict[str, str] | None = None) -> tuple[list[str], str | None]:
    """The fund universe, live if possible, else the last good list.

    Returns (tickers, as_of_of_cache). The second element is None for a live
    fetch and the cached list's date when it fell back, so callers can say which
    one they scanned. ([], None) when neither is available."""
    tickers = fetch_halal_universe(symbols, max_holdings, csv_urls=csv_urls)
    if tickers:
        if cache_path:
            try:
                save_last_good(cache_path, symbols, max_holdings, tickers)
            except OSError as exc:
                print(f"  ! could not save the universe cache ({exc})")
        return tickers, None
    if cache_path:
        cached = load_last_good(cache_path, symbols, max_holdings)
        if cached:
            print(f"  live holdings unavailable — using the last good list from {cached[1]}")
            return cached
    return [], None


def from_config(uni_cfg: dict, outdir: str | Path) -> tuple[list[str], str | None]:
    """`resolve_universe` with the settings from config.yaml's `universe:` block.
    Shared by run.py, calibrate.py and backtest.py so all three scan the same
    names from the same sources."""
    etfs = uni_cfg.get("etfs") or ["SPUS"]
    cap = int(uni_cfg.get("max_holdings", 30))
    cache = uni_cfg.get("cache_file", "data/universe.json")
    csv_urls = {str(k).upper(): v for k, v in (uni_cfg.get("holdings_csv") or {}).items()}
    return resolve_universe(etfs, cap, cache_path=Path(outdir) / cache if cache else None,
                            csv_urls=csv_urls)


# --------------------------------------------------- the screened list, shared

def save_screened(path: str | Path, tickers: list[str], mode: str,
                  today: dt.date | None = None) -> None:
    """Record the list run.py actually scanned, after the halal screen.

    calibrate.py and backtest.py read it (`load_screened`) so all three measure
    the same names. Before this, the backtest skipped the screen and fetched the
    fund holdings on its own, and tested names the scan had rejected."""
    if not tickers:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"as_of": (today or dt.date.today()).isoformat(),
                                "screen_mode": mode, "tickers": list(tickers)},
                               indent=2), encoding="utf-8")


def load_screened(path: str | Path) -> tuple[list[str], str] | None:
    """(tickers, as_of) of the last screened list, or None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    tickers = [t for t in (data.get("tickers") or []) if isinstance(t, str) and _valid_ticker(t)] \
        if isinstance(data, dict) else []
    return (tickers, str(data.get("as_of") or "an earlier run")) if tickers else None


def for_validation(cfg: dict, outdir: str | Path) -> tuple[list[str], str]:
    """The names calibrate.py and backtest.py should measure, and where they came from.

    The screened list the last scan ran on, when there is one; otherwise the
    same sources run.py would start from (without its screen)."""
    uni = cfg.get("universe") or {}
    screened = load_screened(Path(outdir) / uni.get("screened_file", "data/screened.json"))
    if screened:
        return screened[0], f"the screened list scanned on {screened[1]}"
    if uni.get("source") == "etf":
        tickers = from_config(uni, outdir)[0]
        if tickers:
            return tickers, "fund holdings (unscreened — no scan has run yet)"
    return list(cfg.get("tickers") or []), "the config watchlist"


# ------------------------------------------------------- dated membership log

def append_history(path: str | Path, tickers: list[str], today: dt.date | None = None) -> int:
    """Record which names the scan ran on today, one ``date,ticker,rank`` row each.

    The backtest runs today's universe backwards, so it only ever tests the
    names that survived into today's fund holdings (survivorship bias). This log
    is what a later backtest needs to use the universe *as it was* on each date
    (`members_on`). It only helps once it is long, so it is written from the
    first run. A rerun on the same date replaces that date's rows."""
    if not tickers:
        return 0
    path = Path(path)
    day = (today or dt.date.today()).isoformat()
    rows = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if r.get("date") != day]
    rows += [{"date": day, "ticker": t, "rank": str(i)} for i, t in enumerate(tickers, 1)]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["date", "ticker", "rank"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r["date"], int(r["rank"]))))
    return len(tickers)


def load_history(path: str | Path) -> dict[str, list[str]]:
    """{ISO date: [tickers in rank order]} from the membership log."""
    out: dict[str, list[tuple[int, str]]] = {}
    try:
        with Path(path).open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                try:
                    out.setdefault(r["date"], []).append((int(r["rank"]), r["ticker"]))
                except (KeyError, ValueError):
                    continue
    except OSError:
        return {}
    return {d: [t for _, t in sorted(v)] for d, v in sorted(out.items())}


def members_on(history: dict[str, list[str]], day: str | dt.date) -> list[str] | None:
    """The universe as last recorded on or before `day`, or None before the log began."""
    day = day.isoformat() if isinstance(day, dt.date) else str(day)[:10]
    known = [d for d in history if d <= day]
    return history[max(known)] if known else None
