# risk-sim runbook (docker compose on the R430)

risk-sim is a stateless FastAPI service: POST a portfolio + correlation
matrix to `/api/v1/simulate`, get VaR / CVaR / margin-call probabilities
back. No database. The API container makes no outbound network calls; a
sidecar compose service (`correlation-job`, same image) fetches Yahoo
Finance history once a day and writes correlation snapshots to a shared
named volume, which the API serves read-only under `/api/v1/correlations`.
It runs as its own compose project, completely separate from the EBMS stack
(own project name `risk-sim`, own bridge network, nothing shared with EBMS).
Host port **8080** (EBMS holds 80/443/8000/8001/5432/6379).

All commands below run from this directory (`risk-sim/`).

## Build + start

```bash
docker compose up -d --build
docker compose ps            # wait for state "healthy"
curl -s http://localhost:8080/healthz
curl -s http://localhost:8080/readyz
```

## Dashboard

The API serves a self-contained correlation dashboard (single HTML page,
inline CSS/JS, no CDNs — works offline) at:

- <http://localhost:8080/> (or `http://<r430-host>:8080/`)
- <http://localhost:8080/dashboard> (same page)

It reads the same `/api/v1/correlations*` endpoints: pick a snapshot date
(defaults to the latest), get the correlation heatmap (blue = positive,
red = negative, gray = uncorrelated), per-ticker stat tiles (last price,
annualized vol %, drift %), and the lookback / skipped-ticker metadata.
Below the heatmap, a "Margin account (CFD, 20% margin)" section shows the
margin-at-open/midday/close stat tiles (the peak one wears a **Peak** badge)
and a per-ticker table with prices, quantity, and the three margin figures
plus a totals row. Under that, a "Margin Required — Year to Date" line chart
plots the daily open/midday/close margin totals from the snapshot's
`margin_timeseries` section (see below; historical midday is a proxy).
Below the margin chart, a "CFD Account P&L — Year to Date" card plots the
cumulative daily P&L of the configured positions from the snapshot's
`pnl_timeseries` section (blue above / red below an emphasized zero
baseline), with Current P&L / Best day / Worst day stat tiles.
Snapshots written before these features existed have no
`margin_account` / `margin_timeseries` / `pnl_timeseries` section — the
dashboard simply hides those blocks for such dates. Before the first correlation-job run it shows an empty state
with the manual-run command instead of data — nothing to fix.

## Run the bundled example

```bash
curl -s -X POST http://localhost:8080/api/v1/simulate \
  -H 'Content-Type: application/json' \
  -d @examples/request_example.json | python3 -m json.tool
```

The JSON response is the output — there are no files written on the server.
Redirect it if you want to keep a copy, e.g. `> results/$(date +%F).json`.

## Real market data (optional, runs OUTSIDE the container)

`data/fetch_market_data.py` pulls Yahoo Finance history and builds a request
payload with your own ticker/lookback choices. Run it on your laptop
(`pip install -r requirements-data.txt`), edit the `position_units` /
`margin_pct` placeholders in the generated `live_request.json`, then POST
that file as above. For the standing daily snapshot of the configured ticker
list, see "Daily correlation snapshots" below — its `latest.json` already
contains a ready-to-POST payload.

## Daily correlation snapshots (`correlation-job`)

A second compose service, `correlation-job`, reuses the same image and runs
`python -m app.daily_correlation --loop`: it computes a snapshot immediately
on start, then sleeps until the next **07:00 UTC** and repeats (plain Python
loop inside the container — no host cron, nothing touches the EBMS stack).
A failed run (Yahoo outage, typo'd ticker file) is logged and retried at the
next 07:00 slot; `restart: unless-stopped` covers reboots.

### Config

Edit `config/tickers.json` on the host (bind-mounted read-only into the job,
no rebuild needed — picked up at the next run):

```json
{
  "tickers": ["AAPL", "MSFT", "CSCO", "MU", "SNDK", "SE", "NOBA.ST"],
  "lookback_days": 90,
  "interval": "1d",
  "positions": { "AAPL": 100, "MSFT": 100, "CSCO": 100, "MU": 100,
                 "SNDK": 100, "SE": 100, "NOBA.ST": 100 },
  "margin_rate": 0.20
}
```

Use Yahoo's symbols (non-US listings need the exchange suffix, e.g.
`NOBA.ST`). Unknown/failed tickers are skipped and listed under `skipped`
in the output; the run only fails if fewer than 2 tickers survive.

