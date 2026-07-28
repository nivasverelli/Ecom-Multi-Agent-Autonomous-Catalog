"""Typed recommendation and decision models (specialist + manager outputs)."""

from dataclasses import dataclass


@dataclass
class Recommendation:
    """Base recommendation. All specialist outputs parse into a subclass."""
    action: str
    target_value: float
    reasoning: str
    confidence: float            # 0-1
    expected_impact: str
    primary_risk: str
    assumptions: str
    source: str = "unknown"      # which specialist produced it
    used_fallback: bool = False  # True if produced deterministically


@dataclass
class InventoryRecommendation(Recommendation):
    pass


@dataclass
class AdvertisingRecommendation(Recommendation):
    pass


@dataclass
class ManagerDecision:
    final_price: float
    final_ad_spend: float
    chosen_priority_objective: str
    rejected_recommendation_reason: str
    trade_off_accepted: str
    lesson_learned: str
    resolution_notes: str
    used_fallback: bool = False
