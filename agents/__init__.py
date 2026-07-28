"""Hybrid rule+LLM subagents and (legacy) manager/orchestrator decision logic.

NOTE: the manager below still uses the previous specialist contract and is being
rewired in a later step; the subagents now expose `analyze(sku) -> dict`.
"""

from agents.base import HybridAgent
from agents.inventory import PricingInventoryAgent
from agents.advertising import AdvertisingAgent
from agents.registry import SUBAGENT_REGISTRY

__all__ = [
    "HybridAgent",
    "PricingInventoryAgent",
    "AdvertisingAgent",
    "SUBAGENT_REGISTRY",
]
