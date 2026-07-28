"""SECTION 7 - Simulator / environment (the deterministic feedback loop).

IMPORTANT (determinism): importing `config` first guarantees random.seed(42) has
run before the scheduled-event sampling below, so the event days are stable
regardless of module import order.
"""

import random
from typing import Any, Dict

import config  # noqa: F401  (imported for its seeding side effect; must precede sample)
from models.context import SharedContext
from models.recommendations import ManagerDecision
from models.results import DailySimulationResult

# Two scheduled deterministic events, chosen once via the seeded RNG.
_EVENT_DAYS = random.sample(range(2, 8), 2)  # two distinct days in [2, 7]
COMPETITOR_DROP_DAY = _EVENT_DAYS[0]
WEEKEND_SPIKE_DAY = _EVENT_DAYS[1]


def scheduled_events(day: int) -> Dict[str, Any]:
    """Return the deterministic event modifiers for a given simulation day.

    Two events only (per spec): a competitor price drop (we lose the buy box,
    demand softens) and a weekend demand spike (demand jumps).
    """
    if day == COMPETITOR_DROP_DAY:
        return {"event_multiplier": 0.70, "buy_box_lost": True,
                "flags": ["COMPETITOR_PRICE_DROP"]}
    if day == WEEKEND_SPIKE_DAY:
        return {"event_multiplier": 1.50, "buy_box_lost": False,
                "flags": ["WEEKEND_DEMAND_SPIKE"]}
    # EXTENSION POINT: seasonality, inventory aging, supplier delay, ...
    return {"event_multiplier": 1.00, "buy_box_lost": False, "flags": []}


def simulate_market(context: SharedContext, decision: ManagerDecision,
                    event_multiplier: float) -> DailySimulationResult:
    """Advance the market one day. Strict old_*/new_* ordering: no value is
    overwritten before every formula that needs its old value has run."""
    # 1. capture pre-decision state
    old_price = context.price
    old_spend = context.ad_spend
    old_velocity = context.velocity
    old_qty = context.qty
    old_acos = context.acos

    # 2. apply the decision
    new_price = decision.final_price
    new_spend = decision.final_ad_spend

    # 3. event modifier already resolved by caller (event_multiplier)

    # 4. new velocity (guard new_price > 0). Demand rises as price falls and as
    #    spend rises above the $120 baseline, scaled by the event multiplier.
    if new_price > 0:
        raw_velocity = old_velocity * (24.99 / new_price) * (1 + ((new_spend - 120) / 120)) * event_multiplier
    else:
        raw_velocity = 0
    new_velocity = max(5, int(raw_velocity))

    # 5. new quantity on hand
    new_qty = max(0, old_qty - new_velocity)

    # 6. new ACoS (guard denominator > 0)
    denom = new_velocity * new_price
    new_acos = (new_spend / denom) if denom > 0 else 0.0

    # 7. stockout handling
    stocked_out = new_qty == 0
    sold_units = new_velocity if not stocked_out else min(new_velocity, old_qty)
    if stocked_out:
        print(f"  *** STOCKOUT WARNING: quantity hit 0 on day {context.day} "
              f"(date {context.current_date}). Sales contribution capped. ***")

    # derived post-decision metrics
    new_days_of_supply = (new_qty / new_velocity) if new_velocity > 0 else 999.0
    ad_per_unit = (new_spend / new_velocity) if new_velocity > 0 else 0.0
    contribution_per_unit = new_price - ad_per_unit - context.cogs
    # absolute daily profit = units sold * unit margin - ad spend
    daily_profit = sold_units * (new_price - context.cogs) - new_spend

    return DailySimulationResult(
        day=context.day,
        date=context.current_date,
        old_price=round(old_price, 2),
        new_price=round(new_price, 2),
        old_spend=round(old_spend, 2),
        new_spend=round(new_spend, 2),
        old_velocity=old_velocity,
        new_velocity=new_velocity,
        old_qty=old_qty,
        new_qty=new_qty,
        old_acos=round(old_acos, 4),
        new_acos=round(new_acos, 4),
        days_of_supply=round(new_days_of_supply, 2),
        contribution_per_unit=round(contribution_per_unit, 2),
        daily_profit=round(daily_profit, 2),
        event_multiplier=event_multiplier,
        stocked_out=stocked_out,
    )
