"""Business objectives priority and operational (hard) constraints."""

from dataclasses import dataclass, field
from typing import List

from models.enums import Objective


@dataclass
class BusinessObjectives:
    """Static priority ordering of business objectives (most critical first)."""
    priority: List[Objective] = field(default_factory=lambda: [
        Objective.AVOID_STOCKOUT,
        Objective.PRESERVE_ORGANIC_RANK,
        Objective.MAXIMIZE_PROFIT,
    ])


@dataclass
class OperationalConstraints:
    """Hard constraints. Python enforces these; the LLM never overrides them."""
    min_margin: float = 5.00
    stockout_floor_days: int = 15
    max_price_change_pct: float = 0.05
    max_spend_change_pct: float = 0.20
    min_ad_spend: float = 50.00
    max_ad_spend: float = 300.00
