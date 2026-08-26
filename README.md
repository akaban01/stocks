# Halal Short-Term Spread Scanner

A volatility-**squeeze / breakout** scanner over a **halal (Shariah-screened)**
watchlist that tells you, for each name, **exactly what to place** — the strategy,
the strikes, the price, the risk and the exit.

Two questions decide an options trade, and this answers both:

1. **Is a move coming?** — the scanner ranks how *compressed* each name's
   volatility is. Coiled springs tend to release with an outsized move.
2. **Are options cheap or rich?** — the IV layer reads the option chain: IV rank,
   the implied-vs-realized risk premium, term structure and skew.

The second question is the one that picks the trade:

| Volatility | What it means | What to do |
|---|---|---|
| **Low IV** (rank ≲ 25) | The market is underpricing the move | **BUY premium** — long straddle / strangle / debit spread |
| **Mid IV** | No volatility edge | Stand aside, or trade the chart / term structure |
| **High IV** (rank ≳ 65) | The market is overpaying for the move | **SELL premium** — credit spread / iron condor |

The output is one card per ticker with the whole instruction on it: *"SELL premium
— IV rank 88, rich → Iron Condor. Sell the Sep 19 100 put / buy the 90 put, sell
the 135 call / buy the 145 call for $208 credit. Max loss $792, breakevens 97.92
and 137.08, ~74% probability of profit, close at 50% of the credit, earnings in 6
days so keep the wings on."*

A GitHub Action refreshes it every weekday and can ping a **Slack/Discord** webhook
when a setup fires.

## Backend writes JSON, frontend renders it

The Python side **no longer generates any HTML**. Choosing a strategy got complex
enough that markup in f-strings was the wrong place for it, so the pipeline now
splits cleanly:

```
backend  (python)        →  public/data/*.json      ← generated every run
frontend (public/)       →  index.html + assets/    ← hand-written, never regenerated
```

| File | Written by | Contains |
|---|---|---|
| `public/data/scan.json` | `run.py` | signals, the IV read, one recommendation per ticker, **and the UI copy** (action labels, premium-state rules, strategy playbook, glossary) |
| `public/data/signals.csv` | `run.py` | the same rows, flat, for spreadsheets |
| `public/data/charts.json` | `run.py` | downsampled closing-price history per ticker |
| `public/data/backtest.json` | `backtest.py` | does the score work? |
| `public/data/calibration.json` | `calibrate.py` | how the score weights were set |

Shipping the *copy* inside `scan.json` is deliberate: an explanation can never
drift from the field it explains, and any other client — a notebook, a bot, your
own UI — gets the same self-describing payload the dashboard reads.

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
| **IV rank / percentile** | ATM IV vs the trailing realized-vol range | The buy-or-sell-premium call |
| **Risk premium** | **IV − HV** and IV/HV ratio | How much you're paid over what the stock actually does |
| **Term structure** | Front-expiry IV vs ~60d IV | Backwardation = an event is priced in; favours selling the front / calendars |
| **Skew** | OTM put IV − call IV | Which side pays more to sell |
| **Liquidity** | ATM bid/ask + open interest | Whether a 4-leg spread is even fillable |
| Lean (weak) | Squeeze momentum | Faint directional hint only |

Each ticker gets a **Setup Score (0–100)** — higher means more coiled — a
1σ / 2σ expected-move band (~68% of moves land inside ±1σ, ~95% inside ±2σ), and a
**Premium Score (0–100)** blending IV rank (45%), the IV/HV risk premium (40%) and
the term structure (15%). Premium score is what decides buy vs sell.

> **On IV rank.** Free data sources publish no historical implied volatility, so
> IV rank and percentile here are ranked against each name's own trailing
> **realized**-vol distribution. Implied vol forecasts forward realized vol, so
> that distribution is the honest yardstick — but the proxy reads a little high,
> because implied carries a persistent premium over realized. The `vrp` and
> `iv_hv_ratio` fields separate that premium out.

> **Does the score actually work?** Yes, in the way that matters. The backtest
> (5y, the live universe — see the **Does it work?** tab, or
> `public/data/backtest.json`) shows coiled names break out of their *own*
> compressed ±1σ band **~44%** of the time vs **~30%** for calm names — a real
> *expansion* edge. (They don't move more in raw % — the score targets low-vol
> names — so the edge is relative, which is exactly what a both-ways straddle
> trader wants.) Re-runs each day.

## Quick start (local)

```bash
pip install -r requirements.txt

python run.py                          # scan + IV read + strategies -> public/data/
python run.py --tickers AAPL,MSFT,NVDA # ad-hoc one-off scan
python backtest.py --years 5           # validate the score on history
python calibrate.py --years 5          # re-fit the score weights
python -m pytest -q                    # the test suite (network-free)

# then view the dashboard — fetch() does not work over file://
python -m http.server 8765 --directory public   # http://localhost:8765
```

