"""SharedContext - the single source of truth (the org's memory)."""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List

from models.business import BusinessObjectives, OperationalConstraints
from models.recommendations import ManagerDecision
from models.results import DailySimulationResult


@dataclass
class SharedContext:
    """Single source of truth. Two layers:

    Working memory  - what TODAY's decision needs (current metrics + derived +
                      flags + objectives + constraints + sim metadata).
    Persistent      - retained across days (decision/outcome history, lessons,
                      and the confidence ledger).

    Read/write discipline: specialists receive a READ-ONLY view (see
    read_only_view); ONLY the orchestrator commits updates back here.
    """
    # --- working memory: operational metrics ---
    price: float
    cogs: float
    qty: int
    velocity: int
    ad_spend: float
    acos: float
    buy_box_lost_to_competitor: bool = False

    # --- working memory: derived (computed in Python every cycle) ---
    derived: Dict[str, Any] = field(default_factory=dict)
    event_flags: List[str] = field(default_factory=list)

    # --- working memory: configuration ---
    objectives: BusinessObjectives = field(default_factory=BusinessObjectives)
    constraints: OperationalConstraints = field(default_factory=OperationalConstraints)

    # --- working memory: simulation metadata ---
    day: int = 1
    current_date: str = ""

    # --- persistent memory (across days) ---
    decision_history: List[ManagerDecision] = field(default_factory=list)
    outcome_history: List[DailySimulationResult] = field(default_factory=list)
    lessons_learned: List[str] = field(default_factory=list)
    # confidence_ledger: action_type -> running track-record confidence (0-1)
    confidence_ledger: Dict[str, float] = field(default_factory=dict)

    def compute_derived(self) -> None:
        """Pre-compute all derived metrics in Python BEFORE any agent runs.

        Every division is guarded. The LLM consumes these numbers; it never
        recomputes them.
        """
        # days_of_supply: guard velocity 0 -> effectively infinite supply.
        days_of_supply = (self.qty / self.velocity) if self.velocity > 0 else 999.0
        gross_margin = self.price - self.cogs
        # contribution per unit; guard velocity before dividing ad spend.
        ad_cost_per_unit = (self.ad_spend / self.velocity) if self.velocity > 0 else 0.0
        contribution_per_unit = self.price - self.cogs - ad_cost_per_unit
        # storage fee pressure: simple deterministic signal tied to the
        # inventory/discount narrative.
        storage_fee_pressure = "HIGH" if days_of_supply > 60 else "LOW"

        self.derived = {
            "days_of_supply": round(days_of_supply, 2),
            "gross_margin": round(gross_margin, 2),
            "contribution_per_unit": round(contribution_per_unit, 2),
            "ad_cost_per_unit": round(ad_cost_per_unit, 2),
            "storage_fee_pressure": storage_fee_pressure,
        }

    def recent(self, n: int = 3) -> Dict[str, List[Dict[str, Any]]]:
        """Last n days of persistent memory, serialised for prompts/views."""
        return {
            "recent_decisions": [asdict(d) for d in self.decision_history[-n:]],
            "recent_outcomes": [asdict(o) for o in self.outcome_history[-n:]],
            "recent_lessons": self.lessons_learned[-n:],
        }

    def read_only_view(self, n: int = 3) -> Dict[str, Any]:
        """The exact, immutable snapshot every specialist/manager reads.

        Returns plain dicts so callers literally cannot mutate the context.
        """
        return {
            "day": self.day,
            "date": self.current_date,
            "working_memory": {
                "price": self.price,
                "cogs": self.cogs,
                "qty": self.qty,
                "velocity": self.velocity,
                "ad_spend": self.ad_spend,
                "acos": round(self.acos, 4),
                "buy_box_lost_to_competitor": self.buy_box_lost_to_competitor,
                "derived": dict(self.derived),
                "event_flags": list(self.event_flags),
            },
            "objectives_priority": [o.value for o in self.objectives.priority],
            "constraints": asdict(self.constraints),
            "confidence_ledger": dict(self.confidence_ledger),
            "persistent_memory_last_3_days": self.recent(n),
        }
