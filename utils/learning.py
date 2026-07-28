"""SECTION 6 - Learning: the lightweight, deterministic confidence ledger.

One-sentence rule: if the most recent action of a given type moved its target
metric in the intended direction, nudge that action's confidence up; otherwise
nudge it down.
"""

from typing import Dict, List

from models.enums import PriceAction, AdAction
from models.context import SharedContext
from models.results import DailySimulationResult

CONFIDENCE_STEP = 0.05
LEDGER_DEFAULT = 0.50


def _nudge(ledger: Dict[str, float], action: str, success: bool) -> None:
    current = ledger.get(action, LEDGER_DEFAULT)
    delta = CONFIDENCE_STEP if success else -CONFIDENCE_STEP
    ledger[action] = round(max(0.0, min(1.0, current + delta)), 2)


def update_confidence_ledger(context: SharedContext,
                             result: DailySimulationResult) -> List[str]:
    """One-sentence rule: if the most recent action of a given type moved its
    target metric in the intended direction, nudge that action's confidence up;
    otherwise nudge it down. Returns human-readable notes for logging."""
    ledger = context.confidence_ledger
    notes: List[str] = []
    floor = context.constraints.stockout_floor_days

    # Determine which price action actually happened, and whether it worked.
    if result.new_price > result.old_price:        # RAISE_PRICE
        # intended to protect stock above the floor
        success = result.days_of_supply >= floor
        _nudge(ledger, PriceAction.RAISE_PRICE.value, success)
        notes.append(f"RAISE_PRICE {'kept' if success else 'failed to keep'} supply >= floor "
                     f"-> conf {ledger[PriceAction.RAISE_PRICE.value]:.2f}")
    elif result.new_price < result.old_price:      # LOWER_PRICE
        # intended to increase velocity
        success = result.new_velocity > result.old_velocity
        _nudge(ledger, PriceAction.LOWER_PRICE.value, success)
        notes.append(f"LOWER_PRICE {'raised' if success else 'did not raise'} velocity "
                     f"-> conf {ledger[PriceAction.LOWER_PRICE.value]:.2f}")

    # Determine which ad action actually happened, and whether it worked.
    if result.new_spend > result.old_spend:        # INCREASE_BUDGET
        # intended to lower ACoS (more efficient traffic)
        success = result.new_acos < result.old_acos
        _nudge(ledger, AdAction.INCREASE_BUDGET.value, success)
        notes.append(f"INCREASE_BUDGET {'lowered' if success else 'did not lower'} ACoS "
                     f"-> conf {ledger[AdAction.INCREASE_BUDGET.value]:.2f}")
    elif result.new_spend < result.old_spend:      # DECREASE_BUDGET
        # intended to lower ACoS by trimming wasteful spend
        success = result.new_acos < result.old_acos
        _nudge(ledger, AdAction.DECREASE_BUDGET.value, success)
        notes.append(f"DECREASE_BUDGET {'lowered' if success else 'did not lower'} ACoS "
                     f"-> conf {ledger[AdAction.DECREASE_BUDGET.value]:.2f}")

    if not notes:
        notes.append("Both levers HELD - no confidence ledger change.")
    return notes
