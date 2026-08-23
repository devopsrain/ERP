"""
Pydantic schemas for the correlated-price Monte Carlo risk simulator.

Kept deliberately simple: this service simulates *risk* (dispersion of
outcomes, tail losses, margin-call probability) given user-supplied
volatility/correlation assumptions. It does not forecast returns and
`annual_drift` defaults to 0.0 (risk-neutral) unless the caller overrides it,
so the tool can't be mistaken for a return-prediction / advice engine.
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class AssetInput(BaseModel):
    ticker: str = Field(..., min_length=1, max_length=16)
    initial_price: float = Field(..., gt=0, description="Current price per unit")
    annual_volatility: float = Field(..., gt=0, le=5.0, description="Annualized sigma, e.g. 0.65 = 65%")
    annual_drift: float = Field(0.0, ge=-2.0, le=2.0, description="Annualized mu; defaults to 0 (risk-neutral)")
    position_units: float = Field(..., description="Units held. Positive = long, negative = short")
    margin_pct: Optional[float] = Field(
        None, gt=0, le=1.0,
        description="Margin requirement as a fraction (0.20 = 20% margin / 5x leverage). "
                    "If set, the engine reports the probability price breaches the margin-call threshold."
    )


class SimulationRequest(BaseModel):
    assets: List[AssetInput] = Field(..., min_length=1, max_length=25)
    correlation_matrix: List[List[float]] = Field(
        ..., description="Symmetric NxN correlation matrix matching the order of `assets`"
    )
    num_simulations: int = Field(10_000, ge=100, le=200_000)
    horizon_days: int = Field(20, ge=1, le=252)
    confidence_level: float = Field(0.95, gt=0.5, lt=1.0)
    random_seed: Optional[int] = Field(None, description="Set for reproducible runs")

    @field_validator("correlation_matrix")
    @classmethod
    def _matrix_is_square(cls, v: List[List[float]]) -> List[List[float]]:
        n = len(v)
        if n == 0 or any(len(row) != n for row in v):
            raise ValueError("correlation_matrix must be a square NxN matrix")
        return v

    @model_validator(mode="after")
    def _matrix_matches_assets(self) -> "SimulationRequest":
        n_assets = len(self.assets)
        n_matrix = len(self.correlation_matrix)
        if n_assets != n_matrix:
            raise ValueError(
                f"correlation_matrix is {n_matrix}x{n_matrix} but {n_assets} assets were supplied; "
                "these must match 1:1 in the same order"
            )
        # cheap resource guard: cap total simulated data points on a bare-metal box
        total_points = self.num_simulations * self.horizon_days * n_assets
        if total_points > 60_000_000:
            raise ValueError(
                f"num_simulations * horizon_days * n_assets = {total_points:,} exceeds the safety "
                "cap of 60,000,000 points. Reduce simulations, horizon, or asset count."
            )
        return self


class AssetResult(BaseModel):
    ticker: str
    initial_price: float
    mean_terminal_price: float
    p05_terminal_price: float
    p95_terminal_price: float
    margin_call_probability: Optional[float] = None


class SimulationResponse(BaseModel):
    num_simulations: int
    horizon_days: int
    confidence_level: float
    initial_portfolio_value: float
    mean_terminal_pnl: float
    value_at_risk: float = Field(..., description="Loss at the confidence level, positive = loss amount")
    conditional_value_at_risk: float = Field(..., description="Average loss beyond VaR (expected shortfall)")
    prob_of_loss: float
    portfolio_margin_call_probability: Optional[float] = None
    realized_correlation_matrix: List[List[float]]
    per_asset: List[AssetResult]
