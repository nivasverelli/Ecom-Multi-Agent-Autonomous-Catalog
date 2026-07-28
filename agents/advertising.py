"""Advertising Agent - hybrid rules + LLM judgment.

Maximizes ad profitability and stops wasted spend. The deterministic rules
produce a baseline and the feasible bid menu; for ambiguous or high-spend SKUs
an LLM confirms or overrides within that menu. Rules are the fallback and the
real-time path; the LLM is the strategic judgment layer.

Rule order (FIRST-MATCH-WINS; Click Drain first because a zero-order SKU has an
undefined/zero ACOS, so we catch "traffic but no sales" before any ACOS test):
  1. Click Drain     Ad_Clicks_7D > 20 & Ad_Orders_7D == 0     -> PAUSE_ADS
  2. Bleeding Margin  ACOS > 0.35                               -> LOWER_BIDS
  3. Unicorn          0 < ACOS < 0.15 & Ad_Orders_7D > 5        -> INCREASE_BIDS
  4. Default                                                    -> MAINTAIN_ADS
"""

from typing import Any, Dict, List, Tuple

from agents.base import HybridAgent

# --- Intent labels (plain string constants) ---------------------------------
PAUSE_ADS = "PAUSE_ADS"
LOWER_BIDS = "LOWER_BIDS"
INCREASE_BIDS = "INCREASE_BIDS"
MAINTAIN_ADS = "MAINTAIN_ADS"

# --- Rule thresholds & bid magnitudes ---------------------------------------
CLICK_DRAIN_MIN_CLICKS = 20       # > this many clicks with 0 orders -> pause
ACOS_DANGER = 0.35                # ACOS above this is bleeding margin
ACOS_UNICORN_MAX = 0.15           # ACOS below this (and > 0) is highly profitable
UNICORN_MIN_ORDERS = 5            # need real order volume to call it a unicorn

PAUSE_BID_PCT = -1.00             # -100% (paused)
LOWER_BID_PCT = -0.25             # -25%
INCREASE_BID_PCT = 0.20           # +20%
MAINTAIN_BID_PCT = 0.0            # no change

# --- Triage bands (when to spend an LLM call) -------------------------------
ACOS_NEAR_DANGER = 0.28           # 0.28..0.35 = near-danger judgment call
ACOS_NEAR_UNICORN = 0.20          # 0.15..0.20 = near-unicorn judgment call
AD_SPEND_ESCALATE = 500.0         # Ad_Spend_7D at/above this is high-stakes

# --- Guardrails (hard safety bounds applied after the decision) -------------
MAX_BID_UP = 0.50                 # never raise bids more than +50% in one step
MAX_BID_DOWN = -1.00              # -100% = paused, the hard floor