`positions` maps ticker -> CFD quantity and `margin_rate` (default 0.20) is
the margin fraction — both feed the `margin_account` section described
below. A ticker without a `positions` entry defaults to quantity 0: it stays
in the correlations but is **omitted from the margin rows entirely** (not
listed with zeros). Set every position to 0 (or drop the key) and the
snapshot is written without a `margin_account` section at all.

Each `positions` entry accepts **two forms, mixed freely** — the plain
quantity shown above, or an object with an optional `entry_price` (the
actual price the CFD position was opened at, used only as the P&L basis):

```json
"positions": {
  "AAPL":  { "qty": 100, "entry_price": 185.5 },
  "MSFT":  { "qty": 100 },
  "CSCO":  100
}
```

`entry_price` is optional: tickers without one fall back to the **first
available close of the current year** as the P&L basis (see the
`pnl_timeseries` section below — which basis was used is recorded per
ticker in the snapshot). Old configs with plain numeric positions keep
working unchanged.

### CFD margin account (`margin_account` in each snapshot)

For the latest trading day the job also records, per positioned ticker, the
open / midday / close price and the margin required at each checkpoint
(`price x quantity x margin_rate`), plus totals:

- `rows[]`: `{ticker, quantity, open, midday, close, midday_source,
  margin_open, margin_midday, margin_close}`
- `totals`: `position_value_{open,midday,close}`,
  `margin_{open,midday,close}`, and `peak_margin` (max of the three margin
  totals)
- `skipped`: positioned tickers Yahoo returned no usable daily bar for

**Midday caveat:** midday is the intraday bar (60m, falling back to 30m)
nearest the session midpoint (`midday_source: "intraday"`). Yahoo's intraday
data is best-effort — when it's missing or doesn't cover the session, the
job uses `(high+low)/2` of the daily bar instead and flags the row with
`midday_source: "hl_midpoint_proxy"` (the dashboard marks such values
with `≈`). That proxy is NOT an actual traded midday price.

Two more caveats: prices are the **unadjusted** daily bars (margin is on
traded prices, not the adjusted series used for correlations), and totals
sum each ticker's native quote currency with no FX conversion (`NOBA.ST` is
SEK, the rest USD) — treat cross-currency totals as indicative only. A
failure in this section never fails the correlation run; the snapshot is
then written without `margin_account` and the reason is in the job log.

### YTD margin time series (`margin_timeseries` in each snapshot)

With positions configured the snapshot also carries a `margin_timeseries`
section: `{margin_rate, midday_source: "hl_midpoint_proxy", points: [...]}`
where each point is `{date, open, midday, close, n_tickers}` — the **total**
margin required at that day's open / midday / close, for every trading day
from Jan 1 of the current year through today (one batched unadjusted-OHLC
download). The dashboard draws it as the "Margin Required — Year to Date"
line chart under the margin table (hidden for snapshots without the section).

- **Midday proxy caveat:** intraday history is not available months back, so
  historical midday is always the `(high+low)/2` daily-bar proxy — indicative
  only, not a traded price. Only today's point is refined with the real
  intraday midday total from `margin_account` when one exists (that point is
  then flagged `midday_source: "intraday"`).
- **History is recomputed fresh each run** from the current
  `config/tickers.json` — it is not accumulated. That means position or
  margin-rate changes apply **retroactively to the whole YTD series**: the
  chart always answers "what would this book have required all year", not
  "what did it require at the time".
- `n_tickers` records how many positioned tickers had a bar that day; a
  lower count = partial day (exchange holiday, late listing), visible in the
  chart tooltip.
- Same guarantee as `margin_account`: any failure only logs a warning and the
  snapshot is written without the section — the correlation run never fails
  because of it.

