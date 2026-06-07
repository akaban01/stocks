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
| Lean (weak) | Squeeze momentum | Faint directional hint only |

Each ticker gets a **Setup Score (0–100)** — higher means more coiled — and a
1σ / 2σ expected-move band. ~68% of moves land inside ±1σ, ~95% inside ±2σ.

## Quick start (local)

```bash
pip install -r requirements.txt
python run.py                          # scans the watchlist in config.yaml
python run.py --tickers AAPL,MSFT,NVDA # ad-hoc one-off scan
```

Outputs land in `public/`:
- `report.md` — ranked, human-readable table
- `index.html` — styled dashboard (served by GitHub Pages)
- `signals.csv` / `signals.json` — full machine-readable results

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

```
score = compression(≤35) + vol_room(≤20) + squeeze(≤45)
  compression = (1 − bollinger_bandwidth_percentile) × 35
  vol_room    = (1 − historical_vol_percentile)      × 20
  squeeze     = 30 + min(days_in_squeeze, 15)/15 × 15   (only if squeeze is on)
```

All indicator math lives in [`spread_scanner/indicators.py`](spread_scanner/indicators.py)
and is computed without look-ahead.

---

⚠️ **Educational tool, not financial advice.** Expected-move bands are
statistical estimates derived from past volatility — they are not predictions,
and past volatility does not guarantee future behavior.
