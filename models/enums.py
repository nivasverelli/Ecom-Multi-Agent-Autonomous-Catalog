"""Enumerations: action types, business objectives, and execution states."""

from enum import Enum


class PriceAction(Enum):
    RAISE_PRICE = "RAISE_PRICE"
    LOWER_PRICE = "LOWER_PRICE"
    HOLD = "HOLD"


class AdAction(Enum):
    INCREASE_BUDGET = "INCREASE_BUDGET"
    DECREASE_BUDGET = "DECREASE_BUDGET"
    HOLD = "HOLD"


class Objective(Enum):
    AVOID_STOCKOUT = "avoid_stockout"
    PRESERVE_ORGANIC_RANK = "preserve_organic_rank"
    MAXIMIZE_PROFIT = "maximize_profit"


# SECTION 10 - the explicit, self-documenting states of one decision cycle.
class State(Enum):
    OBSERVE = "OBSERVE"
    PRE_COMPUTE = "PRE_COMPUTE"
    SPECIALIST_RECOMMENDATIONS = "SPECIALIST_RECOMMENDATIONS"
    CONFLICT_RESOLUTION = "CONFLICT_RESOLUTION"
    EXECUTE = "EXECUTE"
    MEASURE = "MEASURE"
    LEARN = "LEARN"
    COMMIT_CONTEXT = "COMMIT_CONTEXT"
    NEXT_CYCLE = "NEXT_CYCLE"