### CFD account P&L (`pnl_timeseries` in each snapshot)

From the **same single YTD OHLC download** as `margin_timeseries` (the data
is fetched once and feeds both sections), the snapshot also carries a
`pnl_timeseries` section: the cumulative daily profit/loss of the configured
positions from the start of the year through the **last completed trading
day** — a bar dated "today" (a live, partial session) is always dropped.

```json
"pnl_timeseries": {
  "basis_by_ticker": { "AAPL": { "basis": "entry_price", "value": 185.5 },
                       "MSFT": { "basis": "first_close", "value": 421.9 } },
  "points": [ { "date": "2026-01-02", "pnl": -120.5,
                "daily_change": null, "n_tickers": 7 }, ... ]
}
```

- **Basis fallback:** per ticker, P&L is measured against the configured
  `entry_price` when one is set in `config/tickers.json`; otherwise against
  the **first available close of the current year**. `basis_by_ticker`
  records which was used and its value. With the first-close basis a
  ticker's P&L is by construction 0 on its first trading day of the year.
- `pnl(t) = Σ qty × (close(t) − basis)` over the tickers that have a bar
  that day (`n_tickers` keeps partial days visible, exactly as in
  `margin_timeseries`); `daily_change` is `pnl(t) − pnl(t−1)` (`null` on
  the first point — there is no previous day).
- **Fees caveat:** the figures are pure price P&L on unadjusted closes.
  They exclude commissions, spreads, overnight financing / swap charges,
  dividend adjustments and FX conversion (each ticker is summed in its
  native quote currency) — so the real CFD account balance will differ,
  usually to the downside.
- Like the other CFD sections, history is **recomputed fresh each run** from
  the current config (position/entry-price edits apply retroactively to the
  whole YTD series), and any failure only logs a warning — the snapshot is
  then written without the section.

