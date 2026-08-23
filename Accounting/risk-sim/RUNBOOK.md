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
Snapshots written before the margin feature existed have no
`margin_account` / `margin_timeseries` section — the dashboard simply hides
those blocks for such dates. Before the first correlation-job run it shows an empty state
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
