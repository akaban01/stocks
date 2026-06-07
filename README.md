# Halal Short-Term Spread Scanner

A volatility-**squeeze / breakout** scanner over a **halal (Shariah-screened)**
watchlist, for **short-term (< 2 week)** moves.

It does **not** try to call direction. Instead it finds names whose volatility is
*compressed* — coiled springs that tend to release with an outsized move — and
reports how far each could travel **up _and_ down** over the next ~2 weeks. That's
the "spread both ways" idea: a straddle-style, market-neutral setup list.

Fresh data is pulled automatically every weekday by a GitHub Action, which
rewrites [`public/report.md`](public/report.md), publishes an HTML dashboard to
**GitHub Pages**, and can ping a **Slack/Discord** webhook when a setup fires.

## What it measures

| Signal | Indicator | Meaning |
|---|---|---|
| Compression | Bollinger **bandwidth** percentile | Tight range = energy stored |
| Squeeze | **TTM Squeeze** (Bollinger inside Keltner) | Classic "big move loading" trigger |
| Room to move | Historical-volatility percentile | Low vol mean-reverts → expansion |
| The spread | **Expected move** = price × σ_daily × √(horizon) | ± range up & down over the horizon |
| Trigger | **Squeeze fired** (released + break direction) | The actual entry signal, vs. the build-up |
| Calendar | **Earnings** inside the horizon | A big move into earnings is normal, not edge |
| Cheap/rich | **Implied vs historical** move (options) | Is the market under/over-pricing the move? |
| Lean (weak) | Squeeze momentum | Faint directional hint only |

Each ticker gets a **Setup Score (0–100)** — higher means more coiled — and a
1σ / 2σ expected-move band. ~68% of moves land inside ±1σ, ~95% inside ±2σ.

> **Does the score actually work?** Yes, in the way that matters. The
> [backtest](public/backtest.md) (5y, the live universe) shows coiled names break
> out of their *own* compressed ±1σ band **~44%** of the time vs **~30%** for calm
> names — a real *expansion* edge. (They don't move more in raw % — the score
> targets low-vol names — so the edge is relative, which is exactly what a
> both-ways straddle trader wants.) Re-runs each day; see the dashboard link.

## Quick start (local)

```bash
pip install -r requirements.txt
python run.py                          # scan (fetch halal universe, screen, rank)
python run.py --tickers AAPL,MSFT,NVDA # ad-hoc one-off scan
python backtest.py --years 5           # validate the score on history
python -m pytest -q                    # run the test suite
```

Outputs land in `public/`:
- `report.md` — ranked, human-readable table
- `index.html` — styled dashboard (served by GitHub Pages)
- `backtest.md` / `backtest.html` — does the score work? validation report
- `signals.csv` / `signals.json` — full machine-readable results

## Reading the signals

- **Squeeze** — `🔒 Nd` building for N days; **`🔥 ▲/▼`** just *fired* (released) —
  that release is the entry trigger, with the arrow showing the break direction.
- **Implied / Vol** — option-implied move and whether it's **cheap / fair / rich**
  vs. the historical band. *Cheap* = the market is underpricing the move (favours
  buying a straddle). Priced for the top-ranked names only.
- **Earnings** — `⚠ Nd` means earnings land inside the horizon; treat any expected
  move there as priced-in, not edge.

## Halal universe & screening

The scanner builds its universe in two automated stages, so you never hand-pick
tickers:

**1. Fetch — pre-screened ETF holdings.** Each run pulls the current top holdings
of Shariah-compliant ETFs (default **SPUS** + **HLAL**) and unions them by weight
([`spread_scanner/universe.py`](spread_scanner/universe.py)). These funds are
screened by professionals *and* a Shariah board, so it's an authoritative starting
list. If the fetch fails, it falls back to the curated `tickers:` list in the config.

**2. Verify — the financial-ratio formula.** Every fetched name is re-checked
([`spread_scanner/halal.py`](spread_scanner/halal.py)) against the recognized
quantitative methodology (AAOIFI / S&P Dow Jones Islamic style). Using market cap
as the denominator, a name passes when:

```
permissible industry  (no banks, insurance, alcohol, tobacco, gambling, weapons…)
AND  interest-bearing debt / market cap  < 33%
AND  cash & equivalents / market cap     < 33%
AND  accounts receivable / market cap    < 33%   (optional)
```

The resulting **Debt%** and **Cash%** show in every report so you can see the
verification. Set `halal_screen.financial_formula.mode: annotate` to keep names
that fail (just flagging the ratios) instead of dropping them.