`run.py` prints the same calls to the console as it writes:

```
What to do:
  [SELL] NVDA   score  77.7  Iron Condor  credit $208
  [BUY ] TXN    score  61.3  Long Straddle  debit $754
  [WAIT] ARM    score  44.1  Stand aside
```

## Reading a recommendation

Every ticker in `scan.json` carries a `recommendation` block:

```jsonc
{
  "action": "SELL_PREMIUM",          // BUY_PREMIUM | SELL_PREMIUM | NEUTRAL_INCOME
                                     // | STAND_ASIDE | NO_DATA
  "headline": "NVDA: SELL premium — IV rank 88, rich → Iron Condor",
  "detail":   "Sell 1× 2026-09-19 100 put; Buy 1× ... — net credit $208.00 per spread",
  "confidence": 0.65,                // how much the inputs agree, not odds of winning
  "premium_state": "rich",           // cheap / fair / rich
  "premium_score": 92.0,
  "bias": "neutral", "bias_strength": "none",
  "plan": {
    "key": "iron_condor", "name": "Iron Condor",
    "vega": "short", "theta": "positive", "risk": "defined",
    "legs": [ { "action": "sell", "right": "put", "strike": 100.0,
                "expiry": "2026-09-19", "mid": 1.18, "bid": 1.17, "ask": 1.19,
                "iv": 61.0, "open_interest": 2400, "label": "Sell 1× ..." } ],
    "net": -208.0,                   // + = debit paid, − = credit received
    "max_profit": 208.0, "max_loss": 792.0,
    "breakevens": [97.92, 137.08], "profit_zone": "inside",
    "pop": 0.74,                     // model probability, zero-drift lognormal
    "credit_to_width": 0.21,
    "manage":   { "profit_target_pct": 50, "stop_loss_multiple": 2.0, "close_by_dte": 21 },
    "sizing":   { "risk_budget": 500, "contracts": 0, "over_budget": true },
    "compliance": { "tier": "more_contested", "note": "..." }
  },
  "alternatives": [ /* other ways to express the same view, fully priced */ ],
  "avoid":  [ { "name": "Long straddles", "reason": "..." } ],
  "why":    [ "IV rank 88/100 (rich premium, blended score 92).", "..." ],
  "warnings": [ "Earnings in 6 days, inside this expiry: ..." ]
}
```

**How the strategy is picked**

| | Neutral | Bullish break | Bearish break |
|---|---|---|---|
| **Cheap premium** | Long straddle (strangle if wide) | Bull call spread | Bear put spread |
| **Fair premium** | Stand aside — unless coiled, or the term structure is inverted → calendar | Bull call spread | Bear put spread |
| **Rich premium** | Iron condor | Bull put spread | Bear call spread |

Modifiers, applied on top:

- **A released squeeze** (`squeeze_fired`) is the only *strong* directional signal —
  the momentum `lean` alone never promotes a neutral structure to a one-sided one.
- **Earnings inside the expiry** forces defined risk, and warns on the side that
  matters: buying premium means paying event premium that gets crushed after the
  print; selling it means the crush is the trade and the gap is the risk.
- **Liquidity** caps the leg count. `poor` stands aside outright with the actual
  bid/ask in the reason; `fair` or `unknown` drops the 4-leg condor to a single
  2-leg credit spread.
- **Naked short strangles** are only ever offered as an *alternative*, and only
  when `strategy.allow_undefined_risk: true`, the chain is deep, and no earnings
  fall inside the expiry.

> ⚠️ **Options are contested in Islamic finance.** The halal screen covers the
> underlying *company*, not the instrument. Every plan carries a `compliance` note
> (`least_contested` for shares, `contested` for defined-risk debit structures,
> `more_contested` for short premium on margin) — that is a flag for your own
> research, not a ruling. Seek your own.

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
options:
  enabled: true          # read option chains -> IV rank, term structure, skew
  top_n: 15              # how many top-ranked names to price (2-3 calls each)
strategy:
  risk_budget_usd: 500   # max loss per position; sets the suggested contract count
  allow_undefined_risk: false   # true = offer naked short strangles as an alternative
params:
  horizon_days: 10       # ~2 weeks of trading days — the short-term window
  history_period: 1y
