"""The subagent registry the orchestrator iterates.

Adding a new rule+LLM subagent later means appending one line here - nothing
else. Each entry exposes `analyze(sku) -> dict`.
"""

from typing import List

from agents.base import HybridAgent
from agents.inventory import PricingInventoryAgent
from agents.advertising import AdvertisingAgent

SUBAGENT_REGISTRY: List[HybridAgent] = [
    PricingInventoryAgent(),
    AdvertisingAgent(),
]
