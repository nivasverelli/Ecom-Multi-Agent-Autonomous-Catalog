"""Manager / orchestrator - the P&L owner over the two subagents.

A TRUE HYBRID AGENT, built as a tiered/cascade decision architecture so it scales
from this 100-SKU sample to millions of SKUs unchanged:

  Tier 1  RULES   - deterministic, vectorizable, handle the long tail for ~free.
  Tier 2  POLICY  - the learned bandit (utils.memory); generalizes by
                    category x situation x action, so new SKUs inherit behavior.
  Tier 3  LLM     - the expensive reasoner, spent ONLY on escalated SKUs
                    (conflicts, high economic exposure, uncertain/cold situations).
  Gate    HITL    - humans, reserved for the highest-stakes/flagged decisions.

How the three brains combine on an escalated SKU:
  POLICY feeds the LLM as evidence -> LLM reasons & picks a final option ->
  RULES enforce hard constraints (margin floor, stockout protection, band) +
  a HARD policy floor (a severely-negative action is vetoed even if the LLM
  picked it) -> the HITL gate flags risky decisions -> outcomes later update the
  POLICY. If the LLM is unavailable, we fall back to the deterministic path.
"""

from typing import Any, Dict, List, Tuple

import config
from agents.inventory import (
    RAISE_PRICE, RAISE_PRICE_SLIGHTLY, DROP_PRICE, HOLD_PRICE,
    RAISE_PCT, RAISE_SLIGHTLY_PCT, UNDERCUT_DELTA,
)
from agents.advertising import (
    PAUSE_ADS, LOWER_BIDS, INCREASE_BIDS, MAINTAIN_ADS,
    PAUSE_BID_PCT, LOWER_BID_PCT, INCREASE_BID_PCT, MAINTAIN_BID_PCT,
)
from utils.llm import call_llm
from utils.parsing import safe_json_parse
from utils.memory import (
    price_bucket_key, ad_bucket_key, policy_value, policy_is_negative,
    STOCKOUT_FLOOR_DAYS, MIN_SAMPLES_TRUSTED,
)

# Objectives (priority ladder) ----------------------------------------------
AVOID_STOCKOUT = "avoid_stockout"
PRESERVE_RANK = "preserve_organic_rank"
MAXIMIZE_PROFIT = "maximize_profit"

# Hard constraints & HITL thresholds ----------------------------------------
MAX_PRICE_CHANGE_PCT = 0.10       # band clamp for non-liquidation moves
HITL_BIG_MOVE_PCT = 0.15          # flag a price move larger than this (autopilot allows up to 15%)
HITL_HIGH_PRICE = 200.0           # flag changes on expensive SKUs (increased from 60.0 to 200.0)
MARGIN_BUFFER = 0.01              # keep contribution per unit strictly positive

# Tier-3 (LLM) escalation thresholds - configurable for any business appetite.
# Raise/lower these to make the manager more or less "agentic" at scale.
ESCALATE_EXPOSURE = 50000.0       # price * velocity above this = high-stakes
ESCALATE_LOW_CONF = 0.60          # a low-confidence subagent change escalates

_PRICE_UP = {RAISE_PRICE, RAISE_PRICE_SLIGHTLY}
_AD_UP = {INCREASE_BIDS}
_AD_DOWN = {PAUSE_ADS, LOWER_BIDS}


def _velocity_effect_price(intent: str) -> int:
    if intent == DROP_PRICE:
        return 1
    if intent in _PRICE_UP:
        return -1
    return 0


def _velocity_effect_ad(intent: str) -> int:
    if intent in _AD_UP:
        return 1
    if intent in _AD_DOWN:
        return -1
    return 0


MANAGER_SYSTEM_PROMPT = (
    "You are the P&L owner / manager for an Amazon catalogue. You DO NOT do any "
    "arithmetic - every number is pre-computed and given to you. For one SKU you "
    "receive: the context, the pricing agent and advertising agent recommendations "
    "(with reasons), the active objective, whether they conflict, and POLICY EVIDENCE "
    "- the average historical reward and number of times each candidate action has "
    "been tried in this situation. Your goal: maximise long-run profit WITHOUT "
    "stocking out. Choose exactly one price option and one ad option from the "
    "provided lists. Use the policy evidence as guidance; you MAY reason against it. "
    "Respond with ONLY a JSON object with keys: final_price_intent (one of the price "
    "option intents), final_ad_intent (one of the ad option intents), chosen_objective, "
    "rationale, rejected_reason (why you did not pick the alternatives), confidence "
    "(0-1 number)."
)


