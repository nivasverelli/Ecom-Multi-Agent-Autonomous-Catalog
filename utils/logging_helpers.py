"""Pretty per-day explainability logging helpers (Section 8)."""

from typing import Any, Dict, List

from models.recommendations import Recommendation


def _hr(char: str = "=") -> str:
    return char * 78


def log_day_header(day: int, current_date: str, event_flags: List[str]) -> None:
    print("\n" + _hr("="))
    flags = ", ".join(event_flags) if event_flags else "none"
    print(f" DAY {day}  |  {current_date}  |  events: {flags}")
    print(_hr("="))


def log_context(view: Dict[str, Any]) -> None:
    wm = view["working_memory"]
    d = wm["derived"]
    print("[CONTEXT]")
    print(f"  price=${wm['price']:.2f}  cogs=${wm['cogs']:.2f}  qty={wm['qty']}  "
          f"velocity={wm['velocity']}/day  ad_spend=${wm['ad_spend']:.2f}  acos={wm['acos']:.2%}")
    print(f"  days_of_supply={d['days_of_supply']}  gross_margin=${d['gross_margin']:.2f}  "
          f"contribution/unit=${d['contribution_per_unit']:.2f}  storage_pressure={d['storage_fee_pressure']}")
    print(f"  buy_box_lost={wm['buy_box_lost_to_competitor']}  top_objective={view['objectives_priority'][0]}")


def log_recommendation(rec: Recommendation) -> None:
    tag = " (fallback)" if rec.used_fallback else ""
    print(f"[{rec.source} REC]{tag}  action={rec.action}  target={rec.target_value:.2f}  "
          f"confidence={rec.confidence:.2f}")
    print(f"    risk: {rec.primary_risk}")
    print(f"    reasoning: {rec.reasoning}")