The dashboard renders this as the "CFD Account P&L — Year to Date" card:
a single cumulative-P&L line with an emphasized zero baseline (blue above
zero / red below, matching the heatmap's sign convention), stat tiles for
Current P&L and the Best/Worst `daily_change` days, and a footnote stating
each basis and the fees exclusion.

### Momentum screener (runs after each correlation snapshot)

After writing the correlation snapshot, the same daily job runs a momentum
screener over a ~500-ticker US large-cap universe and writes **separate**
outputs to the same volume:

- `/data/output/screener/YYYY-MM-DD.json` — one file per day, kept as history
- `/data/output/screener-latest.json` — same content, stable name

Served by the API (same guarded pattern — empty list / 404 before the first
run, never a 500):

```bash
curl -s http://localhost:8080/api/v1/screener             # list available dates
curl -s http://localhost:8080/api/v1/screener/latest      # newest screener run
curl -s http://localhost:8080/api/v1/screener/2026-08-25  # specific day
```

**Config** — the optional `"screener"` section of `config/tickers.json`
(every key has a default; the values below ARE the defaults):

```json
"screener": {
  "enabled": true,
  "min_market_cap": 20e9,
  "min_price": 10,
  "min_return_2d": 0.10,
  "min_return_5d": 0.30,
  "min_rvol": 1.5,
  "min_avg_dollar_vol": 20e6,
  "require_above_ma20": true,
  "require_above_ma50": true,
  "doubler_windows_days": [90, 270],
  "doubler_min_return": 1.00,
  "score_weights": { "ret5d": 0.30, "ret2d": 0.20, "rvol": 0.20,
                     "dist_ma20": 0.15, "dist_ma50": 0.15 }
}
```

A ticker passes when: last close >= `min_price`, 2-day return >=
`min_return_2d`, 5-day return >= `min_return_5d`, relative volume (last
volume ÷ prior 20-day average) >= `min_rvol`, 20-day average dollar volume
>= `min_avg_dollar_vol`, price above MA20/MA50 (when required), and market
cap >= `min_market_cap`. Market cap is looked up **only** for the few
tickers that already passed every price-based filter; a ticker whose cap
Yahoo cannot provide is **kept and flagged** `cap_unknown: true`, never
silently dropped. Survivors get a 0–100 momentum score (each component
min-max normalized across that day's survivors, weighted per
`score_weights`; a lone survivor scores 100). `new_52w_high` comes from a
second, finalists-only `period="1y"` fetch and is `null` when that fetch
fails. Set `"enabled": false` to skip the screener entirely.

**Doublers (second list in every snapshot)** — alongside the momentum
candidates, each snapshot carries a `doublers` list: tickers up
`doubler_min_return` (default **+100%**) over *any* of the
`doubler_windows_days` calendar windows (default 90 and 270 days). Window
returns are close-to-close over the derived TRADING-day count
`round(window × 252/365)` — 90d → 62 bars, 270d → 186 bars — and are also
attached to every momentum candidate as `ret_90d` / `ret_270d` (`null` when
the ticker has too little history). A doubler only has to pass the **price
and average-dollar-volume gates** (a stock that doubled in a quarter usually
fails the short-term 2d/5d/RVOL gates — that is the point of the separate
list); the market-cap check is the same as for momentum finalists (known
small caps dropped, unknown caps kept + `cap_unknown`). Each row:
`{ticker, price, ret_90d, ret_270d, window_hit ("90d"|"270d"|"both"), rvol,
market_cap, new_52w_high}` ranked by the larger window return.
`new_52w_high` comes from the main fetched window (needs ≥252 bars; `null`
otherwise — no extra fetch for doublers). **Fetch-size note:** supporting
the 270-day window pushed the daily universe fetch from ~110 to ~400
calendar days of bars per ticker (~4× the data, same one call per
100-ticker batch) — expect the screener step to take proportionally longer.
The dashboard shows the list as a second "Doublers (≥100% in 90d/270d)"
table under the momentum table, with its own empty state; the same
discovery-not-advice caveat covers both tables.

**Historical replay / backfill (CLI, scheduled path unchanged)** — the
screener is also runnable standalone to (re)create dated snapshots:

```bash
# one historical day: full screen using ONLY data <= that date
docker compose run --rm correlation-job python -m app.momentum_screener --asof 2026-08-10

# seed history: replay the last 30 trading days from ONE fetch
docker compose run --rm correlation-job python -m app.momentum_screener --backfill 30

# rewrite dates that already have files (default: skipped)
docker compose run --rm correlation-job python -m app.momentum_screener --backfill 30 --force
```

Replays write `screener/<date>.json` **only** — `screener-latest.json` is
never touched, so the dashboard keeps showing the real latest live run.
`--backfill N` fetches once (window extended back to cover N extra trading
days), replays each of the last N trading days by slicing that one dataset,
and skips dates whose files already exist unless `--force`; market caps are
looked up once per ticker for the whole backfill. Every replayed snapshot is
marked `"backfilled": true` and carries an honest `note`: **market-cap
filtering uses CURRENT caps** — free data has no historical caps, the same
limitation the backtest states. Price data is strictly sliced to ≤ the
snapshot date (no look-ahead); the finalists-only 1-year 52w-high fetch is
skipped in replay mode (it would anchor to "now"), so `new_52w_high` is
`null` when the fetched window can't answer it. Backfilling is the intended
way to seed history for the **report card** below. `--config`,
`--output-dir` and `--universe` follow the daily-job conventions; the CLI
runs even when `screener.enabled` is false (invoking it is explicit enough).

**Signal report card (`report_card` in the latest snapshot)** — at the end
of each daily screen the job grades its own past picks: for the snapshots
dated 5, 10 and 20 **trading** days ago (nearest existing file within ±2
days), each recorded pick's realized return = latest close ÷ recorded price
− 1. Per lookback and separately for momentum candidates and doublers the
snapshot gets `{n, snapshot_date, win_rate, mean, median, best:{ticker,ret},
worst:{ticker,ret}}`; lookbacks with no matching file (or no gradable picks)
are simply omitted, and with nothing to grade the key is absent entirely.
A report-card failure only logs — the screen itself is never lost. The
dashboard renders it as a compact "Report card — how past picks did" block
under the two tables (hidden while absent — backfill some history to make it
appear). Mind the bias: backfilled history was selected with current market
caps, and the report card grades close-to-close without costs.

**Universe maintenance** — `config/universe.json` is a **static snapshot**
of ~500 well-known US large caps (S&P 500-style), embedded 2026-08. Index
membership drifts (additions, mergers, ticker changes), so refresh the list
occasionally; unknown/delisted symbols are simply counted in `skipped`.
Yahoo symbol notation applies (`BRK-B`, `BF-B`).

**Run-time note:** this is by far the heaviest fetch of the daily run —
~500 tickers × ~400 calendar days of daily bars (chunked into batches of
100, each retried; grown from ~110 days for the doubler windows + 52w
highs) vs the handful the correlation job needs. Expect it to add minutes,
not seconds. It runs strictly **after** the correlation outputs are written and
is guarded: a screener failure only logs, it never fails the correlation run.

**Not investment advice:** an empty `candidates` list is a *normal* daily
outcome — the default gates (+10% in 2 days AND +30% in 5 days) are strict
on purpose. This is a **discovery screen, not a buy signal**: a +30% week
can be accumulation, a short squeeze, or pure hype. Always do second-stage
analysis (news, filings, float/short interest, liquidity) before acting.
The dashboard shows the latest run as a "Momentum Screener" card below the
P&L chart (ranked table with score bars and MA / new-high badges; hidden
until the first screener run exists).

### Momentum-screen backtest (batch CLI, NOT part of the daily job)

`app/backtest.py` backtests the screener's core signal (2d/5d return
thresholds from the `screener` config) over the ~500-ticker universe,
mechanically and with no look-ahead: signal at close of day *t* (ret2d ≥ th2
AND ret5d ≥ th5), **entry at the next day's OPEN**, exit at the close H
trading days after the entry day, minus a flat round-trip cost in bps. Run it
manually when you want to (re-)evaluate the screen — it never runs as part of
the daily job:

