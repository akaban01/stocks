# Short-Term Spread Scanner

A volatility-**squeeze / breakout** scanner over a screened watchlist that tells
you, for each name, **exactly what to place** — the strategy, the strikes, the
price, the risk and the exit.

Two questions decide an options trade, and this answers both:

1. **Is a move coming?** — the scanner ranks how *compressed* each name's
   volatility is. Coiled springs tend to release with an outsized move.
2. **Are options cheap or rich?** — the IV layer reads the option chain: IV rank,
   the implied-vs-realized risk premium, term structure and skew.

The second question is the one that picks the trade:

| Volatility | What it means | What to do |
|---|---|---|
| **Low IV** (rank ≲ 25) | The market is underpricing the move | **BUY premium** — debit spread with a directional read; long straddle / strangle only when the backtest supports it (see below) |
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
| `public/data/scan.json` | `run.py` | signals, the IV read, one recommendation per ticker, the ≈13-month spread candidates, **and the UI copy** (action labels, premium-state rules, strategy playbook, glossary) |
| `public/data/signals.csv` | `run.py` | the same rows, flat, for spreadsheets |
| `public/data/charts.json` (on `site-data`) | `run.py` | downsampled closing-price history per ticker, plus the calendar-month record behind the Seasonality view |
| `public/data/weekly.json` (on `site-data`) | `run.py` | the same history as one row per **ISO week** (high, low, close) — the bars the Repeat test and the Backtest tab walk |
| `public/data/backtest.json` | `backtest.py` | does the score work — and does the move beat what options charged? |
| `public/data/calibration.json` | `calibrate.py` | how the score weights were set (and the fit reused between refits) |
| `public/data/iv_history.csv` | `run.py` | each priced name's ATM implied vol, one row per day — **appended, never regenerated** |
| `public/data/universe.json` | `run.py` | the last fund-holdings list fetched live, the fallback when a fetch fails |
| `public/data/screened.json` | `run.py` | the list the last scan ran on after the halal screen — what `calibrate.py` and `backtest.py` measure |
| `public/data/universe_history.csv` | `run.py` | that list for every day (`date,ticker,rank`), appended — for a future backtest without survivorship bias |
| `public/data/site-data.json` | the workflow | which `site-data` commit carries this run's `charts.json` / `weekly.json` |
| `weights.json` (repo root, gitignored) | `calibrate.py` | the fitted weights `run.py` and `backtest.py` both load |
| `alert.json` (repo root, gitignored) | `run.py` | the pending webhook message, posted later by `send_alerts.py` |

`charts.json` and `weekly.json` are ~1.3 MB rebuilt from scratch every run, so
they are gitignored on `master`. The workflow force-pushes them to the
**`site-data`** branch as a single commit (that branch never grows) and commits
only the small `site-data.json` pointer. Netlify's build
([`netlify.toml`](netlify.toml) → [`scripts/fetch_site_data.py`](scripts/fetch_site_data.py))
downloads them at that exact commit; if the download fails, the build fails and
the last good deploy stays live. **Do not delete the `site-data` branch.** In a
fresh clone, `python scripts/fetch_site_data.py` fetches the published copies,
or `python run.py` rebuilds them. Everything else in `public/data/` is committed,
and `iv_history.csv`, `universe.json` and `calibration.json` are the state the
next run builds on.

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

> **On history.** A name is scored only once it has at least half of
> `params.percentile_lookback` bars (60 by default). Below that the two
> percentile terms — two thirds of the score — come off a handful of points, and
> a number computed that way has no business being ranked against a name with a
> full year. A name with enough to score but less than the full lookback is
> published carrying `"limited history"`.

> **On IV rank.** Free data sources publish no historical implied volatility, so
> IV rank and percentile here are ranked against each name's own trailing
> **realized**-vol distribution, over `params.iv_hv_lookback` (60 trading days
> by default). Implied vol forecasts forward realized vol, so that distribution
> is the honest yardstick — but the proxy reads a little high, because implied
> carries a persistent premium over realized. The `vrp` and `iv_hv_ratio` fields
> separate that premium out. This window is deliberately longer than, and
> independent of, `vol_lookback` (the Setup Score's "room to move" term): the
> Setup Score selects names for a *low* `vol_lookback`-day HV percentile, so
> comparing IV against that same short window would score every coiled name as
> rich for mechanical reasons — the Premium Score would be measuring the
> selection, not the market.
>
> That stand-in is temporary. Every run logs each priced name's implied vol
> (`iv_history.csv`), and once a name has `options.MIN_IV_HISTORY` (120) readings,
> about six months of runs, its IV rank and percentile are taken against its own
> past implied vol instead. The switch is per name and automatic; the IV-rank
> tile says which it used ("vs past IV" or "vs realized vol"), and `scan.json`
> carries it as `options.iv_rank_basis`.

> **Does the score actually work?** For what it measures, yes; for the trades,
> not shown yet. On the held-out split (the calibration panel of the **Does it
> work?** tab), the top fifth of the score broke its *own* ±1σ band **~44%** of
> the time against **~25%** for the bottom fifth. On non-overlapping windows the
> gap is about **+16 pts, 95% CI roughly +10 to +22** (`backtest.json` →
> `verdict`). Those are one run's figures, and the universe is whatever passes the
> screen *today*, measured backwards.
>
> That band is the name's own *compressed* 20-day volatility, and the score picks
> names for having a small one. Coiled names actually move *less* in raw terms,
> so the edge is mostly quiet volatility returning to normal. An option is not
> priced off that shrunken window. Measured against the **60-day** realized band
> instead, the edge is about **+1 pt, CI roughly −5 to +6**: no evidence that
> coiled names out-move a longer-run estimate, so **buying premium on the score
> alone is not supported by this backtest.** The test that settles it — the
> forward move against the *implied* move logged each day in
> `public/data/iv_history.csv` — reports on the same tab once 60 readings have
> matured.
>
> **So the engine does not recommend straddles or strangles until that evidence
> exists** (`strategy.long_vol_requires_evidence`, on by default). Each run reads
> the last published backtest: once enough implied-vol readings have matured, a
> model straddle on coiled, cheap names has to have returned more than zero on
> average; until then, the 60-day-band interval has to clear zero. When neither
> holds, a cheap-premium name with a directional read gets a debit spread, and
> one without stands aside. The card and the page summary say why.
>
> Two things keep that test honest. Each run also prices its **five
> lowest-scoring names** (`options.control_n`) for the log only — no card, no
> trade — so coiled names can be compared with calm ones. And every live
> contract's implied vol is **solved from its own bid/ask mid** (Black–Scholes,
> `options.solve_chain_ivs`) rather than taken from Yahoo's `impliedVolatility`,
> which after the close is often worked off a stale last trade. Contracts with
> no live quote keep Yahoo's number and are marked `iv_source: "yahoo"`.
>
> Earnings don't explain the edge. The backtest also reports both intervals with
> every window that holds an earnings report removed (dates from Yahoo, about 16%
> of bars): on one run the own-band edge stayed at +14 pts and the 60-day-band
> edge at −2.
>
> **Directional trades need evidence too** (`strategy.direction_requires_evidence`).
> The engine's direction reads are the momentum lean and the way a squeeze
> released. The backtest measures each one, per side, against the base rate: how
> often the move went that way anyway (`backtest.json` → `direction`). On one run
> over the screened names, the bullish lean was right 56.2% of the time against a
> 55.6% base, the bearish lean 45.5% against 44.3%, and a squeeze release was no
> better (and on about 50 cases per side). A read is traded on only when its 95%
> interval clears zero. Until one does, every name is treated as having no
> direction: no directional debit or credit spreads, and no preferred 13-month
> spread. Most days that means most cards stand aside, which is what the evidence
> supports.