class ManagerAgent:
    """Hybrid P&L owner: deterministic fast path + LLM arbitration + learned policy."""

    name = "MANAGER"

    def __init__(self, memory: Dict[str, Any]):
        self.mem = memory

    # ---------------------------- objective ----------------------------
    def _objective(self, sku: Dict[str, Any]) -> str:
        dos = sku.get("Days_of_Supply")
        if dos is not None and dos < STOCKOUT_FLOOR_DAYS:
            return AVOID_STOCKOUT
        if not bool(sku.get("Is_Buy_Box_Winner", False)):
            return PRESERVE_RANK
        return MAXIMIZE_PROFIT

    @staticmethod
    def _margin_floor_price(sku: Dict[str, Any]) -> float:
        cogs = sku.get("Unit_COGS") or 0.0
        fba = sku.get("FBA_Fee") or 0.0
        referral = sku.get("Amazon_Referral_Fee_%") or 0.0
        denom = max(1e-6, 1.0 - referral)
        return round((cogs + fba) / denom + MARGIN_BUFFER, 2)

    # ---------------------------- option menus ----------------------------
    def _price_options(self, sku: Dict[str, Any]) -> List[Dict[str, Any]]:
        price = sku.get("Current_Price")
        comp = sku.get("Lowest_Competitor_Price")
        opts: List[Dict[str, Any]] = []
        if price is not None:
            opts += [
                {"intent": HOLD_PRICE, "target_price": round(price, 2)},
                {"intent": RAISE_PRICE_SLIGHTLY, "target_price": round(price * (1 + RAISE_SLIGHTLY_PCT), 2)},
                {"intent": RAISE_PRICE, "target_price": round(price * (1 + RAISE_PCT), 2)},
            ]
        if comp is not None and comp > 0:
            opts.append({"intent": DROP_PRICE, "target_price": round(comp - UNDERCUT_DELTA, 2)})
        return opts

    def _ad_options(self) -> List[Dict[str, Any]]:
        return [
            {"intent": MAINTAIN_ADS, "bid_change_pct": MAINTAIN_BID_PCT},
            {"intent": LOWER_BIDS, "bid_change_pct": LOWER_BID_PCT},
            {"intent": INCREASE_BIDS, "bid_change_pct": INCREASE_BID_PCT},
            {"intent": PAUSE_ADS, "bid_change_pct": PAUSE_BID_PCT},
        ]

    # ---------------------------- Tier-3 escalation ----------------------------
    def _should_escalate(self, sku: Dict[str, Any], price_rec: Dict[str, Any],
                         ad_rec: Dict[str, Any]) -> Tuple[bool, str]:
        reasons: List[str] = []
        conflict = (_velocity_effect_price(price_rec.get("intent"))
                    * _velocity_effect_ad(ad_rec.get("intent")) < 0)
        if conflict:
            reasons.append("agents conflict")

        price = sku.get("Current_Price")
        velocity = sku.get("Historical_Velocity_7D")
        if price is not None and velocity is not None \
                and price * velocity >= ESCALATE_EXPOSURE:
            reasons.append("high economic exposure")

        conf = price_rec.get("confidence")
        changed = price_rec.get("intent") != HOLD_PRICE or ad_rec.get("intent") != MAINTAIN_ADS
        if changed and isinstance(conf, (int, float)) and conf < ESCALATE_LOW_CONF:
            reasons.append("low subagent confidence")

        # cold/uncertain: changing in a situation we have little learned data on
        pbk = price_bucket_key(sku)
        _, pcount = policy_value(self.mem, pbk, price_rec.get("intent"))
        if price_rec.get("intent") != HOLD_PRICE and pcount < MIN_SAMPLES_TRUSTED:
            reasons.append("uncertain (cold policy)")

        return (bool(reasons), "; ".join(reasons))

    # ---------------------------- public entry ----------------------------
    def decide(self, sku: Dict[str, Any], price_rec: Dict[str, Any],
               ad_rec: Dict[str, Any]) -> Dict[str, Any]:
        escalate, why = self._should_escalate(sku, price_rec, ad_rec)
        if escalate:
            try:
                d = self._llm_decide(sku, price_rec, ad_rec)
                d["decided_by"] = "llm"
            except Exception as exc:
                print(f"  [LLM FAIL: MANAGER {sku.get('SKU')}] "
                      f"{type(exc).__name__}: {exc} -> deterministic fallback")
                d = self._rule_decide(sku, price_rec, ad_rec)
                d["decided_by"] = "rules_fallback"
        else:
            d = self._rule_decide(sku, price_rec, ad_rec)
            d["decided_by"] = "rules"
        d["escalated"] = escalate
        d["triage_reason"] = why
        return d

    # ---------------------------- Tier-1/2 deterministic ----------------------------
    def _rule_decide(self, sku: Dict[str, Any], price_rec: Dict[str, Any],
                     ad_rec: Dict[str, Any]) -> Dict[str, Any]:
        objective = self._objective(sku)
        price_intent = price_rec.get("intent")
        new_price = price_rec.get("suggested_price")
        ad_intent = ad_rec.get("intent")
        bid_pct = ad_rec.get("suggested_bid_change_pct", 0.0)
        notes: List[str] = []
        overrode = False

        # rule-path heuristic: don't raise price while losing the buy box
        if objective == PRESERVE_RANK and price_intent in _PRICE_UP:
            notes.append("rank protection: held price instead of raising while losing the buy box")
            price_intent, new_price, overrode = HOLD_PRICE, sku.get("Current_Price"), True

        conf = price_rec.get("confidence")
        base_conf = float(conf) if isinstance(conf, (int, float)) else 0.5
        return self._finalize(sku, objective=objective, price_intent=price_intent,
                              new_price=new_price, ad_intent=ad_intent, bid_pct=bid_pct,
                              notes=notes, overrode=overrode, base_conf=base_conf,
                              price_rec=price_rec, ad_rec=ad_rec, rationale="",
                              rejected_reason="")

    # ---------------------------- Tier-3 LLM arbitration ----------------------------
    def _llm_decide(self, sku: Dict[str, Any], price_rec: Dict[str, Any],
                    ad_rec: Dict[str, Any]) -> Dict[str, Any]:
        objective = self._objective(sku)
        pbk, abk = price_bucket_key(sku), ad_bucket_key(sku)
        price_opts = self._price_options(sku)
        ad_opts = self._ad_options()

        def annotate(opts, bucket):
            out = []
            for o in opts:
                avg, n = policy_value(self.mem, bucket, o["intent"])
                out.append({**o, "avg_reward": avg, "times_tried": n})
            return out

        payload = {
            "sku_context": {
                "SKU": sku.get("SKU"), "Category": sku.get("Category"),
                "Days_of_Supply": sku.get("Days_of_Supply"),
                "Is_Buy_Box_Winner": sku.get("Is_Buy_Box_Winner"),
                "Current_Price": sku.get("Current_Price"),
                "Lowest_Competitor_Price": sku.get("Lowest_Competitor_Price"),
                "Competitor_Count": sku.get("Competitor_Count"),
                "ACOS": sku.get("ACOS"),
            },
            "objective": objective,
            "conflict": _velocity_effect_price(price_rec.get("intent"))
            * _velocity_effect_ad(ad_rec.get("intent")) < 0,
            "pricing_agent": {"intent": price_rec.get("intent"),
                              "suggested_price": price_rec.get("suggested_price"),
                              "reason": price_rec.get("reason")},
            "advertising_agent": {"intent": ad_rec.get("intent"),
                                  "bid_change_pct": ad_rec.get("suggested_bid_change_pct"),
                                  "reason": ad_rec.get("reason")},
            "price_options": annotate(price_opts, pbk),
            "ad_options": annotate(ad_opts, abk),
            "instruction": "Pick exactly one price option intent and one ad option intent.",
        }
        raw = call_llm(config.MANAGER_MODEL, MANAGER_SYSTEM_PROMPT, payload)
        data = safe_json_parse(raw)

        fp_intent = str(data["final_price_intent"])
        fa_intent = str(data["final_ad_intent"])
        pmatch = next((o for o in price_opts if o["intent"] == fp_intent), None)
        amatch = next((o for o in ad_opts if o["intent"] == fa_intent), None)
        if pmatch is None or amatch is None:
            raise ValueError(f"LLM chose infeasible option(s): {fp_intent}/{fa_intent}")

        overrode = (fp_intent != price_rec.get("intent")) or (fa_intent != ad_rec.get("intent"))
        base_conf = data.get("confidence")
        base_conf = float(base_conf) if isinstance(base_conf, (int, float)) else 0.6
        return self._finalize(sku, objective=objective, price_intent=fp_intent,
                              new_price=pmatch["target_price"], ad_intent=fa_intent,
                              bid_pct=amatch["bid_change_pct"], notes=[], overrode=overrode,
                              base_conf=base_conf, price_rec=price_rec, ad_rec=ad_rec,
                              rationale=str(data.get("rationale", "")),
                              rejected_reason=str(data.get("rejected_reason", "")))

    # ---------------------------- shared guardrails + HITL gate ----------------------------
    def _finalize(self, sku: Dict[str, Any], *, objective: str, price_intent: str,
                  new_price: Any, ad_intent: str, bid_pct: float, notes: List[str],
                  overrode: bool, base_conf: float, price_rec: Dict[str, Any],
                  ad_rec: Dict[str, Any], rationale: str,
                  rejected_reason: str) -> Dict[str, Any]:
        cur_price = sku.get("Current_Price")
        dos = sku.get("Days_of_Supply")
        pbk, abk = price_bucket_key(sku), ad_bucket_key(sku)
        notes = list(notes)
        conflict = (_velocity_effect_price(price_rec.get("intent"))
                    * _velocity_effect_ad(ad_rec.get("intent")) < 0)

        # (a) HARD stockout protection - applies to BOTH paths regardless of the LLM.
        if dos is not None and dos < STOCKOUT_FLOOR_DAYS:
            if price_intent == DROP_PRICE:
                notes.append("stockout protection: cancelled price drop")
                price_intent, new_price, overrode = HOLD_PRICE, cur_price, True
            if ad_intent == INCREASE_BIDS:
                notes.append("stockout protection: cancelled bid increase")
                ad_intent, bid_pct, overrode = MAINTAIN_ADS, 0.0, True

        # (b) HARD policy floor (advisory+hard floor): veto a proven-losing action.
        if price_intent != HOLD_PRICE and policy_is_negative(self.mem, pbk, price_intent):
            avg, n = policy_value(self.mem, pbk, price_intent)
            notes.append(f"policy floor: {price_intent} avg {avg:+.1f}/{n} runs -> HOLD")
            price_intent, new_price, overrode = HOLD_PRICE, cur_price, True
        if ad_intent != MAINTAIN_ADS and policy_is_negative(self.mem, abk, ad_intent):
            avg, n = policy_value(self.mem, abk, ad_intent)
            notes.append(f"policy floor: {ad_intent} avg {avg:+.1f}/{n} runs -> MAINTAIN")
            ad_intent, bid_pct, overrode = MAINTAIN_ADS, 0.0, True

        # (c) margin floor + (d) band clamp (except an intentional liquidation drop)
        guardrail_breach = False
        if new_price is not None and cur_price is not None:
            floor = self._margin_floor_price(sku)
            if new_price < floor:
                notes.append(f"margin floor raised price ${new_price:.2f}->${floor:.2f}")
                new_price, guardrail_breach = floor, True
            if price_intent != DROP_PRICE:
                hi = round(cur_price * (1 + MAX_PRICE_CHANGE_PCT), 2)
                lo = round(cur_price * (1 - MAX_PRICE_CHANGE_PCT), 2)
                clamped = min(max(new_price, lo), hi)
                if clamped != new_price:
                    notes.append(f"price clamped to +/-{MAX_PRICE_CHANGE_PCT:.0%} band "
                                 f"(${new_price:.2f}->${clamped:.2f})")
                    new_price = clamped

        new_price = round(new_price, 2) if new_price is not None else cur_price
        price_change_pct = abs(new_price - cur_price) / cur_price if cur_price else 0.0
        changed = (new_price != round(cur_price, 2) if cur_price is not None else False) \
            or (bid_pct not in (0.0, None))

        # (e) HITL gate.
        review_reasons: List[str] = []
        if price_change_pct > HITL_BIG_MOVE_PCT:
            review_reasons.append(f"large price move ({price_change_pct:.1%})")
        if changed and cur_price is not None and cur_price >= HITL_HIGH_PRICE:
            review_reasons.append(f"high-price SKU (${cur_price:.2f})")
        _, pcount = policy_value(self.mem, pbk, price_intent)
        if changed and price_intent != HOLD_PRICE and pcount == 0 \
                and cur_price is not None and cur_price >= HITL_HIGH_PRICE:
            review_reasons.append("untested situation (no policy data)")
        requires_review = bool(review_reasons)
        applied = changed and not requires_review

        # (f) confidence: subagent/LLM confidence tempered by policy track record.
        pavg, pn = policy_value(self.mem, pbk, price_intent)
        base = base_conf
        if pn >= 1:
            base += 0.15 if pavg > 0 else -0.15
        confidence = round(max(0.0, min(1.0, base)), 2)

        manager_notes = "; ".join([n for n in [rationale] + notes if n])
        return {
            "sku": sku.get("SKU"), "asin": sku.get("ASIN"), "brand": sku.get("Brand"),
            "product_name": sku.get("Product Name"), "category": sku.get("Category"),
            "objective": objective, "conflict": conflict,
            "old_price": round(cur_price, 2) if cur_price is not None else None,
            "new_price": new_price, "price_intent": price_intent,
            "price_change_pct": round(price_change_pct, 4),
            "price_reason": price_rec.get("reason", ""),
            "ad_intent": ad_intent, "bid_change_pct": bid_pct,
            "ad_reason": ad_rec.get("reason", ""),
            "changed": changed, "applied": applied,
            "requires_human_review": requires_review,
            "review_reason": "; ".join(review_reasons),
            "confidence": confidence, "manager_notes": manager_notes,
            "rejected_reason": rejected_reason,
            "price_bucket": pbk, "ad_bucket": abk,
        }