```bash
# full run with grid + variants + dynamic exits (defaults: 8 years, H=1,3,5,10,20, costs 5/10/25/50 bps)
docker compose run --rm correlation-job python -m app.backtest --grid --variants --exits all

# custom horizons/costs, forced refetch
docker compose run --rm correlation-job python -m app.backtest \
  --years 8 --holding 1,3,5,10,20 --costs 5,10,25,50 --refresh-data
```

Flags: `--years N` (history depth, default 8), `--holding a,b,c` (holding
periods in trading days), `--costs a,b,c` (round-trip costs in bps; a gross
0-bps row is always included), `--grid` (th2 × th5 threshold grid of signal
counts + forward-10d returns), `--variants` (baseline vs +RVOL≥2 vs
+52-week-high at H=10, **plus a per-variant train/val/OOS table** — same
60/20/20 split and sign-flip warnings as the headline split, net of the
default cost level), `--exits fixed|ma10|trail2atr|all` (dynamic exit styles,
see below; default `fixed` = none), `--refresh-data` (ignore the cache),
`--config/--output-dir/--universe` (same conventions as the daily job).

**Excess vs SPY (alpha) columns:** every trade also gets an excess return =
trade return − SPY close-to-close return over the **same entry/exit dates**
(SPY aligned by date, forward-filled over benchmark gaps). `exc mean` /
`exc med` columns appear in the per-holding tables, the variants table, the
per-variant segment table and the dynamic-exits table; the continuation
curve gains a second pair of lines (mean/median cumulative excess). An
"edge" whose excess is ~0 is just beta.

**Dynamic exits (`--exits`):** evaluated for the baseline and 52w_high
variants, side by side with fixed H=10/H=20, plus the average holding days
per style. `ma10` exits at the close of the first day the close falls below
the 10-day SMA (computed on closes up to and including that day; the
entry-day close counts as day 0). `trail2atr` uses a trailing stop at
highest-close-since-entry − 2×ATR14 (ATR from daily H/L/C, causal — no
look-ahead) and exits at the close of the first day the close is below the
stop. Both are capped at 20 trading days. **All dynamic exits are checked
and filled at daily closes only** — real stops would fill intraday, usually
worse, so treat these rows as a conservative approximation.

