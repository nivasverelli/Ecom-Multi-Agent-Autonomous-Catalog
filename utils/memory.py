"""Manager memory + lightweight policy learning (contextual bandit), JSON-backed.

This is the manager's brain across runs. There is no live data: the manager
learns by comparing the decisions it saved LAST run against the actual numbers in
the NEXT CSV the user supplies. The reward is a profit-with-guardrails blend.

Memory layout (manager_memory.json):
{
  "version": 1,
  "runs": [ {run_id, date, n_skus, n_changed, n_flagged, total_reward} ],
  "last_decisions": {              # snapshot used to grade the next run
      "<SKU>": {date, category, price_bucket, ad_bucket,
                price_intent, old_price, new_price,
                ad_intent, bid_change_pct,
                pre: {price, cogs, fba_fee, referral_pct, velocity, ad_spend}}
  },
  "policy": {                      # learned value table (the bandit)
      "<bucket_key>": {"<action>": {count, total_reward, avg_reward}}
  },
  "review_queue": [ ... ]          # items still awaiting a human
}
"""

import json
import os
from typing import Any, Dict, List, Tuple

MEMORY_PATH = "manager_memory.json"

# Reward shaping ------------------------------------------------------------
STOCKOUT_FLOOR_DAYS = 14          # below this, stockout penalty applies
ACOS_CEILING = 0.35               # above this, an ACOS penalty applies
STOCKOUT_PENALTY = 50.0           # $ penalty for landing below the supply floor
ACOS_PENALTY = 25.0               # $ penalty for landing above the ACOS ceiling

# Policy confidence ---------------------------------------------------------
MIN_SAMPLES_TRUSTED = 3           # a bucket/action needs this many tries to be trusted
POLICY_NEG_THRESHOLD = -1.0       # avg reward below this = "this move tends to hurt"


def _empty_memory() -> Dict[str, Any]:
    return {"version": 1, "runs": [], "last_decisions": {},
            "policy": {}, "review_queue": []}


def load_memory(path: str = MEMORY_PATH) -> Dict[str, Any]:
    """Load memory JSON, or return a fresh empty structure on first run."""
    if not os.path.exists(path):
        return _empty_memory()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            mem = json.load(fh)
        # forward-compatible defaults
        for k, v in _empty_memory().items():
            mem.setdefault(k, v)
        return mem
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  [WARN] could not read {path} ({exc}); starting with fresh memory.")
        return _empty_memory()


def save_memory(mem: Dict[str, Any], path: str = MEMORY_PATH) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(mem, fh, indent=2, default=str)


# --- state bucketing -------------------------------------------------------
def price_state(sku: Dict[str, Any]) -> str:
    """Supply pressure + buy-box status -> a coarse pricing situation label."""
    dos = sku.get("Days_of_Supply")
    winner = bool(sku.get("Is_Buy_Box_Winner", False))
    if dos is None:
        supply = "unknown"
    elif dos < STOCKOUT_FLOOR_DAYS:
        supply = "critical"
    elif dos > 45:
        supply = "overstock"
    else:
        supply = "healthy"
    box = "winning_box" if winner else "losing_box"
    return f"{supply}|{box}"


def ad_state(sku: Dict[str, Any]) -> str:
    """ACOS band -> a coarse advertising situation label."""
    acos = sku.get("ACOS")
    orders = sku.get("Ad_Orders_7D")
    if acos is None or (orders == 0):
        return "no_conversion"
    if acos > ACOS_CEILING:
        return "high_acos"
    if 0.0 < acos < 0.15:
        return "low_acos"
    return "mid_acos"


def price_bucket_key(sku: Dict[str, Any]) -> str:
    return f"{sku.get('Category', 'NA')}|{price_state(sku)}"


def ad_bucket_key(sku: Dict[str, Any]) -> str:
    return f"{sku.get('Category', 'NA')}|{ad_state(sku)}"