## Quick start (local)

Needs **Python 3.12+** (numpy 2.5 requires it; `.python-version` pins it for
pyenv / uv, and CI uses the same file). On 3.11 the install fails to resolve
numpy.

```bash
pip install -r requirements-dev.txt     # runtime deps + pytest + ruff
                                        # (requirements.txt alone is runtime only)

python calibrate.py                    # fit the score weights -> weights.json
                                       # (reuses the committed fit if < 30 days old;
                                       #  --force refits now)
python run.py                          # scan + IV read + strategies -> public/data/
python run.py --tickers AAPL,MSFT,NVDA # ad-hoc one-off scan
python send_alerts.py                  # post the alert run.py staged, if any
python backtest.py --years 5           # validate the score on history
python -m pytest -q                    # the test suite (network-free)
ruff check .                           # the lint CI runs

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
  "detail":   "Sell 1× 2026-09-19 100 put; Buy 1× ... — net credit $208.00 per spread (planned fill; ...)",
  "confidence": 0.65,                // how much the inputs agree, not odds of winning
  "premium_state": "rich",           // cheap / fair / rich
  "premium_score": 92.0,
  "bias": "neutral", "bias_strength": "none",
  "plan": {
    "key": "iron_condor", "name": "Iron Condor",
    "vega": "short", "theta": "positive", "risk": "defined",
    "legs": [ { "action": "sell", "right": "put", "strike": 100.0,
                "expiry": "2026-09-19", "mid": 1.18, "bid": 1.17, "ask": 1.19,
                "iv": 61.0, "open_interest": 2400, "label": "Sell 1× ...",
                "mid_source": "quote" } ],   // "last" = no live bid/ask, priced off the last trade
    "net": -208.0,                   // + = debit paid, − = credit received — at the planned
                                     //   fill: a third of the way from mid toward natural
    "net_mid": -214.0, "net_natural": -196.0,   // the best and worst fills around it
    "max_profit": 208.0, "max_loss": 792.0,     // both priced at `net`, as are size and POP
    "breakevens": [97.92, 137.08], "profit_zone": "inside",
    "pop": 0.74,                     // N(d₂) held to expiry, each breakeven at its own
                                     //   strike's IV; ignores the early exits in `manage`
    "credit_to_width": 0.21,
    "manage":   { "profit_target_pct": 50, "stop_loss_multiple": 2.0, "close_by_dte": 21 },
    "sizing":   { "risk_budget": 1000, "contracts": 1, "over_budget": false },
    "risk_form": { "tier": "short_premium", "note": "..." }   // what secures it
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
  print; selling it means the crush is the trade and the gap is the risk. The date
  is read for every name that gets priced, whichever way the universe was screened
  — it used to be attached only when the financial-ratio screen ran, so an
  ad-hoc `--tickers` scan had this guardrail silently switched off behind an
  Earnings column of dashes.
- **Liquidity** caps the leg count. `poor` stands aside outright with the actual
  bid/ask in the reason; `fair` or `unknown` drops the 4-leg condor to a single
  2-leg credit spread.
- **Naked short strangles** are only ever offered as an *alternative*, and only
  when `strategy.allow_undefined_risk: true`, the chain is deep, and no earnings
  fall inside the expiry.

Every plan also carries a `risk_form` note saying **what actually secures it** —
a debit you have already paid (`defined_debit`), margin against a short option
(`short_premium`), shares you already own (`covered`), or a long option
(`option_covered`, the diagonal and the calendar). That is a different question
from how likely the trade is to win, and it is the one that decides how the
position can hurt you.

Two structures deliberately publish *less* than the others, because the model
behind the missing numbers does not apply to them:

- **The calendar spread gets no breakeven and no probability of profit.** Its legs
  expire a month or two apart, so there is no single price at which it settles:
  what it is worth at the front expiry depends on implied volatility that day. It
  used to publish both, from a pair of strikes placed by hand at
  `strike × (1 ± 0.6σ)` — which the page then rendered exactly like a vertical's.
  The same rule is why the long-dated diagonal has never quoted one.
- **The covered call's `net` is the credit, not the cost of the stock.** The shares
  leg is marked `own` — it is stock you already hold, not a leg of this order — so
  it is excluded from the net rather than priced as a sale of a hundred contracts
  of stock. Its `max_loss` is still the real one: the shares going to zero.

## The Spreads tab — the same names at ~13 months

The strategy engine above answers *what to place this month*. The **Spreads** tab
answers a different question on the same chains: **if you wanted this name for the
next year, which spread expresses it?** That is not the same trade, and
[`spread_scanner/leaps.py`](spread_scanner/leaps.py) is a separate engine because
almost every assumption changes:

- **Time decay barely works for you.** A 13-month short option decays a rounding
  error per day. Selling premium out here is not an income trade — it is a
  directional trade you happen to get paid for.
- **Vega dominates.** Over a year the *level* of implied volatility moves the
  position far more than a week of theta does.
- **Every long-dated spread is directional.** A year-long iron condor collects
  negligible theta against a full year of gap risk, so there is no honest neutral
  structure. When the scanner has no directional read on a name, the tab
  recommends **nothing** and lists the candidates for reference instead.
- **Strikes go by moneyness, not sigma.** One sigma over 13 months is 40%+ of spot
  on a volatile name; a vertical placed there is a synthetic long. Long-dated
  strikes are percentages of spot (`VERTICAL_WIDTH`, `CREDIT_OTM`, `ITM_DEPTH`).

Five structures are priced off the real long-dated chain:

| Structure | Legs | What it is |
|---|---|---|
| **LEAPS Bull Call Spread** | ATM long call, ~15% OTM short call | Direction with a year of room, at a cost fixed on day one |
| **LEAPS Bear Put Spread** | ATM long put, ~15% OTM short put | The same shape pointed down |
| **Poor Man's Covered Call** | ~20% ITM long-dated call + short **front-month** call | Covered-call income on a fraction of the capital — the long call, not stock, secures the short one |
| **LEAPS Bull Put Spread** | ~20% OTM short put + lower wing | Paid up front to be right slowly, capital committed for the year |
| **LEAPS Bear Call Spread** | ~20% OTM short call + higher wing | The same, above the price |

The tab is a sortable table — ticker, structure, expiry, legs, net debit/credit,
max profit, max loss, reward-to-risk, that return annualised, breakeven,
probability of profit and position size — and any row opens the full legs, the management rules and that
name's caveats.

**Where the numbers are honest about themselves:**

- **"13 months" is a target, not a listing.** Exchanges list LEAPS on January
  cycles, so the nearest real expiry to 395 days can sit anywhere from ~9 to ~18
  months out. The engine takes the closest listed expiry inside that window and
  reports its **true DTE**; when it is far off the target, it says so.
- **IV rank is a front-month reading.** A 13-month contract is priced off a
  flatter part of the volatility surface, so "cheap" or "rich" is a weaker signal
  out here. Every block ships that caveat along with the long expiry's own ATM IV.
- **The diagonal gets no probability of profit.** Its legs expire thirteen months
  apart, so a single-sigma number would be quietly wrong rather than imprecise.
  Its max profit is the *conservative* case — assigned on the very first short
  call, counting none of the monthly rolls that are the point of the structure.
- **Liquidity is read on the long chain, not the front month.** LEAPS quote several
  times wider, and you pay that spread twice, a year apart.
- **Sizing runs against its own budget** (`strategy.long_risk_budget_usd`, default
  $2,500), because a LEAPS spread costs several times a monthly one.
- **Reward-to-risk is also shown annualised.** A 13-month spread returning 0.6×
  is not better than a monthly one returning 0.3×: the monthly trade recycles the
  same capital twelve times. The `RoR / yr` column makes that comparison
  visible. It is a simple annualisation that assumes the trade could be
  repeated — an assumption, not a forecast.
- **The probability model is N(d₂)**, under a lognormal whose expected *price* is
  today's. Holding the price flat rather than its logarithm puts the median
  slightly below spot, so the chance of finishing above spot is a little under
  half — and more so the longer the expiry. Dropping that −σ²/2 term, as the
  helper originally did, overstates the chance of finishing above any strike by
  ~2 points at a front month, ~8 at thirteen months and ~14 on a high-vol name
  eighteen months out. Probabilities also read volatility at the money rather
  than at each strike; on these structures that is worth under a point.

Set `options.long_dated.enabled: false` to skip the extra chain call per ticker.

## The Charts tab — when in the year, not just how much

The **Price history** view is the closing line for every screened name over the
download window, with the calendar-year boundaries marked.

The **Seasonality** view cuts the same closes a different way: every whole month
in the window becomes one return, and those returns are grouped into twelve
calendar buckets. It answers a timing question the rest of the dashboard does
not — *which months have these names actually risen and fallen in?* — with three
things on the page:

- the pooled **best and worst month** across the whole basket, with the average
  move, the hit rate and how many years stand behind each. Twelve averages always
  have a highest and a lowest, so a month is named only when it is more extreme
  than the best (or worst) month in 95% of histories with the calendar months
  shuffled (`seasonality.extreme_p`). Otherwise the tile says no month stands out
  and how often chance produced one that extreme. Each month also carries
  `excess_pct`, its average minus the name's average month;
- **average move** and **how often it worked** as two twelve-bar charts. They are
  deliberately separate: a fat average built on one spectacular year sits right
  on the coin-flip line in the second chart, which is exactly the tell you want;
- a **name-by-month grid** — every ticker's average return in each month, sorted
  by any month you click, so "who is strong in December" is one click away.

Three rules keep it honest, and they are in `spread_scanner/seasonality.py`
rather than left to the reader:

| Rule | Why |
|---|---|
| Only whole months count | A window starting mid-March contributes no March, and the month in progress is dropped. A return measured over eleven days is not a March. |
| A gap breaks the chain | If a month is missing from the history, the month after it is dropped too, rather than silently absorbing two months of move. |
| Under 3 years, no ranking | A month is still shown with its sample count, but it is never named best or worst. Three years is a floor, not a blessing. |

`charts.history_period` (default `10y`) sets how many observations each month
gets — ten years is ten Januaries. Shorten it and the record gets noisier, not
just shorter.

The **two views read different windows on purpose.** Seasonality wants the whole
download; the price cards are trimmed to `charts.display_years` (default `5`),
because a card's high/low and window change describe where a name sits *now* — a
decade-wide range is a history lesson, and twice the bars through the same point
budget smooths away the drawdowns the card exists to show. `period` and
`history_period` in `charts.json` name each window.

> ⚠️ **A tendency, not an edge.** Ten readings per month is a small sample, and
> the names in one screen move together, so the pooled row is closer to "ten
> years of evidence" than to "ten years × thirty names". Nothing here knows about
> earnings dates, index rebalances or the dividend calendar — which is where a
> lot of month-shaped behaviour actually comes from.

## The Repeat test — the same trade, every year

The Charts tab says *which months these names have risen in*. The **Repeat test**
asks the next question, which is the one you actually place a trade on:

> *If I bought this name in the same week every year and held it for eight weeks,
> how many of the last ten years **closed** at least +8% up — and how many did
> not?*

**The verdict is the exit — "closed", not "touched".** "Eight weeks, +1%" asks
whether the name is 1% above the entry at the close of week eight, not whether it
touched +1% along the way and gave it back. The two words are deliberately kept
apart, because they are the two numbers this tab exists to tell you apart. That is the number a vertical spread settles against, and it is
the one the headline, the year strip and the ranking are all counted on. Whether
the target was ever *touched* is reported next to it, in its own column and its
own tile, because it is worth knowing the profit was there to take: a year that
touched and then closed back under reads **red at the exit and blue in Touched**,
and counts as a miss. Touched has a colour of its own — not the verdict's green —
for exactly that reason: two different claims sharing one green is the confusion
the column was added to remove.

Six controls, and the answer redraws as you turn any of them:

| Control | What it means |
|---|---|
| **Name** | one of the screened names — the ranked table below runs every one of them on the same settings |
| **Direction** | an upside target (a call spread's thesis) or a downside one (a put spread's) |
| **Buy week** | the ISO week of the year you place it. The label names the calendar dates, because "week 37" is not something anyone can place on a calendar. Underneath it, a **heat strip**: every one of the 53 weeks run on your current settings, red through grey to green, with the best one named and clickable |
| **Hold** | how many weeks the trade runs — the buy week plus the N−1 after it |
| **Target** | how far the name has to move **by the close of the last week**, as a percentage. May be zero or negative — see below |
| **Years** | how many years back to repeat it |

**The best week is found by trying all of them.** The strip under the slider runs
the same test on every ISO week and paints the rate, so you can see whether the
week you picked sits on a green ridge or on the one good week in a red field —
which is most of what tells a pattern from a coincidence. A week under the
3-judged-year floor is faded and can never be crowned: ISO week 53 falls in about
one year in six and would otherwise win the title on three lucky years. And the
headline it reports is *the best of 53 tries*, which is a high bar to clear by
luck and a low one to clear by chance — the strip says so under itself, and says
it with numbers: alongside the winner it prints the runner-up and the middle of
the rankable weeks, so the crown is visibly sitting on a distribution. A week
twenty points clear of the field and a week two points clear are the same crown
and very different evidence, and there is a one-click button here to adopt it.

**The target is a distance, not a level.** $250 meant something very different in
2016, so each year's target price is computed from that year's own entry, and
every one of them is printed in the table — there is nothing to take on trust.

**It can be zero or negative.** An in-the-money vertical is already past its
breakeven at today's price, so its real question is *did it hold up* rather than
*did it travel*: set −3% and this counts the years that closed no worse than 3%
down. Going down, the sign flips with it — a downside target of −3% is a level 3%
*above* the entry the name has to close under. With the target at or behind the
entry, touching it is close to automatic, and the page says so rather than
showing a bare 100% record.

You get four numbers, a year-by-year strip, the full table of entries and exits,
and the same test run across every other name for context:

| Number | What it is |
|---|---|
| **Closed** | years where the window's **closing** price was past the target. This is the headline and the verdict — the strict reading, and a vertical spread settles against exactly this |
| **Touched it on the way** | years where the high (or the low, going down) reached the target at any point inside the window. The *generous* reading, reported beside the verdict rather than as it: it says the price was there, not that you were still in the trade when it was, so it only pays if you take profit early. Never the smaller number |
| **Best it got** | median of how far each window travelled toward the target |
| **Worst it got** | median of how far each window went the *other* way — the drawdown the years that worked still put you through |

Both tables carry a **summary row** at the foot, because the two counts the tab
exists to separate are worth reading at the bottom of the columns they came from:
*7 of 10 closed past · 10 of 10 touched* under one name's years, and the same two
pooled across every name under the ranking. Only judged years are in it — the
cell beside the total names how many were set aside, so a total that does not
match the row count never has to be worked out. The two median columns are dashed
rather than totalled: a median of medians is not a median.

> The pooled figure has 279 judged years behind it and they are **not 279
> independent ones** — these names move together, so a year that was good for the
> market was good for most of the list at once. It is one broad answer, not 279.

### Optional: price it as a debit spread

Everything above is percentages of the stock and rests on nothing but the closes.
This section is the other half of the question — **what it would have cost and
what it would have paid, in dollars** — and it is opened deliberately, because it
needs one number this repo does not hold.

> ⚠️ **The debit is yours, not the market's.** There are ten years of stock bars
> here and no option history, so nothing can look up what an eight-week call
> spread really cost in October 2018. You set it as a share of the width and it
> is held constant across every year. In life it is not: the debit climbs with
> implied volatility, and implied volatility climbs when the market is
> frightened — so the years you most want the trade are the years it cost most.
> A constant debit therefore flatters a strategy whose good years were panicky
> ones.

Everything downstream of that single assumption is exact. A vertical held to
expiry is worth `clamp(exit − long strike, 0, width)` and nothing else — no
model, no volatility, no time value, just the close already on the page.

| Control | What it means |
|---|---|
| **Long strike** | the strike you buy, as % from that year's entry. `0` is at the money |
| **Short strike** | the strike you sell, further out. It is what caps the payout, so it must sit beyond the long one — the page refuses the trade and says so otherwise |
| **Debit paid** | what you pay, as a **share of the width**. A share, not a dollar amount, because $2 was a different trade when the name was $25 |
| **Contracts** | position size. 100 shares to a contract, so a $0.40 debit is $40 of real money |

Direction comes from the chip you already set: **up is a call debit spread, down
is a put debit spread**, written with the same two positive numbers.

Two tables, both with totals: the chosen name year by year — entry, strikes, cash
out, where the stock expired, cash in, net, return — and the same structure across
every other name, sorted by net.

**The number to trust most is the breakeven.** It is the debit, as a share of
width, that would have left the run exactly square: under it this run made money,
over it it did not. It is the one figure here that does not depend on your
assumption, which makes it the one you can take to a live quote.

Not in any of it: commission, slippage, assignment, early exercise, or the fact
that a real chain has strikes at $2.50 intervals rather than wherever a percentage
of the entry happens to land.

Dividends *are* in it, and in the one direction worth naming. The closes are
dividend-adjusted, which is right for a percentage target — a target is then a
target in today's money — and wrong for a strike, because option strikes are never
adjusted. A window spanning an ex-dividend date therefore travels a little further
on this series than the real price did against the real strike: roughly the
dividends paid while the trade was on, about 0.3% over eight weeks on a 2% yielder.
Immaterial next to a typical width, and the whole of the answer in a year that
finished a cent from one, where the payout is all or nothing. It is printed under
the money tables rather than silently corrected, because correcting it would mean
a second, unadjusted download for one caveat's worth of drift. The LEAPS **Spreads**
tab is not affected — it prices real strikes off a live chain at today's price.

### What it refuses to do

The counting rules live in `spread_scanner/weekly.py` and ship inside
`weekly.json`, so the page and the docs cannot drift from what was measured:

| Rule | Why |
|---|---|
| The week in progress is dropped | A Wednesday high is not the week's high, and a trial reading one finds hits that have not happened yet. |
| A year still running is neither a close nor a miss | It has no exit yet, and the exit is the verdict. It is reported as **still open** and left out of both columns — *including when the target is already behind it*, because a name can be past the target in week three and back under it by week eight. Admitting one would move the rate in a single direction, and the newest year would quietly hold the headline up. The row still says "Touched, still open", and the count of them is printed under the headline. |
| A year the history cannot cover is **skipped**, and says so | A name that listed in 2024 has eight skipped years, not eight failures. |
| Every name sits on one gapless week axis | "Eight weeks later" is eight positions later for every name — never eight *rows* spanning a hole in the history. A window containing a gap is refused rather than closed up. |
| Under 3 judged years, no ranking | In the all-names table a short history is shown, greyed, at the bottom. Two years at 100% is not a better answer than ten at 70%. |

> ⚠️ **A stock finishing past your level is not the spread paying out.** A debit
> vertical reaches its maximum only at expiry with the name still past the short
> strike; the Spreads tab is where that gets priced. Read a closing rate here as
> the first of those two conditions, not as a backtested return. On top of that, ten
> years is ten observations, this list is whoever passes the screen *today* — the
> names that would have dragged a week's record down are the ones no longer here
> to be measured — and nothing here knows about earnings dates, which is where a
> lot of week-shaped behaviour comes from.

## The Backtest tab — one rule, every name, every week

The Repeat test asks about one week of the calendar. The **Backtest** tab asks
the other question, the one a rule is actually made of:

> *If I had bought every squeeze — or every new 8-week high, or every close under
> the 20-week average — and held it eight weeks, how often did that **close**
> +8% up? And how often did an ordinary week?*

**That second sentence is the tab.** These names rose over the decade the history
covers, so almost any rule shows a positive record and none of it is the rule's
doing. Every answer here is printed next to its **baseline** — the same names,
the same direction, hold and target, entered on *every* week there was — and the
headline is the gap between the two, in points. A rule that does not clear its
baseline was holding the market, whatever its hit rate says.

| Rule | Fires when |
|---|---|
| **Every week (no rule)** | always — this is the baseline, selectable so you can look at it directly |
| **Squeeze** | the high-to-low range of the last N weeks, as a share of price, is the narrowest it has been in the trailing year — the scanner's coiled spring, in weekly form |
| **Breakout** | the close is above every close of the previous N weeks |
| **Breakdown** | the close is below every close of the previous N weeks |
| **Above the average** | the close is above the mean of the last N weekly closes |
| **Below the average** | the close is below that mean — the dip, for anyone who buys them |

Any two of them can be combined — see *Two rules at once* below.

**Every percentage is what the position made, not what the price did.** Going
down, the sign turns over with the direction: a short whose name rose 18% reads
−18%, and one whose name fell 13% reads +13%. It sounds obvious written down,
and it was wrong here for a while — the tab printed the raw price move for both
directions, so a *winning* short read as a loss and a losing one read as a gain.
The Repeat test shares the convention, so a number means the same thing on both
tabs.

**Two rules at once.** *And also* adds a second rule, ANDed with the first: the
week has to satisfy both. "The squeeze, but only while the name is above its
20-week average" is the question people ask straight after the first one, and
it is the one that turns a signal into something resembling a strategy. The
second rule keeps **its own lookback**, because the same number means a range
window to a squeeze and a moving average to the filter beside it. Both windows
have to be answerable before either fires — a filter that cannot see far enough
back yet does not get to pass a week by default, since having no opinion is not
agreement.

Direction, hold, target and how far back to look are the same controls the Repeat
test has, and the target may be zero or negative there for the same reason. Two
more are particular to this tab:

* **History, years** — the stretch of the axis the run walks, counted in ISO
  years back from the newest week. A rule that works over ten years and not over
  the last three is worth knowing about.
* **Count every firing** — off by default. A signal that fires while a trade is
  already running is passed over, because one position is what you could have
  held, and because five overlapping windows over one good quarter are not five
  pieces of evidence. Turned on, every firing counts, which is the right reading
  for a survey and the wrong one for a plan.

**No look-ahead, and the tests say so.** A signal at week *i* is computed from
week *i* and the weeks before it and nothing after — `tests/test_backtest_js.py`
runs two histories that are identical up to week 60 and different after it
through every rule and requires the same signals from both, which is the one
property a backtest cannot be wrong about. The entry is that week's own close,
the first price available once the signal existed, so the signal week's high and
low are history you bought after rather than an excursion you sat through.

### Sweeping the two dials

One setting is a number; the grid around it is evidence. **Sweep the dials**
runs the same rule at every hold against every lookback and paints the result,
so you can see whether the pair you picked sits on a ridge of settings that all
worked or is the one green cell in a red field — which is most of what tells a
pattern from a coincidence. Click any cell to move the controls there. With a second rule set, the grid
sweeps the **rule you are testing** and holds the filter fixed at the lookback
you gave it — a grid that swept both would be asking a different question in
every row — and it says so above itself.

Each cell is measured against **its own column's baseline** — every week at that
same hold. That is the part a grid like this gets wrong quietly: a column
measured against another column's baseline would report an edge that is really
the difference between holding four weeks and holding twenty-six, dressed up as
the rule's doing.

The winner is never printed alone. Beside it go the **runner-up** — a cell well
clear of the field is a ridge, a cell a fraction clear is the same crown and much
weaker evidence — and the **middle cell**, what an *ordinary* setting on that grid
is worth. A grid whose middle is negative and whose best is +6 has one good cell,
not a rule that works. Cells under 30 finished trades are faded: shown, never
ranked.

> ⚠️ **Forty-eight cells are not forty-eight independent tries.** Neighbouring
> cells share most of their trades, and the names move together on top of that.
> And the winner is the best of forty-eight — a high bar to clear on purpose and
> a low one to clear by luck, which is exactly why the field is printed under it.

It is off by default, because it is forty-odd runs of the answer above and it
asks a second question. Turning the two dials it sweeps does **not** recompute
it: those cells were all computed already, so adopting one moves the controls and
leaves the grid where it is.

**Pick the baseline as your rule** and the tab tells you something it is
otherwise hard to know: with overlapping windows counted the edge is exactly
zero, by construction. With one-trade-at-a-time on, "every week" becomes every
*N*th week — a thinned sample of the very thing it is being compared against —
and the gap that shows up is the **noise floor** on those settings. That is the
bar a real rule has to clear, and it is printed rather than left to be guessed.

### The four views

The tab asks four different questions of one set of dials, so the dials sit at
the top and the answers are **views** under them — the same arrangement as the
Charts tab, and for the same reason: they were stacked as opt-in sections and
the page became a very long scroll. The chosen view rides in the fragment
beside the name, so `#backtest#NVDA#sweep` is a link to exactly what you were
looking at. Only the view on screen is computed; the search is 300 runs per
name and does not run while you are reading something else.