> ⚠️ **Approximation, not a fatwa.** The standards use a trailing-average market
> cap and a precise definition of "interest-bearing securities"; we use spot
> values. The **non-compliant-income < 5%** purification rule isn't automated.
> Re-verify each name with a dedicated screener (Zoya, Musaffa) before trading.

## Configure

Edit [`config.yaml`](config.yaml):

```yaml
universe:
  source: etf            # 'etf' = auto-fetch holdings; 'config' = use tickers: below
  etfs: [SPUS, HLAL]     # Shariah ETFs to pull from
  max_holdings: 30
halal_screen:
  financial_formula:
    enabled: true
    mode: filter         # 'filter' drops failures; 'annotate' keeps + reports ratios
    max_debt_ratio: 0.33
    max_cash_ratio: 0.33
params:
  horizon_days: 10       # ~2 weeks of trading days — the short-term window
  history_period: 1y
tickers: [AAPL, NVDA, ...]   # fallback list if the ETF fetch fails
```

## Automated data refresh (GitHub Actions)

[`.github/workflows/update.yml`](.github/workflows/update.yml) runs on a cron
schedule, pulls the latest data with `yfinance` (no API key needed), regenerates
the report, and commits it back to the repo.

```yaml
on:
  schedule:
    - cron: "30 21 * * 1-5"   # 21:30 UTC weekdays, ~30 min after US close
  workflow_dispatch:           # or run it manually
permissions:
  contents: write              # so it can commit the report
```

To put this on GitHub:

```bash
git init
git add .
git commit -m "Initial spread scanner"
git branch -M main
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

Then in the repo: **Settings → Actions → General → Workflow permissions →
Read and write**, so the Action can commit. Trigger it once by hand from the
**Actions** tab (workflow_dispatch) to confirm it works; after that it runs daily.

> Change the cron to match your market. GitHub cron is always **UTC**, and
> scheduled runs can be delayed during peak load — treat the timing as approximate.

## GitHub Pages dashboard

The workflow regenerates `public/index.html` and deploys it to Pages on every run.
To turn it on: **Settings → Pages → Build and deployment → Source = GitHub Actions**.
Your dashboard will be live at `https://<you>.github.io/<repo>/`. The workflow
already requests the `pages`/`id-token` permissions it needs.

## Alerts (Slack / Discord)

To get pinged when a ticker's Setup Score crosses the threshold:

1. Create an **incoming webhook** in Slack or Discord and copy its URL.
2. In the repo: **Settings → Secrets and variables → Actions → New repository
   secret**, name it `ALERT_WEBHOOK_URL`, paste the URL.
3. Tune `alerts.score_threshold` in [`config.yaml`](config.yaml) (default 60).

Alerts fire only on a **new crossing** — a name at/above the threshold now that
was below it on the previous run — so you don't get spammed with the same setups.
The payload shape (Slack `text` vs Discord `content`) is auto-detected from the URL.
No webhook configured = the step quietly does nothing.

## How the score is built

A weighted blend of three normalized "coiled spring" signals, scaled to 0–100:

```
score = 100 × [ w_compression × (1 − bandwidth_percentile)
              + w_vol_room    × (1 − hv_percentile)
              + w_squeeze     × squeeze_signal ]        # squeeze_signal = 0 off, 0.6–1.0 on (rises with duration)
```

The weights are **data-calibrated**, not hand-picked. [`calibrate.py`](calibrate.py)
sets each weight ∝ how much that feature lifts the band-break (expansion) rate,
measured on a **train** split and validated **out-of-sample**:

| Feature | weight | OOS check (test split) |
|---|---|---|
| compression | 29% | calibrated weights separate high- vs low-score band-break rate by **+19 pts** |
| vol room | 48% | vs **+14 pts** for the old hand-set heuristic — |
| squeeze | 23% | the calibration holds up out of sample. |

Re-run `python calibrate.py` after changing the universe or horizon; it rewrites
[`public/calibration.md`](public/calibration.md) and prints the weights to paste into
`scanner.SCORE_WEIGHTS`. All indicator math lives in
[`spread_scanner/indicators.py`](spread_scanner/indicators.py), computed without look-ahead.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # 27 network-free tests
```

Tests cover the indicator math (incl. the rolling-percentile NaN edge case), the
Shariah screen (incl. the "Non-Alcoholic" regression), the expected-move scaling,
squeeze-fired detection, holdings parsing, and the backtest stats. CI runs them
**before** generating or deploying anything ([`.github/workflows/update.yml`](.github/workflows/update.yml)),
so a broken change never reaches the dashboard.

---

⚠️ **Educational tool, not financial advice.** Expected-move bands are
statistical estimates derived from past volatility — they are not predictions,
and past volatility does not guarantee future behavior.
