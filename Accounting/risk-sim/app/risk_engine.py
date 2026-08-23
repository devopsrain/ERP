"""
Correlated multi-asset Monte Carlo simulator.

Method: correlated Geometric Brownian Motion via Cholesky decomposition of
the (repaired, if necessary) correlation matrix. Pure NumPy, vectorized —
no Python-level loops over simulation paths.

This module is intentionally dependency-light (numpy only) so it's easy to
unit test outside of FastAPI/Kubernetes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

import numpy as np

TRADING_DAYS_PER_YEAR = 252


@dataclass
class Asset:
    ticker: str
    initial_price: float
    annual_volatility: float
    annual_drift: float
    position_units: float
    margin_pct: Optional[float] = None


def nearest_correlation_matrix(corr: np.ndarray, eps: float = 1e-8) -> np.ndarray:
    """
    Repairs a correlation matrix that isn't positive semi-definite (common
    when a matrix is hand-assembled pairwise rather than estimated from
    actual joint data) by clipping negative eigenvalues and renormalizing
    back to unit diagonal. Returns the input unchanged if it's already valid.
    """
    corr = np.array(corr, dtype=float)
    corr = (corr + corr.T) / 2.0  # enforce symmetry
    eigvals, eigvecs = np.linalg.eigh(corr)
    if np.all(eigvals > eps):
        return corr
    eigvals_clipped = np.clip(eigvals, eps, None)
    repaired = eigvecs @ np.diag(eigvals_clipped) @ eigvecs.T
    d = np.sqrt(np.diag(repaired))
    repaired = repaired / np.outer(d, d)
    np.fill_diagonal(repaired, 1.0)
    return repaired


def simulate_correlated_paths(
    assets: List[Asset],
    correlation_matrix: List[List[float]],
    num_simulations: int,
    horizon_days: int,
    random_seed: Optional[int] = None,
) -> np.ndarray:
    """
    Returns price_paths with shape (num_simulations, horizon_days, n_assets).
    """
    n_assets = len(assets)
    corr = nearest_correlation_matrix(np.array(correlation_matrix, dtype=float))
    chol = np.linalg.cholesky(corr)  # lower-triangular, corr = chol @ chol.T

    rng = np.random.default_rng(random_seed)
    dt = 1.0 / TRADING_DAYS_PER_YEAR

    s0 = np.array([a.initial_price for a in assets])
    mu = np.array([a.annual_drift for a in assets])
    sigma = np.array([a.annual_volatility for a in assets])

    # independent standard normals: (sims, days, assets)
    z = rng.standard_normal((num_simulations, horizon_days, n_assets))
    # correlate across the asset axis: for each (sim, day), z_corr = chol @ z
    z_corr = z @ chol.T

    drift_term = (mu - 0.5 * sigma ** 2) * dt  # shape (n_assets,)
    diffusion_term = sigma * np.sqrt(dt) * z_corr  # shape (sims, days, n_assets)

    log_returns = drift_term[None, None, :] + diffusion_term
    cum_log_returns = np.cumsum(log_returns, axis=1)
    price_paths = s0[None, None, :] * np.exp(cum_log_returns)
    return price_paths


def run_risk_assessment(
    assets: List[Asset],
    correlation_matrix: List[List[float]],
    num_simulations: int,
    horizon_days: int,
    confidence_level: float,
    random_seed: Optional[int] = None,
) -> dict:
    price_paths = simulate_correlated_paths(
        assets, correlation_matrix, num_simulations, horizon_days, random_seed
    )
    n_assets = len(assets)
    s0 = np.array([a.initial_price for a in assets])
    units = np.array([a.position_units for a in assets])

    initial_portfolio_value = float(np.dot(s0, units))
    terminal_prices = price_paths[:, -1, :]  # (sims, n_assets)
    terminal_portfolio_value = terminal_prices @ units  # (sims,)
    terminal_pnl = terminal_portfolio_value - initial_portfolio_value

    loss_quantile = np.percentile(terminal_pnl, (1 - confidence_level) * 100)
    value_at_risk = float(max(-loss_quantile, 0.0))
    tail_losses = terminal_pnl[terminal_pnl <= loss_quantile]
    conditional_value_at_risk = float(-tail_losses.mean()) if tail_losses.size else value_at_risk
    prob_of_loss = float(np.mean(terminal_pnl < 0))

    # realized correlation of simulated daily log returns, as a sanity check
    # against the input correlation assumption
    log_rets = np.diff(np.log(price_paths), axis=1)  # (sims, days-1, n_assets)
    flat = log_rets.reshape(-1, n_assets) if log_rets.shape[1] > 0 else np.zeros((1, n_assets))
    realized_corr = np.corrcoef(flat, rowvar=False) if flat.shape[0] > 1 else np.eye(n_assets)

    per_asset = []
    any_margin_call = np.zeros(price_paths.shape[0], dtype=bool)
    portfolio_has_margin_positions = False

    for i, a in enumerate(assets):
        prices_i = terminal_prices[:, i]
        result = {
            "ticker": a.ticker,
            "initial_price": a.initial_price,
            "mean_terminal_price": float(prices_i.mean()),
            "p05_terminal_price": float(np.percentile(prices_i, 5)),
            "p95_terminal_price": float(np.percentile(prices_i, 95)),
            "margin_call_probability": None,
        }
        if a.margin_pct is not None:
            portfolio_has_margin_positions = True
            path_i = price_paths[:, :, i]  # (sims, days)
            if a.position_units >= 0:
                threshold = a.initial_price * (1 - a.margin_pct)
                breached = np.any(path_i <= threshold, axis=1)
            else:
                threshold = a.initial_price * (1 + a.margin_pct)
                breached = np.any(path_i >= threshold, axis=1)
            result["margin_call_probability"] = float(breached.mean())
            any_margin_call = any_margin_call | breached
        per_asset.append(result)

    return {
        "initial_portfolio_value": initial_portfolio_value,
        "mean_terminal_pnl": float(terminal_pnl.mean()),
        "value_at_risk": value_at_risk,
        "conditional_value_at_risk": conditional_value_at_risk,
        "prob_of_loss": prob_of_loss,
        "portfolio_margin_call_probability": (
            float(any_margin_call.mean()) if portfolio_has_margin_positions else None
        ),
        "realized_correlation_matrix": realized_corr.tolist(),
        "per_asset": per_asset,
    }