### Best per name

Search every rule at every lookback, hold and direction — 300 combinations per
name — and crown the winner for each.

On its own that would be the most
dishonest thing on this page: search hard enough against ten years of one stock
and something always wins, and the winner is usually noise wearing a rule's name.

So the crown is never the headline. **The history is cut in two.** Every
combination is searched on the older part, the winner is chosen there, and what
gets reported is what that same setting went on to do on the rest — a stretch the
search never saw. It is the same train/holdout split `calibrate.py` fits the
Setup Score's weights on, for the same reason.

#### What "best" is measured against

This is the part that is easy to get wrong, and the first version of this view
did. Everywhere else on the tab a rule is measured against **every week in the
same direction** — that is what answers "did the rule pick the weeks", and it
is right there. But a search ranks *across* directions, and that yardstick is
not comparable between them: a short is only ever measured against shorting
blindly, and on a name that rose eightfold that bar is on the floor. A short
that merely lost *less* than a blind short scored +30 points and got crowned,
and the tab reported the loss as an edge.

So the search ranks on a yardstick that is the same for both directions:
**simply holding the name for the same number of weeks**, which is the
alternative anyone actually had. Against that, a losing short cannot win. And a
combination has to have *made money* on the searched half to be crowned at all,
because beating a bad alternative is not the same as being worth taking.

