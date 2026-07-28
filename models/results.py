"""Measured per-day simulation outcome model."""

from dataclasses import dataclass


@dataclass
class DailySimulationResult:
    """Measured outcome of one simulated day."""
    day: int
    date: str
    old_price: float
    new_price: float
    old_spend: float
    new_spend: float
    old_velocity: int
    new_velocity: int
    old_qty: int
    new_qty: int
    old_acos: float
    new_acos: float
    days_of_supply: float
    contribution_per_unit: float
    daily_profit: float
    event_multiplier: float
    stocked_out: bool
