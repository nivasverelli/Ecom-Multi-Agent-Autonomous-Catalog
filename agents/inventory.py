"""Pricing & Inventory Agent - hybrid rules + LLM judgment.

Balances inventory velocity against Buy Box competitiveness. The deterministic
rules (below) produce a baseline and the feasible price menu; for ambiguous or
high-stakes SKUs an LLM confirms or overrides within that menu. Rules are the
fallback and the real-time path; the LLM is the strategic judgment layer.

Rule order (FIRST-MATCH-WINS):
  1. Critical Stock      Days_of_Supply < 14            -> RAISE_PRICE (+5%)
  2. Monopoly            Competitor_Count == 0 & winner -> RAISE_PRICE_SLIGHTLY (+2%)
  3. Overstocked&Losing  DoS > 45 & not winner & comp>0 -> DROP_PRICE (comp - $0.01)
  4. Default                                            -> HOLD_PRICE
"""

from typing import Any, Dict, List, Tuple

from agents.base import HybridAgent

# --- Intent labels (plain string constants) ---------------------------------
RAISE_PRICE = "RAISE_PRICE"
RAISE_PRICE_SLIGHTLY = "RAISE_PRICE_SLIGHTLY"
DROP_PRICE = "DROP_PRICE"
HOLD_PRICE = "HOLD_PRICE"

# --- Rule thresholds & magnitudes -------------------------------------------
CRITICAL_STOCK_DAYS = 14          # below this -> protect stock
OVERSTOCK_DAYS = 45               # above this -> liquidate
RAISE_PCT = 0.05                  # +5% on critical stock
RAISE_SLIGHTLY_PCT = 0.02         # +2% on a monopoly
UNDERCUT_DELTA = 0.01             # match lowest competitor minus $0.01

# --- Triage bands (when to spend an LLM call) -------------------------------
NEAR_CRITICAL_DAYS = 20           # 14..20 days = near-stockout judgment call
NEAR_OVERSTOCK_DAYS = 40          # 40..45 days = near-overstock judgment call
REVENUE_EXPOSURE_ESCALATE = 2000.0  # Current_Price * Historical_Velocity_7D

# --- Guardrails (hard safety bounds applied after the decision) -------------
MAX_PRICE_CHANGE_PCT = 0.10       # sanity band for RAISE/HOLD vs current price
MIN_PRICE = 0.01                  # never suggest a non-positive price
# NOTE: margin/COGS floor is intentionally NOT enforced here - the manager /
# constraint layer owns hard margin floors (DROP_PRICE stays pure per spec).