Four numbers come out, and the order is the point:

| | |
|---|---|
| **Found** | the best edge the search turned up, against simply holding. This is what a tool without a holdout would show you, and it means almost nothing |
| **Out** | what that same setting did afterwards. **This is the finding** |
| **Returned** | what the pick itself made out of sample, with nothing subtracted — the money, as opposed to the comparison |
| **Best available** | the best edge the holdout actually contained — what you would have picked knowing the answer. The gap between it and *Out* is the part the search missed |

And above all of them, the number that settles it: **how many of the picks held
up** — which now takes both halves, since a pick has to have made money *and*
beaten holding the name. Either alone is cheap.

> ⚠️ **What it says about this data.** Around 60% of the picks hold up, and the
> median edge over simply holding falls from about +6 points in-sample to about
> +2 out of it — while the holdout demonstrably *contained* edges near +20 that
> the search could not identify in advance. So the search is not finding
> nothing; it is finding something worth roughly two points over buying the
> thing and waiting, which is not what a per-name "best strategy" list looks
> like it is offering. Read the table as a ranking of **hypotheses to go and
> test properly**, never as a list of trades.

A combination needs 10 finished trades on **both** halves before it can be
ranked: one that traded plenty while it was being searched and twice in the
holdout has not been tested, it has been guessed at. Click any row to put that
name and its settings into the controls above.