**Runtime + cache:** the FIRST run is heavy — ~500 tickers × 8 years of daily
OHLCV, fetched in batches of 100 with the usual retries; expect several
minutes and treat Yahoo rate-limits as normal (failed batches are skipped and
recorded, not fatal). The bars are cached under
`/data/output/backtest/history/` (one `batch-NNN.csv.gz` per batch + SPY +
`manifest.json` with the fetch date); later runs reuse the cache and finish in
seconds. The cache is invalidated automatically when `--years` changes, the
universe gains tickers, or the stored field set changes — caches written
before the dynamic-exits feature lack High/Low bars (needed for ATR) and are
refetched automatically on the first run after the update. Pass
`--refresh-data` to force a refetch (do this occasionally — the cache does
not extend itself to "today").

**Outputs:** `/data/output/backtest/report-YYYYMMDD-HHMM.json` (full numbers)
and `report-YYYYMMDD-HHMM.md` (human-readable, also printed to stdout):
limitations block first, then per-holding return tables across cost levels
(incl. the excess-vs-SPY columns), the H=5 non-overlapping equity-curve max
drawdown, the day+1..+20 momentum continuation curve (raw + excess lines),
the SPY benchmark, optional grid/variants tables (variants incl. the
variant × train/val/OOS segment table), the dynamic-exits table when
`--exits` is used, and a train/validation/out-of-sample (60/20/20 by date)
comparison with an explicit **WARNING** when the out-of-sample mean flips
sign vs train — the same warning is issued per variant in the segment table.

**How to interpret:**

- *Continuation vs reversal:* the continuation curve is the centerpiece — if
  the mean/median cumulative return keeps rising after entry, momentum
  continues (longer holds are justified); if it humps and fades, buying
  strength gets faded and only short holds can work.
- *Median vs mean:* momentum trade distributions are right-skewed. A positive
  mean with a ~zero/negative median means most trades lose and a few huge
  winners pay for everything — the p10/p90 columns show how wide that really
  is. Judge the strategy on the median and the percentiles, not the mean.
- *Benchmark:* compare per-holding means to the same-window SPY means — an
  "edge" smaller than index drift is just beta.
- *Trust the OOS split:* thresholds that only work in-sample (grid cells with
  few signals, sign-flip warnings) are noise, not edge.

