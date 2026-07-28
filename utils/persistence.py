"""SECTION 9 - Persistence: the CSV audit trail."""

import csv
import json
import os
from typing import Optional

from models.context import SharedContext
from models.recommendations import Recommendation, ManagerDecision
from models.results import DailySimulationResult

CSV_PATH = "amazon_daily_metrics.csv"
CSV_FIELDS = [
    "day", "date",
    "old_price", "final_price", "old_spend", "final_ad_spend",
    "velocity", "qty", "acos", "days_of_supply",
    "gross_margin", "contribution_per_unit", "storage_fee_pressure",
    "daily_profit", "stocked_out",
    "chosen_priority_objective",
    "inventory_action", "inventory_confidence",
    "advertising_action", "advertising_confidence",
    "conflict", "rejected_recommendation_reason", "trade_off_accepted",
    "lesson_learned", "resolution_notes",
    "event_flags", "confidence_ledger",
]


def append_csv_row(context: SharedContext, inv: Recommendation, adv: Recommendation,
                   decision: ManagerDecision, result: DailySimulationResult,
                   conflict: Optional[str]) -> None:
    """Write the header exactly once; append one row per day. List/object fields
    are JSON-serialised."""
    write_header = not os.path.exists(CSV_PATH)
    # utf-8-sig writes a BOM so the audit trail opens correctly in Excel even
    # when the manager's reasoning contains Unicode (arrows, dashes, ...).
    # Fail loud, continue safe: if the file is locked (e.g. open in Excel) we
    # warn and skip this row rather than crashing the simulation.
    try:
        fh = open(CSV_PATH, "a", newline="", encoding="utf-8-sig")
    except OSError as exc:
        print(f"  [WARN] could not write CSV row (day {result.day}): {exc}. "
              "Close the file if it is open elsewhere; continuing run.")
        return
    with fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerow({
            "day": result.day,
            "date": result.date,
            "old_price": result.old_price,
            "final_price": result.new_price,
            "old_spend": result.old_spend,
            "final_ad_spend": result.new_spend,
            "velocity": result.new_velocity,
            "qty": result.new_qty,
            "acos": result.new_acos,
            "days_of_supply": result.days_of_supply,
            "gross_margin": round(result.new_price - context.cogs, 2),
            "contribution_per_unit": result.contribution_per_unit,
            "storage_fee_pressure": context.derived.get("storage_fee_pressure", ""),
            "daily_profit": result.daily_profit,
            "stocked_out": result.stocked_out,
            "chosen_priority_objective": decision.chosen_priority_objective,
            "inventory_action": inv.action,
            "inventory_confidence": inv.confidence,
            "advertising_action": adv.action,
            "advertising_confidence": adv.confidence,
            "conflict": conflict or "none",
            "rejected_recommendation_reason": decision.rejected_recommendation_reason,
            "trade_off_accepted": decision.trade_off_accepted,
            "lesson_learned": decision.lesson_learned,
            "resolution_notes": decision.resolution_notes,
            "event_flags": json.dumps(context.event_flags),
            "confidence_ledger": json.dumps(context.confidence_ledger),
        })