class PricingInventoryAgent(HybridAgent):
    """Hybrid pricing/inventory analyst (rules + bounded LLM judgment)."""

    name = "PricingInventoryAgent"

    SYSTEM_PROMPT = (
        "You are an Amazon Pricing & Inventory strategist. You DO NOT do any "
        "arithmetic - every number is pre-computed and given to you. A rule engine "
        "has already produced a baseline recommendation and a list of feasible "
        "options, each with an exact target_price. Weigh days of supply, Buy Box "
        "status, competitor pricing, data freshness and any flags, then CONFIRM the "
        "baseline or OVERRIDE it with a different feasible option. You may ONLY pick "
        "an option from feasible_options. Respond with ONLY a JSON object with keys: "
        "chosen_intent (one of the feasible intents), target_price (must equal that "
        "option's target_price), agree_with_rule (boolean), reasoning, confidence "
        "(0-1 number), primary_risk."
    )

    # --- rules -------------------------------------------------------------
    def _apply_rules(self, sku: Dict[str, Any]) -> Dict[str, Any]:
        dos = sku.get("Days_of_Supply")
        is_winner = bool(sku.get("Is_Buy_Box_Winner", False))
        price = sku.get("Current_Price")
        comp = sku.get("Lowest_Competitor_Price")
        comp_count = sku.get("Competitor_Count")

        if dos is not None and price is not None and dos < CRITICAL_STOCK_DAYS:
            return self._mk(sku, RAISE_PRICE, round(price * (1 + RAISE_PCT), 2),
                            f"Critical stock: {dos:.1f} days of supply (< "
                            f"{CRITICAL_STOCK_DAYS}); raise price 5% to prevent stockout.")

        if comp_count == 0 and is_winner and price is not None:
            return self._mk(sku, RAISE_PRICE_SLIGHTLY,
                            round(price * (1 + RAISE_SLIGHTLY_PCT), 2),
                            "Monopoly: no competitors and Buy Box winner; raise price "
                            "2% to maximize margin.")

        if (dos is not None and dos > OVERSTOCK_DAYS and not is_winner
                and comp is not None and comp > 0):
            return self._mk(sku, DROP_PRICE, round(comp - UNDERCUT_DELTA, 2),
                            f"Overstocked ({dos:.1f} days) and losing Buy Box; drop to "
                            f"${comp - UNDERCUT_DELTA:.2f} (lowest competitor - $0.01) "
                            f"to liquidate excess FBA inventory.")

        return self._mk(sku, HOLD_PRICE,
                        round(price, 2) if price is not None else None,
                        "No rule triggered; hold price.")

    def _mk(self, sku: Dict[str, Any], intent: str, suggested_price: Any,
            reason: str) -> Dict[str, Any]:
        return {
            "agent": self.name, "sku": sku.get("SKU"), "intent": intent,
            "current_price": sku.get("Current_Price"),
            "suggested_price": suggested_price, "reason": reason,
        }

    # --- feasible menu -----------------------------------------------------
    def _feasible_options(self, sku: Dict[str, Any]) -> List[Dict[str, Any]]:
        price = sku.get("Current_Price")
        comp = sku.get("Lowest_Competitor_Price")
        opts: List[Dict[str, Any]] = []
        if price is not None:
            opts += [
                {"intent": RAISE_PRICE, "target_price": round(price * (1 + RAISE_PCT), 2)},
                {"intent": RAISE_PRICE_SLIGHTLY,
                 "target_price": round(price * (1 + RAISE_SLIGHTLY_PCT), 2)},
                {"intent": HOLD_PRICE, "target_price": round(price, 2)},
            ]
        if comp is not None and comp > 0:
            opts.append({"intent": DROP_PRICE,
                         "target_price": round(comp - UNDERCUT_DELTA, 2)})
        return opts

    # --- triage ------------------------------------------------------------
    def _should_escalate(self, sku: Dict[str, Any],
                         rule: Dict[str, Any]) -> Tuple[bool, str]:
        dos = sku.get("Days_of_Supply")
        is_winner = bool(sku.get("Is_Buy_Box_Winner", False))
        price = sku.get("Current_Price")
        comp = sku.get("Lowest_Competitor_Price")
        velocity = sku.get("Historical_Velocity_7D")
        reasons: List[str] = []

        if dos is not None and CRITICAL_STOCK_DAYS <= dos < NEAR_CRITICAL_DAYS:
            reasons.append(f"near-stockout (DoS={dos:.1f})")
        if dos is not None and NEAR_OVERSTOCK_DAYS < dos <= OVERSTOCK_DAYS:
            reasons.append(f"near-overstock (DoS={dos:.1f})")
        if (not is_winner and price is not None and comp is not None and comp > 0
                and price > comp and dos is not None
                and CRITICAL_STOCK_DAYS <= dos <= OVERSTOCK_DAYS):
            reasons.append("losing Buy Box above competitor (undercut judgment)")
        if price is not None and velocity is not None \
                and price * velocity >= REVENUE_EXPOSURE_ESCALATE:
            reasons.append("high revenue exposure")

        return (bool(reasons), "; ".join(reasons))

    # --- LLM result --------------------------------------------------------
    def _from_llm(self, sku: Dict[str, Any], data: Dict[str, Any],
                  feasible: List[Dict[str, Any]],
                  rule: Dict[str, Any]) -> Dict[str, Any]:
        intent = str(data.get("chosen_intent", rule["intent"]))
        match = next((o for o in feasible if o["intent"] == intent), None)
        if match is None:  # LLM picked an infeasible intent -> rule baseline wins
            out = dict(rule)
            out["llm_note"] = f"LLM intent '{intent}' not feasible; kept rule baseline"
            return out
        return {
            "agent": self.name, "sku": sku.get("SKU"), "intent": intent,
            "current_price": sku.get("Current_Price"),
            "suggested_price": match["target_price"],
            "reason": str(data.get("reasoning", rule["reason"])),
            "confidence": data.get("confidence"),
            "primary_risk": str(data.get("primary_risk", "")),
            "agree_with_rule": data.get("agree_with_rule"),
        }

    # --- guardrails --------------------------------------------------------
    def _apply_guardrails(self, sku: Dict[str, Any],
                          decision: Dict[str, Any]) -> None:
        price = sku.get("Current_Price")
        sp = decision.get("suggested_price")
        if sp is None:
            return
        # DROP_PRICE intentionally undercuts (can exceed the band) - only floor it.
        if decision.get("intent") == DROP_PRICE:
            if sp < MIN_PRICE:
                decision["suggested_price"] = MIN_PRICE
                decision["guardrail_note"] = "floored to MIN_PRICE"
            return
        if price is None:
            return
        hi = round(price * (1 + MAX_PRICE_CHANGE_PCT), 2)
        lo = max(MIN_PRICE, round(price * (1 - MAX_PRICE_CHANGE_PCT), 2))
        clamped = min(max(sp, lo), hi)
        if clamped != sp:
            decision["suggested_price"] = clamped
            decision["guardrail_note"] = (
                f"price clamped to +/-{MAX_PRICE_CHANGE_PCT:.0%} band "
                f"(${sp:.2f}->${clamped:.2f})")


if __name__ == "__main__":
    import json

    agent = PricingInventoryAgent()
    mock_skus = [
        {  # clear-cut critical stock -> rules only, no LLM
            "SKU": "SKU-CRIT", "Days_of_Supply": 6.0, "Is_Buy_Box_Winner": True,
            "Current_Price": 20.00, "Lowest_Competitor_Price": 19.50,
            "Competitor_Count": 4, "Historical_Velocity_7D": 10,
        },
        {  # gray-zone: losing box, above competitor, mid stock -> escalates to LLM
            "SKU": "SKU-GRAY", "Days_of_Supply": 30.0, "Is_Buy_Box_Winner": False,
            "Current_Price": 64.00, "Lowest_Competitor_Price": 61.00,
            "Competitor_Count": 6, "Historical_Velocity_7D": 40,
        },
        {  # default hold, low stakes -> rules only
            "SKU": "SKU-HOLD", "Days_of_Supply": 25.0, "Is_Buy_Box_Winner": True,
            "Current_Price": 15.00, "Lowest_Competitor_Price": 14.80,
            "Competitor_Count": 5, "Historical_Velocity_7D": 8,
        },
    ]
    for s in mock_skus:
        print(json.dumps(agent.analyze(s), indent=2, default=str))