### Price it as an option

Everything above is percentages of the stock and rests on nothing but the
closes. This section is the other half — **what it would have cost and what it
would have paid, in dollars** — for the name currently picked in the ranking.
Pick another name there to price that one instead. It is deliberately one name
at a time: a dollar total across twenty-nine names is a portfolio nobody ran,
sized by nothing.

The arithmetic is the **same function** the Repeat test's money section uses. It
reads `settled`, `entry`, `exit` and `exit_pct` off a row and nothing else, and
the Backtest tab's rows carry exactly those — so this borrows the option maths
rather than keeping a second copy that would drift from it. A held-to-expiry
option is worth its intrinsic value and nothing else, so everything downstream
of the debit you set is exact.

> ⚠️ **The debit is yours, not the market's** — and it bites harder here than on
> the Repeat test. There is no option history in this repo, so you set the debit
> and it is held constant across every trade. But a rule like the squeeze
> *selects* for a volatility regime, so its trades are not a random sample of
> what options cost: you are pricing the quietest weeks in the history at the
> same debit as every other week. The **breakeven debit** in the totals is the
> number that needs no view on any of this — it is what would have made the run
> wash, and it is the one to take to a live quote.

### What it refuses to do, and what it still cannot see

The rules live in `public/assets/backtest.js`, next to the code that implements
them, and are printed at the foot of the tab: a trade with no exit yet is *still
open* and counted in neither column; a gap in the history inside a window is
*skipped* and said to be skipped; the verdict is the close and **touched** is
reported beside it, never as it; a name under five finished trades is shown in
the ranking but never ranked.

