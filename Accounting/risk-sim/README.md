# risk-sim — correlated-price Monte Carlo risk assessment service

A small FastAPI service that runs a correlated multi-asset Monte Carlo
simulation (Geometric Brownian Motion via Cholesky-decomposed correlation)
and reports **Value at Risk (VaR)**, **Conditional VaR / Expected Shortfall**,
and **margin-call probability** for a leveraged portfolio. Built to run as a
container on a Kubernetes cluster hosted on a Dell R430.

This is a *risk measurement* tool, not a forecasting/advice tool: it takes
your volatility and correlation assumptions as input and tells you the
distribution of outcomes those assumptions imply. `annual_drift` defaults to
`0.0` (no directional view baked in) unless you explicitly set it.

## Architecture

```
app/
  main.py         FastAPI routes: /healthz, /readyz, /api/v1/simulate
  models.py       Pydantic request/response schemas + input validation
  risk_engine.py  Pure-numpy Monte Carlo engine (unit-testable standalone)
Dockerfile        Multi-stage build, non-root runtime user, slim base image
k8s/              Namespace, ConfigMap, Deployment, Service, optional HPA
examples/         Sample request payload
```

## API

### `POST /api/v1/simulate`

Request:

```json
{
  "assets": [
    {"ticker": "MU", "initial_price": 749.0, "annual_volatility": 0.65,
     "annual_drift": 0.0, "position_units": 6.68, "margin_pct": 0.20}
  ],
  "correlation_matrix": [[1.0]],
  "num_simulations": 20000,
  "horizon_days": 20,
  "confidence_level": 0.95,
  "random_seed": 42
}
```

- `annual_volatility` — annualized sigma (0.65 = 65%). Use realized/historical
  vol from your own data source; nothing here is fetched automatically.
- `margin_pct` — optional. If set (e.g. 0.20 for 20% margin / 5x leverage),
  the engine reports the probability that price crosses the margin-call
  threshold at any point in the simulated horizon, not just at the end.
- `correlation_matrix` — must be symmetric and match `assets` 1:1 in order.
  If it isn't positive semi-definite (common with hand-typed pairwise
  correlations), the engine auto-repairs it via eigenvalue clipping.
- Resource guard: `num_simulations * horizon_days * n_assets` is capped at
  60,000,000 to keep a single request from starving the node.

Response includes `value_at_risk`, `conditional_value_at_risk`,
`prob_of_loss`, `portfolio_margin_call_probability`, per-asset terminal price
percentiles, and the *realized* correlation matrix from the simulated paths
(a sanity check against your input assumptions).

### `GET /healthz` / `GET /readyz` / `GET /api/v1/version`

Liveness, readiness (runs a tiny self-test simulation), and version info —
wired into the k8s probes below.

## Run locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

curl -s -X POST http://localhost:8080/api/v1/simulate \
  -H 'Content-Type: application/json' \
  -d @examples/request_example.json | python3 -m json.tool
```

The example payload reuses the four names and rough entry prices from our
earlier margin discussion (MU, SNDK, SE, NOBA at 20% margin each). The
volatility and correlation figures in that file are illustrative
placeholders — swap in real historical/implied figures before treating any
output as meaningful.

## Getting real market data

The example payload above uses illustrative placeholder numbers. To build a
request from actual 2026 historical prices instead:

```bash
pip install -r requirements-data.txt
python data/fetch_market_data.py \
  --tickers MU,SNDK,SE,NOBA.ST \
  --lookback-days 252 \
  --output live_request.json

curl -s -X POST http://localhost:8080/api/v1/simulate \
  -H 'Content-Type: application/json' \
  -d @live_request.json | python3 -m json.tool
```

This pulls daily closes from Yahoo Finance (via `yfinance`) and computes
`annual_volatility` and the `correlation_matrix` from actual daily log
returns over the lookback window, rather than guessed figures. Notes:

- **Non-US tickers need Yahoo's exchange suffix** — e.g. NOBA Bank Group is
  `NOBA.ST` (Stockholm), not `NOBA`.
- `position_units` and `margin_pct` are written as placeholders
  (`1.0` / `null`) since the script has no way to know your actual position
  sizes — edit `live_request.json` before posting it.
- `annual_drift` defaults to `0.0` even here. Pass `--include-drift` to
  populate it from historical mean return if you want it, but historical
  drift over any realistic lookback window is a noisy predictor of future
  returns — treat it as a sensitivity-analysis input, not a forecast.
- This script needs outbound internet access and runs locally (your laptop,
  or anywhere with a route to Yahoo Finance) — deliberately **not** inside
  the deployed container, which has no outbound network access by design.
- Longer lookback windows (252 days ≈ 1yr) give more stable volatility/
  correlation estimates but are slower to react to recent regime changes
  (e.g. SanDisk's realized vol looked very different in January vs. after
  its 400%+ run this year) — there's no single "correct" window, so it's
  worth running a couple of lengths and comparing.

## Build the image

```bash
docker build -t risk-sim:1.0.0 .
```

### Getting the image onto the R430

If you're running **k3s** (the common choice for a single bare-metal box —
lightweight, one binary, built-in containerd):

```bash
docker save risk-sim:1.0.0 | k3s ctr images import -
```

If you're running a full **kubeadm** cluster with `containerd`:

```bash
docker save risk-sim:1.0.0 -o risk-sim.tar
sudo ctr -n k8s.io images import risk-sim.tar
```

Or push to a local registry (`registry:2` container) and reference
`localhost:5000/risk-sim:1.0.0` in the Deployment instead — cleaner if you'll
be rebuilding often.

## Deploy

```bash
kubectl apply -f k8s/00-namespace.yaml
kubectl apply -f k8s/01-configmap.yaml
kubectl apply -f k8s/02-deployment.yaml
kubectl apply -f k8s/03-service.yaml
kubectl apply -f k8s/04-hpa.yaml   # optional, needs metrics-server

kubectl -n risk-sim get pods -w
curl http://<r430-node-ip>:30080/healthz
```

## Sizing notes for the R430

- Memory footprint scales roughly as `num_simulations * horizon_days *
  n_assets * 8 bytes * ~4` (a few working arrays coexist during a run).
  20,000 sims × 20 days × 4 assets ≈ 51 MB of raw floats — trivial. Pushing
  toward the 60M-point request cap gets you into the ~2GB range, which is
  why the container `limits.memory` is set to 2Gi and the request-size guard
  exists in `models.py`.
- `OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS` are capped at 2 in the image so a
  single pod doesn't claim every core on the node; raise them (and the CPU
  `limits`) if this is the only workload on the box.
- `replicas: 2` gives you a rolling-update-safe minimum on a single node;
  drop to 1 if the R430 is resource-constrained.

## Security notes

- Runs as non-root (`uid 10001`), read-only root filesystem, all Linux
  capabilities dropped.
- No outbound network calls anywhere in the service — it only computes on
  the numbers you POST to it.
- Input validation caps simulation size/count to prevent a single request
  from exhausting node resources (see `models.py`).