# --- reward ----------------------------------------------------------------
def profit_proxy(price: float, cogs: float, fba_fee: float, referral_pct: float,
                 velocity: float, ad_spend: float) -> float:
    """Daily-ish contribution proxy: per-unit margin x velocity, minus ad spend.

    Absolute scale does not matter for learning - only the run-over-run delta.
    """
    if price is None:
        return 0.0
    referral = price * (referral_pct or 0.0)
    per_unit = price - (cogs or 0.0) - (fba_fee or 0.0) - referral
    return round(per_unit * (velocity or 0.0) - (ad_spend or 0.0), 2)


def compute_reward(pre: Dict[str, Any], now: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Reward = Δprofit (now vs the pre-decision snapshot) minus guardrail penalties
    observed in the new state. Returns (reward, human-readable notes)."""
    notes: List[str] = []
    profit_before = profit_proxy(pre.get("price"), pre.get("cogs"), pre.get("fba_fee"),
                                 pre.get("referral_pct"), pre.get("velocity"),
                                 pre.get("ad_spend"))
    profit_after = profit_proxy(now.get("Current_Price"), now.get("Unit_COGS"),
                                now.get("FBA_Fee"), now.get("Amazon_Referral_Fee_%"),
                                now.get("Historical_Velocity_7D"), now.get("Ad_Spend_7D"))
    reward = profit_after - profit_before
    notes.append(f"Δprofit {profit_before:.2f}->{profit_after:.2f} = {reward:+.2f}")

    dos = now.get("Days_of_Supply")
    if dos is not None and dos < STOCKOUT_FLOOR_DAYS:
        reward -= STOCKOUT_PENALTY
        notes.append(f"stockout penalty -{STOCKOUT_PENALTY:.0f} (DoS={dos:.1f})")
    acos = now.get("ACOS")
    if acos is not None and acos > ACOS_CEILING:
        reward -= ACOS_PENALTY
        notes.append(f"ACOS penalty -{ACOS_PENALTY:.0f} (ACOS={acos:.2%})")
    return round(reward, 2), notes


# --- policy (the bandit value table) ---------------------------------------
def policy_value(mem: Dict[str, Any], bucket_key: str,
                 action: str) -> Tuple[float, int]:
    """Return (avg_reward, count) for a bucket/action, default (0.0, 0)."""
    entry = mem["policy"].get(bucket_key, {}).get(action)
    if not entry:
        return (0.0, 0)
    return (entry.get("avg_reward", 0.0), entry.get("count", 0))


def update_policy(mem: Dict[str, Any], bucket_key: str, action: str,
                  reward: float) -> None:
    """Incremental running-average update of a bucket/action's value."""
    bucket = mem["policy"].setdefault(bucket_key, {})
    entry = bucket.setdefault(action, {"count": 0, "total_reward": 0.0, "avg_reward": 0.0})
    entry["count"] += 1
    entry["total_reward"] = round(entry["total_reward"] + reward, 2)
    entry["avg_reward"] = round(entry["total_reward"] / entry["count"], 2)


def policy_is_negative(mem: Dict[str, Any], bucket_key: str, action: str) -> bool:
    """True only if we have enough samples AND the action's track record is bad."""
    avg, count = policy_value(mem, bucket_key, action)
    return count >= MIN_SAMPLES_TRUSTED and avg < POLICY_NEG_THRESHOLD


def make_last_decision_entry(decision: Dict[str, Any], row: Dict[str, Any],
                             run_date: str) -> Dict[str, Any]:
    """Build the memory snapshot used to grade this decision on the next run.

    `row` is the pre-decision SKU data (today's CSV). Shared by the batch runner
    and the UI's approve action so both record decisions identically.
    """
    return {
        "date": run_date,
        "category": decision["category"],
        "price_bucket": decision["price_bucket"],
        "ad_bucket": decision["ad_bucket"],
        "price_intent": decision["price_intent"],
        "old_price": decision["old_price"],
        "new_price": decision["new_price"],
        "ad_intent": decision["ad_intent"],
        "bid_change_pct": decision["bid_change_pct"],
        "pre": {
            "price": decision["old_price"],
            "cogs": row.get("Unit_COGS"),
            "fba_fee": row.get("FBA_Fee"),
            "referral_pct": row.get("Amazon_Referral_Fee_%"),
            "velocity": row.get("Historical_Velocity_7D"),
            "ad_spend": row.get("Ad_Spend_7D"),
        },
    }