> ⚠️ **Survivorship, and why the baseline is the answer to it.** This list is
> whoever passes the screen *today*, so ten years of it is ten years of the
> survivors — the names a rule would have lost money on are the ones no longer
> here to be measured. The baseline carries exactly the same bias, which is why
> the *gap* between the two is far more honest than either number alone. On top
> of that: nothing here charges commission, slippage or the spread you would have
> crossed, nothing knows about earnings dates, a week is the finest grain there
> is (a stop inside the week is invisible to it), and **a stock finishing past
> your level is still not the spread paying out** — that is priced on the Spreads
> tab.

## A scan is only published if the option feed answered

The US close is 21:00 UTC in winter and 20:00 in summer, so the schedule sits
after both. It cannot rely on firing then: scheduled workflows are the lowest
priority on shared runners, and the observed fire times for this one ran 21:48,
23:27, 00:42, 02:02, 03:13 and once **05:31** — an eight-hour delay. The odd
minute in the cron helps (`:00` and `:30` are the worst queues) but nothing in a
schedule can bound that.

It matters because the option feed empties overnight. Outside US market hours it
still returns every contract, with a floor implied volatility and no bid, no ask
and no open interest — data shaped like data:

| Scan fired (UTC) | Local (ET) | ATM IV across the priced names | Median OI |
|---|---|---|---|
| 23:29, 00:43, 02:03, 03:14 | 7pm–11pm | 24–68% | 200–600 |
| 05:32 | 1:32am | 0.03–1.56% | 0 |
| 10:19, 10:32 | 6:30am | 0.01–0.78% | 0 |

So the run **refuses to publish a scan whose option feed came back empty**
(`report.option_data_health`). If fewer than half the priced names have a
plausible implied volatility, the validation step fails before the commit, and
yesterday's good scan stays up rather than being overwritten by an empty one.
Options switched off entirely is not a failure — that is no option data, which
is a different thing from bad option data.

That guard is also what makes the schedule safe to tune: if winter runs start
failing it, the cron is too close to the close and should move later. The check
turns that from silent bad data into a visible failed run.

## Universe & screening

The scanner builds its universe in two automated stages, so you never hand-pick
tickers:

**1. Fetch — pre-screened ETF holdings.** Each run pulls the current holdings
of the ETFs in `universe.etfs` (default **SPUS** + **HLAL**) and unions them by
weight ([`spread_scanner/universe.py`](spread_scanner/universe.py)). Starting from
a fund's published holdings means the list is maintained by someone else. Sources,
in order:

1. the **issuer's own daily holdings CSV** — built in for SPUS, and addable per
   fund under `universe.holdings_csv`;
2. a third-party **holdings page**, parsed from its HTML (it broke once already,
   and the CSV is there so it is no longer the only source);
3. the **last list fetched live**, saved to `public/data/universe.json` on every
   successful fetch and committed;
4. the curated `tickers:` list in the config.

Falling back to 3 or 4 reaches the page as a banner, because a scan of a fallback
list otherwise looks exactly like a scan of the funds' live holdings.

The top 30 holdings of two large-cap Shariah funds are a small, closely
correlated set, mostly large-cap tech. The backtest's intervals resample whole
dates for that reason, so 30 names moving together on one day count as one
observation rather than thirty. For the same reason trades are capped **in total**
as well as one by one (`strategy.portfolio_risk_usd`): five trades on names that
move together are close to one bet five times the size, so the day's trades are
funded in order of confidence and later ones are cut to fit.

Share classes of one company count once (`universe.SAME_COMPANY`): the issuer
file lists both GOOG and GOOGL, which took two of the thirty slots for one
business. The weights are summed and the class with the deeper option market is
kept.

Every scan saves the list it actually ran on, after the halal screen, to
`public/data/screened.json`. `calibrate.py` and `backtest.py` measure those names,
so all three agree. Before this the backtest skipped the screen and fetched the
fund holdings on its own, and tested names the scan had rejected.

Tickers are normalized to Yahoo's spelling on the way in: the holdings page writes
class shares as `BRK.B` and every Yahoo endpoint answers only to `BRK-B`, so a
dotted holding downloaded nothing and vanished from the scan behind a single
console line.

**2. Verify — the financial-ratio formula.** Every fetched name is re-checked
([`spread_scanner/halal.py`](spread_scanner/halal.py)) on its industry and its
balance sheet. Using market cap as the denominator, a name passes when:

```
permissible industry  (no banks, insurance, alcohol, tobacco, gambling, weapons…)
AND  interest-bearing debt / market cap  < 33%
AND  cash & equivalents / market cap     < 33%
AND  accounts receivable / market cap    < 33%   (optional)
```

The resulting **Debt%** and **Cash%** show in every report so you can see the
verification. For a second opinion, `python check_musaffa.py -n 30` reads Musaffa's
public verdict for the names the last scan published (`public/data/screened.json`);
it prints for you to read, and nothing in the pipeline uses it. Set `halal_screen.financial_formula.mode: annotate` to keep names
that fail instead of dropping them — each one then arrives carrying its verdict:
a **Fails screen** badge on its card, the reason underneath it, a `fails` in the
scanner table's **Screen** column, and a banner naming every flagged name at the
top of the page. A name the screen could not reach, or could not finish (Yahoo
returned no fundamentals, or no market cap, debt or cash figure), is a third
state, **Not screened**, and never renders as a pass. It is kept and flagged
by default (`financial_formula.unscreened: keep`), or dropped with `drop`: "we did not check this" and "this
passed" are different claims, and only one of them is safe to imply. (Until
recently only the two ratios reached the payload, so a
name kept for being a bank rendered exactly like one that passed. On a page whose
premise is a screened watchlist, that was the worst failure mode available.)