tickers: [AAPL, NVDA, ...]   # fallback list if the ETF fetch fails
```

With `options.enabled: false` (or for names outside `top_n`) there is no IV read,
so the recommendation is honestly `NO_DATA` rather than a guess.

## Automated data refresh (GitHub Actions)

[`.github/workflows/update.yml`](.github/workflows/update.yml) runs on a cron
schedule, pulls the latest data with `yfinance` (no API key needed), rewrites
`public/data/*.json`, validates the payload before it can reach the dashboard, and
commits it back to the repo.

```yaml
on:
  schedule:
    - cron: "30 21 * * 1-5"   # 21:30 UTC weekdays, ~30 min after US close
  workflow_dispatch:           # or run it manually
permissions:
  contents: write              # so it can commit the refreshed data
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

The workflow regenerates `public/data/*.json` and deploys the whole `public/`
folder to Pages on every run — `index.html` and `assets/` are checked in and left
alone. To turn it on: **Settings → Pages → Build and deployment → Source = GitHub
Actions**. Your dashboard will be live at `https://<you>.github.io/<repo>/`. The
workflow already requests the `pages`/`id-token` permissions it needs.

The page has five tabs: **What to do** (the strategy cards), **Scanner** (the
sortable ranked table), **Charts**, **Does it work?** (backtest + calibration) and
**Reference** (the glossary and strategy playbook, both read from `scan.json`).

### Working on the frontend

No build step, no dependencies, no external assets:

```
public/index.html          the shell and the tab markup
public/assets/app.js       data loading + rendering (vanilla JS)
public/assets/styles.css   the design system
```

Nothing generates these — edit and reload. `app.js` reads all of its trading copy
from `scan.json`'s `reference` block, so adding a strategy on the Python side
surfaces in the UI without touching the frontend.

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

The message carries the recommendation, not just the score:

```
📈 Spread Scanner — 1 ticker(s) crossed score ≥ 60:
• NVDA  score 78 · 🔒12d  price 118.45  ±6.8%/10d  [113.68 ↔ 123.23]
   ↳ 🔴 SELL premium: Iron Condor · IV rank 92/100 rich
   ↳ Sell 1× 2026-09-19 100 put; Buy 1× 2026-09-19 90 put; … — net credit $208.00 per spread
```

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

Re-run `python calibrate.py` after changing the universe or horizon. It writes
`weights.json` — the live "model" the scanner loads each run — plus
`public/data/calibration.json` for the **Does it work?** tab. All indicator math lives
in [`spread_scanner/indicators.py`](spread_scanner/indicators.py), computed without
look-ahead.

The **Premium Score** that picks buy-vs-sell is a separate, simpler blend
([`spread_scanner/options.py`](spread_scanner/options.py)):

```
premium = 100 × [ 0.45 × iv_rank
                + 0.40 × f(IV / HV)        # 0.8× → 0, ~1.15× → 0.5, 1.6×+ → 1
                + 0.15 × g(term slope) ]   # backwardation → richer, steep contango → cheaper

  < 35  cheap  → buy premium
  > 65  rich   → sell premium
```

Unlike the Setup Score these weights are reasoned, not fitted: there is no free
history of implied volatility to fit them against. They are constants at the top of
`options.py` — `CHEAP_BELOW`, `RICH_ABOVE` and the `premium_score` blend — so they
are easy to move if you disagree.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # 109 network-free tests
```

Everything is tested without touching the network. `tests/conftest.py` builds
**Black-Scholes-priced synthetic option chains**, so the strategy tests exercise
real credits, breakevens and probabilities rather than stubs.

Coverage: the indicator math (incl. the rolling-percentile NaN edge case), the
Shariah screen (incl. the "Non-Alcoholic" regression), expected-move scaling,
squeeze-fired detection, holdings parsing, backtest stats, the IV rank / premium
score / classification helpers, every branch of the strategy decision table
(including the liquidity and earnings guardrails), the spread arithmetic
(max profit, max loss, breakevens, credit-to-width, sizing) and the JSON payloads
— including that `NaN` never reaches a file the browser has to parse.

CI runs the suite **before** generating or deploying anything, then re-validates the
generated `scan.json` before the commit
([`.github/workflows/update.yml`](.github/workflows/update.yml)), so neither a broken
change nor a malformed payload reaches the dashboard.

### Layout

```
run.py                       scan -> screen -> IV read -> strategies -> JSON
backtest.py / calibrate.py   validation + weight fitting -> JSON
spread_scanner/
  universe.py  halal.py      building and screening the watchlist
  data.py      indicators.py OHLCV and the indicator math
  scanner.py                 Setup Score + expected-move bands
  options.py                 IV rank, risk premium, term structure, skew, liquidity
  strategy.py                the decision table -> one explicit plan per ticker
  report.py                  the JSON payload (and the UI copy that ships with it)
  charts.py    backtest.py   the other payloads
  alerts.py                  Slack / Discord webhook
public/                      the frontend (hand-written) + data/ (generated)
```

---

⚠️ **Educational tool, not financial advice, and not a fatwa.**

Expected-move bands and probabilities are statistical estimates derived from past
and implied volatility — they are not predictions, and past volatility does not
guarantee future behaviour. Option prices in the JSON are last-known mids and will
have moved; price every trade in your broker before placing it. The halal screen
approximates AAOIFI / S&P Islamic methodology on the underlying company and does not
automate the 5%-income purification rule — and conventional options are themselves
contested in Islamic finance. Re-verify each name (Zoya, Musaffa) and seek your own
ruling on the instrument.