class AdvertisingAgent(HybridAgent):
    """Hybrid PPC analyst (rules + bounded LLM judgment)."""

    name = "AdvertisingAgent"

    SYSTEM_PROMPT = (
        "You are an Amazon PPC Advertising strategist. You DO NOT do any "
        "arithmetic - every number is pre-computed and given to you. A rule engine "
        "has already produced a baseline recommendation and a list of feasible "
        "options, each with an exact suggested_bid_change_pct. Weigh ACOS, clicks, "
        "orders, impressions, data freshness and any flags, then CONFIRM the "
        "baseline or OVERRIDE it with a different feasible option. You may ONLY pick "
        "an option from feasible_options. Respond with ONLY a JSON object with keys: "
        "chosen_intent (one of the feasible intents), suggested_bid_change_pct (must "
        "equal that option's value), agree_with_rule (boolean), reasoning, confidence "
        "(0-1 number), primary_risk."
    )

    # --- rules -------------------------------------------------------------
    def _apply_rules(self, sku: Dict[str, Any]) -> Dict[str, Any]:
        acos = sku.get("ACOS")
        clicks = sku.get("Ad_Clicks_7D")
        orders = sku.get("Ad_Orders_7D")

        if clicks is not None and orders is not None \
                and clicks > CLICK_DRAIN_MIN_CLICKS and orders == 0:
            return self._mk(sku, PAUSE_ADS, PAUSE_BID_PCT,
                            f"Click drain: {clicks} clicks and 0 orders; pause ads "
                            f"(high traffic, zero conversion).")

        if acos is not None and acos > ACOS_DANGER:
            return self._mk(sku, LOWER_BIDS, LOWER_BID_PCT,
                            f"Bleeding margin: ACOS {acos:.2%} > {ACOS_DANGER:.0%}; "
                            f"lower bids.")

        if acos is not None and orders is not None \
                and 0.0 < acos < ACOS_UNICORN_MAX and orders > UNICORN_MIN_ORDERS:
            return self._mk(sku, INCREASE_BIDS, INCREASE_BID_PCT,
                            f"Unicorn: ACOS {acos:.2%} (< {ACOS_UNICORN_MAX:.0%}) with "
                            f"{orders} orders; highly profitable, scale up.")

        return self._mk(sku, MAINTAIN_ADS, MAINTAIN_BID_PCT,
                        "No rule triggered; maintain ads.")

    def _mk(self, sku: Dict[str, Any], intent: str, bid_change_pct: float,
            reason: str) -> Dict[str, Any]:
        return {
            "agent": self.name, "sku": sku.get("SKU"), "intent": intent,
            "suggested_bid_change_pct": bid_change_pct, "reason": reason,
        }

    # --- feasible menu -----------------------------------------------------
    def _feasible_options(self, sku: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            {"intent": PAUSE_ADS, "suggested_bid_change_pct": PAUSE_BID_PCT},
            {"intent": LOWER_BIDS, "suggested_bid_change_pct": LOWER_BID_PCT},
            {"intent": INCREASE_BIDS, "suggested_bid_change_pct": INCREASE_BID_PCT},
            {"intent": MAINTAIN_ADS, "suggested_bid_change_pct": MAINTAIN_BID_PCT},
        ]

    # --- triage ------------------------------------------------------------
    def _should_escalate(self, sku: Dict[str, Any],
                         rule: Dict[str, Any]) -> Tuple[bool, str]:
        acos = sku.get("ACOS")
        orders = sku.get("Ad_Orders_7D")
        spend = sku.get("Ad_Spend_7D")
        reasons: List[str] = []

        if acos is not None and ACOS_NEAR_DANGER <= acos <= ACOS_DANGER:
            reasons.append(f"near-danger ACOS ({acos:.2%})")
        if acos is not None and orders is not None \
                and ACOS_UNICORN_MAX <= acos < ACOS_NEAR_UNICORN and orders > UNICORN_MIN_ORDERS:
            reasons.append(f"near-unicorn ACOS ({acos:.2%})")
        if spend is not None and spend >= AD_SPEND_ESCALATE:
            reasons.append(f"high spend (${spend:.2f})")

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
            "suggested_bid_change_pct": match["suggested_bid_change_pct"],
            "reason": str(data.get("reasoning", rule["reason"])),
            "confidence": data.get("confidence"),
            "primary_risk": str(data.get("primary_risk", "")),
            "agree_with_rule": data.get("agree_with_rule"),
        }

    # --- guardrails --------------------------------------------------------
    def _apply_guardrails(self, sku: Dict[str, Any],
                          decision: Dict[str, Any]) -> None:
        pct = decision.get("suggested_bid_change_pct")
        if pct is None:
            return
        clamped = min(max(pct, MAX_BID_DOWN), MAX_BID_UP)
        if clamped != pct:
            decision["suggested_bid_change_pct"] = clamped
            decision["guardrail_note"] = (
                f"bid change clamped to [{MAX_BID_DOWN:+.0%}, {MAX_BID_UP:+.0%}] "
                f"({pct:+.0%}->{clamped:+.0%})")


if __name__ == "__main__":
    import json

    agent = AdvertisingAgent()
    mock_skus = [
        {  # clear-cut click drain, low spend -> rules only, no LLM
            "SKU": "SKU-DRAIN", "ACOS": 0.0, "Ad_Clicks_7D": 45,
            "Ad_Orders_7D": 0, "Ad_Impressions_7D": 5000, "Ad_Spend_7D": 60.0,
        },
        {  # near-danger ACOS + high spend -> escalates to LLM
            "SKU": "SKU-NEAR", "ACOS": 0.31, "Ad_Clicks_7D": 120,
            "Ad_Orders_7D": 14, "Ad_Impressions_7D": 12000, "Ad_Spend_7D": 900.0,
        },
        {  # default maintain, low spend -> rules only
            "SKU": "SKU-MAINT", "ACOS": 0.22, "Ad_Clicks_7D": 15,
            "Ad_Orders_7D": 4, "Ad_Impressions_7D": 3000, "Ad_Spend_7D": 80.0,
        },
    ]
    for s in mock_skus:
        print(json.dumps(agent.analyze(s), indent=2, default=str))