> ⚠️ **Approximate.** The ratios use spot values from `yfinance` rather than the
> trailing averages a formal screen would use, and "interest-bearing securities"
> is approximated by total cash. Treat the screen as a filter, not a verdict.

## Configure

Edit [`config.yaml`](config.yaml):

```yaml
universe:
  source: etf            # 'etf' = auto-fetch holdings; 'config' = use tickers: below
  etfs: [SPUS, HLAL]     # ETFs to pull holdings from
  max_holdings: 30
halal_screen:
  financial_formula:
    enabled: true
    mode: filter         # 'filter' drops failures; 'annotate' keeps them, flagged
    max_debt_ratio: 0.33
    max_cash_ratio: 0.33
options:
  enabled: true          # read option chains -> IV rank, term structure, skew
  top_n: 15              # how many top-ranked names to price (2-3 calls each)
  control_n: 5           # + this many lowest-scoring names, for the IV log only
strategy:
  risk_budget_usd: 1000  # max loss per position; sets the suggested contract count
  portfolio_risk_usd: 3000  # max loss across all of today's trades together
  allow_undefined_risk: false   # true = offer naked short strangles as an alternative
params:
  horizon_days: 10       # ~2 weeks of trading days — the short-term window
  history_period: 1y
charts:
  history_period: 10y    # what's downloaded — and what Seasonality measures
  display_years: 5       # what the price cards draw and summarize
weekly:
  enabled: true          # write weekly.json — the bars the Repeat test walks
  years:                 # how much of the download to ship; blank = all of it
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
    - cron: "23 21 * * 1-5"   # 21:23 UTC weekdays, after the US close year-round
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

The test workflow triggers on pushes to `main` *or* `master`
([`tests.yml`](.github/workflows/tests.yml)); if you rename the default branch to
something else, add it there or pushes will run no tests at all.

Then in the repo: **Settings → Actions → General → Workflow permissions →
Read and write**, so the Action can commit. Trigger it once by hand from the
**Actions** tab (workflow_dispatch) to confirm it works; after that it runs daily.

> Change the cron to match your market. GitHub cron is always **UTC**, and
> scheduled runs can be delayed during peak load — treat the timing as approximate.

## The dashboard (Netlify)

The workflow regenerates `public/data/*.json` and commits it; Netlify deploys
`public/` from the repository on every push to `master`
([`netlify.toml`](netlify.toml)), downloading the two large files from the
`site-data` branch (see the data-file table above). `index.html` and `assets/`
are checked in and left alone. The workflow used to deploy the same folder to
GitHub Pages as well; that second copy was dropped, since the site is served
from Netlify. To host it elsewhere, publish `public/` after running
`python scripts/fetch_site_data.py`.

The page has eight tabs: **What to do** (the strategy cards), **Spreads** (the
≈13-month table), **Scanner** (the sortable ranked table), **Charts** (price
history, and a **Seasonality** view — see below), **Repeat test** (the same trade
placed in the same week every year), **Backtest** (a rule, run over every name
and every week), **Does it work?** (backtest + calibration) and **Reference**
(the glossary and strategy playbook, both read from `scan.json`).

### Linking to a view

The tab on screen is in the URL, so any view can be linked to or bookmarked:

```
https://<you>.github.io/<repo>/#spreads              the Spreads tab
https://<you>.github.io/<repo>/#charts#seasonality   Charts, on the month tables
https://<you>.github.io/<repo>/#backtest#NVDA        the Backtest tab, on NVDA
https://<you>.github.io/<repo>/#repeat#AAPL          the Repeat test, on AAPL
```

The tab names are `playbook` (What to do), `spreads`, `scanner`, `charts`,
`repeat` (Repeat test), `backtest` (Backtest), `validation` (Does it work?) and
`reference`. Charts takes a second segment, `#prices` or `#seasonality`, and the
two tabs that are *about one name* — the Repeat test and the Backtest — take
that name instead (case-insensitively: `#backtest#nvda` works).

**The name is a view; the dials are not.** Hold, target, lookback and the rest
stay out of the URL and in the browser, because they are working state. Which
name you are looking at is a different thing: it is what the tables on screen
are *about*, and "look at NVDA's squeeze" is what a reader actually wants to
send someone. A name the screen does not have is ignored and the URL corrected,
so a stale link lands on the tab rather than on nothing.

That is also the whole of the **Scanner → Backtest** cross-link: every ticker in
the scanner table and on every strategy card is an ordinary anchor to
`#backtest#<name>`. No click handler, and it works from a middle-click or a
copied address like any other link.

Switching tabs rewrites the fragment in place —
`replaceState`, not a history entry, because the tab strip moves on arrow keys
and one entry per keystroke would bury the page you arrived from. A fragment
outranks the tab remembered from your last visit; one naming nothing is replaced
by whatever is on screen, so a copied URL is never a link to nowhere.

### Working on the frontend

No build step, no dependencies, no external assets:

```
public/index.html          the shell and the tab markup
public/assets/trial.js     the Repeat test's counting rules and spread maths, on their own
public/assets/backtest.js  the Backtest tab's rules: when a signal fires, what happened next, and the sweep
public/assets/render.js    pure payload -> HTML helpers (tested under node with hostile input)
public/assets/app/         the page itself, one file per part, loaded in order by index.html:
  core.js                  shared helpers, tab wiring, the URL fragment
  playbook.js scanner.js spreads.js charts.js repeat.js backtest-tab.js validation.js
                           one per tab (validation.js also holds Reference)
  boot.js                  loads the payloads and starts the page (last)
public/assets/styles.css   the design system
```

The page files share one object, `window.SpreadApp`: a name used by more than
one file is read as `App.name`, and everything else stays private to its file.
To add a tab, add a file between `core.js` and `boot.js` in `index.html` and
export what `boot.js` or another tab needs onto `App`.

`trial.js` also owns the option payoff arithmetic, which **both** tabs call —
the Repeat test prices one trade per year, the Backtest tab prices every trade a
rule took, and neither keeps its own copy of what a vertical is worth at expiry.

`trial.js` and `backtest.js` are separate because they are the two pieces of
frontend that are *rules* rather than renderings: what a hit, a miss, a
still-open trade and a skipped one mean, and — on the second — what a rule is
allowed to look at when it decides. `tests/test_trial.py` and
`tests/test_backtest_js.py` run those exact files under node, so the rules are
pinned to the code that ships rather than to a Python re-implementation that
would drift from it. Tests skip themselves where node is missing; GitHub's
runners all have it.

Nothing generates these — edit and reload. The page reads all of its trading copy
from `scan.json`'s `reference` block, so adding a strategy on the Python side
surfaces in the UI without touching the frontend. The palette lives once, as
custom properties in `styles.css`: the charts read `--up` / `--down` / `--wait`
off the stylesheet at render time rather than restating the hex values, so a
theme change moves the whole page rather than everything except the charts.

Every table header is sortable **from the keyboard** as well as the mouse
(`tabindex` + Enter/Space, `aria-sort` for the current column), the tab strip
takes arrow keys with a roving tabindex, and every focusable element paints a
visible `:focus-visible` ring. If you restyle, keep those: dropping the outline
without replacing it is a WCAG 2.4.7 failure, which is exactly how it was lost
the first time.

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

`run.py` only *stages* the message (`alert.json`); `send_alerts.py` posts it, from
a workflow step that runs after the scan has been validated. A scan whose option
feed came back empty is one CI refuses to publish — and, now, one it does not
notify you about either.

The message carries the recommendation, not just the score:

```
📈 Spread Scanner — 1 ticker(s) crossed score ≥ 60:
• NVDA  score 78 · 🔒12d  price 118.45  ±6.8%/10d  [113.68 ↔ 123.23]
   ↳ 🔴 SELL premium: Iron Condor · premium 92/100 rich
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
measured on a **train** split and validated **out-of-sample** (a gap of one
horizon is dropped between the two, so no training outcome reaches into the test
period, and the check compares the top and bottom fifth of each score so the two
weight sets are judged on equal-sized groups). It refits every
`calibration.refit_days` (30 by default) and reuses the committed fit in between,
so the score is one fixed function for a month rather than a new one each day. It
runs as the first step of the daily workflow, writing `weights.json` — the model both `run.py` and
`backtest.py` load, so the live score and the backtested score cannot be two
different functions — plus `public/data/calibration.json`, which is the
calibration half of the **Does it work?** tab.

The figures below are one run's, kept as an illustration of the shape of the
answer:

| Feature | weight | OOS check (test split) |
|---|---|---|
| compression | 29% | calibrated weights separate high- vs low-score band-break rate by **+19 pts** |
| vol room | 48% | vs **+14 pts** for the hand-set heuristic — |
| squeeze | 23% | the calibration held up out of sample. |

**The live numbers are in `public/data/calibration.json`, and they are the ones to
read** — the universe changes, so these will not reproduce exactly. When no
`weights.json` exists the scanner falls back to the constants in
[`scanner.py`](spread_scanner/scanner.py) (the same three numbers, which is why the
table matches them) and says so: `scan.json` records `weights.source` as `default`
rather than `auto-calibrated`, and the dashboard header prints whichever it used.

Re-run `python calibrate.py` after changing the universe or horizon. All indicator
math lives in [`spread_scanner/indicators.py`](spread_scanner/indicators.py),
computed without look-ahead.

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

> ⚠️ **Two of those three terms are one signal seen twice.** Because free data
> publishes no implied-vol history, `iv_rank` ranks IV against the *realized*-vol
> distribution, and `f(IV / HV)` is IV over realized. Both are the same
> implied-versus-realized comparison — one against a year of readings, one against
> today's — so **85% of this number moves together** and only the 15%
> term-structure term is independent of it. Read the score as one strong opinion
> with a small tiebreaker, not as three votes.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q          # 422 network-free tests
ruff check .                 # the lint CI runs — see ruff.toml
```

Everything is tested without touching the network. `tests/conftest.py` builds
**Black-Scholes-priced synthetic option chains**, so the strategy tests exercise
real credits, breakevens and probabilities rather than stubs.

Coverage: the indicator math (incl. the rolling-percentile NaN edge case), the
industry/ratio screen (incl. the "Non-Alcoholic" regression), expected-move scaling,
squeeze-fired detection, holdings parsing, backtest stats, the IV rank / premium
score / classification helpers, every branch of the strategy decision table
(including the liquidity and earnings guardrails), the spread arithmetic
(max profit, max loss, breakevens, credit-to-width, sizing) and the JSON payloads
— including that `NaN` never reaches a file the browser has to parse. The two
frontend files that are *rules* rather than rendering are covered the same way,
by running them under node: `trial.js` (the Repeat test's counting) and
`backtest.js` (when a signal fires, what it is allowed to look at when it
decides, and where the trade is entered).

CI lints and runs the suite **before** generating or deploying anything, then
re-validates the generated `scan.json` before the commit
([`.github/workflows/update.yml`](.github/workflows/update.yml)), so neither a broken
change nor a malformed payload reaches the dashboard. Alerts are posted from a step
**after** that validation, so a scan CI refuses to publish is not one you get
notified about either.

Dependencies carry upper bounds and [Dependabot](.github/dependabot.yml) proposes the
bumps, so an upstream major release arrives as a pull request the suite runs against
rather than inside the next scheduled scan.

Network calls retry at the level their failures actually appear at. Most raise, and
[`spread_scanner/net.py`](spread_scanner/net.py) retries those with backoff. `yf.download`
does not: a ticker that fails is caught inside yfinance, filed as an empty frame and
returned normally, so the batch looks like a success and a wrapper around the call
never sees it. The only signal a caller gets is that the ticker is missing from the
result — so `data.download` re-requests exactly the missing subset, once. A name that is
genuinely dead stays missing and costs one extra request per run; a live one no longer
disappears for the day over a single 429.

### Layout

```
run.py                       scan -> screen -> IV read -> strategies -> JSON
send_alerts.py               posts what run.py staged, after CI validates it
scripts/fetch_site_data.py   Netlify's build step: pulls charts/weekly.json from site-data
netlify.toml                 the Netlify build (publish public/, run the script above)
backtest.py / calibrate.py   validation + weight fitting -> JSON
spread_scanner/
  universe.py  halal.py      building and screening the watchlist
  data.py      indicators.py OHLCV and the indicator math
  scanner.py                 Setup Score + expected-move bands
  options.py                 IV rank, risk premium, term structure, skew, liquidity
  strategy.py                the decision table -> one explicit plan per ticker
  leaps.py                   the same chains at ~13 months -> the Spreads tab
  report.py                  the JSON payload (and the UI copy that ships with it)
  charts.py                  the price history payload
  seasonality.py             the same closes grouped by calendar month
  weekly.py                  the same closes as ISO weeks -> the Repeat test and Backtest tabs
  backtest.py                the validation payload (non-overlapping sample, date bootstrap)
  iv_history.py              the daily implied-vol log, and the backtest against it
  alerts.py                  Slack / Discord webhook (staged, then sent)
  net.py                     retry with backoff, for every network edge
public/                      the frontend (hand-written) + data/ (generated)
  assets/render.js           pure payload -> HTML helpers, tested under node with hostile input
```

## License

[MIT](LICENSE). A public repository with no licence file is "all rights
reserved" by default, which is not what a repository published to read and
learn from wants to say. Nothing in it is a warranty — least of all about the
trades it prints.

---

⚠️ **Educational tool, not financial advice.**

Expected-move bands and probabilities are statistical estimates derived from past
and implied volatility — they are not predictions, and past volatility does not
guarantee future behaviour. Option prices in the JSON are last-known mids and will
have moved; price every trade in your broker before placing it. Every position
here can lose: a debit spread can expire worthless and take the whole premium with
it, and a credit spread can lose several times what it collected. The position
sizes are computed against the risk budgets in `config.yaml`, not against your
account.
