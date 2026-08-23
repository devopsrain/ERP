"""
Pull real historical daily prices (Yahoo Finance via yfinance) and turn them
into a request payload for the risk-sim /api/v1/simulate endpoint:
annualized volatility per asset + a correlation matrix, both computed from
actual daily log returns rather than guessed.

This is a local data-prep step, not part of the deployed service — it needs
outbound internet access to Yahoo Finance, which the risk-sim container
deliberately does NOT have (see README "Security notes"). Run this on your
laptop or anywhere with internet, then POST the resulting JSON to the
service running on the R430.

Usage:
    pip install -r requirements-data.txt
    python fetch_market_data.py --tickers MU,SNDK,SE,NOBA.ST --lookback-days 252 --output live_request.json

Note on ticker symbols: use Yahoo Finance's format, which includes exchange
suffixes for non-US listings — e.g. NOBA Bank Group trades in Stockholm, so
its Yahoo ticker is "NOBA.ST", not "NOBA".
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

TRADING_DAYS = 252


def fetch_history(tickers: list[str], lookback_days: int) -> pd.DataFrame:
    end = datetime.now()
    # buffer the calendar window since weekends/holidays eat into it
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)

    raw = yf.download(
        tickers,
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    if raw.empty:
        sys.exit("No data returned at all — check tickers and network access to Yahoo Finance.")

    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:
        # single-ticker request collapses to a flat frame
        closes = raw[["Close"]]
        closes.columns = tickers

    closes = closes.dropna(how="all").tail(lookback_days + 1)
    return closes


def compute_stats(closes: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.DataFrame, pd.Series]:
    log_returns = np.log(closes / closes.shift(1)).dropna(how="all")
    ann_vol = log_returns.std() * np.sqrt(TRADING_DAYS)
    ann_drift = log_returns.mean() * TRADING_DAYS
    corr = log_returns.corr()
    last_price = closes.iloc[-1]
    return ann_vol, ann_drift, corr, last_price


def build_payload(
    tickers: list[str],
    ann_vol: pd.Series,
    ann_drift: pd.Series,
    corr: pd.DataFrame,
    last_price: pd.Series,
    include_drift: bool,
) -> dict:
    assets = []
    for t in tickers:
        assets.append({
            "ticker": t,
            "initial_price": round(float(last_price[t]), 4),
            "annual_volatility": round(float(ann_vol[t]), 4),
            "annual_drift": round(float(ann_drift[t]), 4) if include_drift else 0.0,
            "position_units": 1.0,   # placeholder — set to your actual position size
            "margin_pct": None,       # placeholder — set to your actual margin %, e.g. 0.20
        })
    correlation_matrix = corr.loc[tickers, tickers].round(4).values.tolist()
    return {
        "assets": assets,
        "correlation_matrix": correlation_matrix,
        "num_simulations": 20000,
        "horizon_days": 20,
        "confidence_level": 0.95,
        "random_seed": 42,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tickers", required=True, help="Comma-separated Yahoo Finance tickers, e.g. MU,SNDK,SE,NOBA.ST")
    parser.add_argument("--lookback-days", type=int, default=252, help="Trading days of history to use (default: 252 = ~1yr)")
    parser.add_argument("--output", default="live_request.json")
    parser.add_argument(
        "--include-drift", action="store_true",
        help="Populate annual_drift from historical mean return instead of leaving it at 0. "
             "Off by default on purpose: historical drift over any realistic lookback window is "
             "a noisy, unreliable estimate of future returns, especially for the high-beta names "
             "in this conversation. Leaving it at 0 keeps the simulation about dispersion/risk, "
             "not a disguised return forecast.",
    )
    args = parser.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    closes = fetch_history(tickers, args.lookback_days)

    missing = [t for t in tickers if t not in closes.columns or closes[t].dropna().empty]
    if missing:
        sys.exit(
            f"No usable data for: {missing}. Check the ticker symbols — non-US listings need "
            f"Yahoo's exchange suffix, e.g. NOBA Bank Group is 'NOBA.ST', not 'NOBA'."
        )

    ann_vol, ann_drift, corr, last_price = compute_stats(closes)
    payload = build_payload(tickers, ann_vol, ann_drift, corr, last_price, args.include_drift)

    with open(args.output, "w") as f:
        json.dump(payload, f, indent=2)

    print(f"Wrote {args.output} from {len(closes)} trading days through {closes.index[-1].date()}")
    print("Placeholders to edit before posting: position_units (default 1.0) and margin_pct (default null) per asset.")
    if not args.include_drift:
        print("annual_drift left at 0.0 for all assets (use --include-drift to populate from historical mean; see --help for the caveat).")


if __name__ == "__main__":
    main()