**Stated biases (the report leads with these):** survivorship (the universe is
*today's* large caps — delisted losers are absent, results biased UP), no
historical market cap (cap filter ≈ universe membership), adjusted closes
(dividends/splits folded into returns), flat-bps costs. It is a **signal
study**: overlapping signals all count, no capital constraint. Additionally:
**variant/exit selection is itself a mild overfitting risk** — comparing
several variants and exit styles and keeping the best-looking one at the
n≈100 scale needs out-of-sample confirmation before acting on it. Numbers are
for hypothesis evaluation, not expected live returns.

### Manual one-off run

```bash
docker compose run --rm correlation-job python -m app.daily_correlation
```

### Where outputs land

The job writes to the shared named volume `correlation-output`, mounted at
`/data/output` (read-only in the API container):

- `/data/output/correlations/YYYY-MM-DD.json` — one file per day, kept as history
- `/data/output/latest.json` — same content, stable name

Each file holds the correlation matrix, per-ticker annualized volatility /
drift / last price, skip metadata, the `margin_account` section (see above,
when positions are configured), and a `simulate_payload` section that is
a ready-to-POST `/api/v1/simulate` body (drift pinned to 0, edit
`position_units` / `margin_pct` before posting), e.g.:

```bash
curl -s http://localhost:8080/api/v1/correlations/latest \
  | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['simulate_payload']))" \
  | curl -s -X POST http://localhost:8080/api/v1/simulate \
      -H 'Content-Type: application/json' -d @- | python3 -m json.tool
```

### Endpoints

```bash
curl -s http://localhost:8080/api/v1/correlations             # list available dates
curl -s http://localhost:8080/api/v1/correlations/latest      # newest snapshot
curl -s http://localhost:8080/api/v1/correlations/2026-08-12  # specific day (404 if absent)
```

Before the first job run these return an empty list / 404 — never an error.

### Image size note

To keep a single image, `requirements-data.txt` (yfinance + pandas) is now
baked into the build alongside the API deps. That adds roughly 150–200 MB
to the image; acceptable on the R430, but if that ever matters, split the
job into its own Dockerfile stage. The API container still makes no
outbound calls — only `correlation-job` talks to the internet (Yahoo
Finance).

### Troubleshooting: Yahoo Finance fetch failures

Two failure signatures show up in `docker compose logs correlation-job`:

**1. `Expecting value: line 1 column 1 (char 0)` and/or
`YFTzMissingError ... possibly delisted`**

Yahoo is rate-limiting or blocking the server's IP and returning an HTML
block page instead of JSON; "possibly delisted" is almost never about the
ticker actually being delisted. The job already retries every fetch 3 times
with backoff and jitter; tickers that still fail are recorded in the
snapshot's `skipped` map with reason `yahoo_blocked_or_missing`, and the run
only fails when fewer than 2 tickers survive. If **every** ticker fails:

- Rebuild without cache so the image picks up the pinned (current) yfinance
  from `requirements-data.txt` — newer versions use curl_cffi browser
  impersonation, which gets past most of Yahoo's blocking:

  ```bash
  docker compose build --no-cache correlation-job
  docker compose up -d correlation-job
  # optional immediate one-off run instead of waiting for 07:00 UTC:
  docker compose run --rm correlation-job python -m app.daily_correlation
  ```

- If it still fails, the server IP is likely rate-limited: just wait — the
  loop retries at the next 07:00 UTC slot. Repeated daily failures usually
  mean yfinance needs bumping again (Yahoo changes its defenses; old
  yfinance versions get blocked first).

**2. `Failed to create TzCache ... [Errno 17] File exists`**

yfinance tried to create its timezone cache under `$HOME/.cache` inside the
hardened container (read-only root filesystem, tmpfs `/tmp`). Fixed in code
+ compose: `app/daily_correlation.py` relocates the tz cache to
`/tmp/yfinance-tz` at import (and falls back to `HOME=/tmp` /
`XDG_CACHE_HOME=/tmp/.cache` when `$HOME` is unwritable), and the
`correlation-job` service mounts a writable tmpfs at `/tmp` with
`read_only: true`. If you see this error, you are running an image/compose
older than that fix — rebuild:

```bash
docker compose build --no-cache correlation-job && docker compose up -d
```

## Stop / update / logs

```bash
docker compose logs -f            # request log incl. per-sim timings
docker compose down               # stop (image + nothing else remains)
docker compose up -d --build      # rebuild + restart after code changes
```

`restart: unless-stopped` means it survives reboots alongside the EBMS
stack with no extra setup.

## Nightly scheduled run (optional)

The service itself is on-demand, but if you want a nightly snapshot of the
risk numbers, cron on the R430 posting to the running service is enough —
no k8s CronJob needed:

```cron
# /etc/cron.d/risk-sim-nightly  (adjust paths; keep the service running)
15 2 * * * fde curl -s -X POST http://localhost:8080/api/v1/simulate \
  -H 'Content-Type: application/json' \
  -d @/home/fde/risk-sim/examples/request_example.json \
  > /home/fde/risk-sim-results/$(date +\%F).json 2>/dev/null
```

If you'd rather not keep the container running at all, the batch variant is:

```bash
docker compose up -d && sleep 5 && curl -s -X POST ... > out.json && docker compose down
```

## Resource envelope

Container is capped at 2 CPUs / 2 GB RAM (matches the old k8s limits) and
numpy's thread pool is pinned to 2 (`OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS`),
so even a maximum-size request (60M simulated points, ~2 GB working set,
guarded in `app/models.py`) cannot starve the EBMS containers. If risk-sim
ends up being the only heavy workload, raise `cpus` and the two thread env
vars together in `docker-compose.yml`.
