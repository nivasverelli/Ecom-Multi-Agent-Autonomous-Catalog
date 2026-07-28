"""Shared base for the hybrid (rules + LLM) subagents.

Design: the deterministic RULES are the floor and the safety net; the LLM is a
bounded JUDGMENT layer on top of them. The LLM never does arithmetic and can
only choose from the feasible options the rules computed.

`analyze()` is a template method with four stages:
  1. RULES      -> deterministic baseline {intent, suggested target, reason}
  2. TRIAGE     -> is this SKU ambiguous/high-stakes enough to spend an LLM call?
                   Clear-cut SKUs ship the rule answer for free (cost control).
  3. LLM        -> (only if escalated) CONFIRM or OVERRIDE within feasible options
  4. GUARDRAILS -> clamp the final decision to hard safety bounds

The rule baseline is ALWAYS the fallback: if the LLM is unavailable, slow, or
returns something infeasible, we ship the deterministic answer. Decisions are
source-agnostic dict-in/dict-out, and carry `as_of`/freshness so the same agents
work on an Excel snapshot today or a BigQuery + live-API context later.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Tuple

import config
from utils.llm import call_llm
from utils.parsing import safe_json_parse


class HybridAgent(ABC):
    """Template-method base shared by the pricing and advertising subagents."""

    name: str = "HybridAgent"
    model: str = config.SPECIALIST_MODEL
    SYSTEM_PROMPT: str = ""

    # --- hooks each subagent must implement ---------------------------------
    @abstractmethod
    def _apply_rules(self, sku: Dict[str, Any]) -> Dict[str, Any]:
        """Deterministic baseline decision for this SKU."""

    @abstractmethod
    def _feasible_options(self, sku: Dict[str, Any]) -> List[Dict[str, Any]]:
        """The bounded menu the LLM is allowed to choose from."""

    @abstractmethod
    def _should_escalate(self, sku: Dict[str, Any],
                         rule: Dict[str, Any]) -> Tuple[bool, str]:
        """(escalate?, reason). True only for ambiguous/high-stakes SKUs."""

    @abstractmethod
    def _from_llm(self, sku: Dict[str, Any], data: Dict[str, Any],
                  feasible: List[Dict[str, Any]],
                  rule: Dict[str, Any]) -> Dict[str, Any]:
        """Build a decision from the LLM's choice, snapped to a feasible option."""

    @abstractmethod
    def _apply_guardrails(self, sku: Dict[str, Any],
                          decision: Dict[str, Any]) -> None:
        """Clamp the decision to hard safety bounds, mutating it in place."""

    # --- shared helpers ------------------------------------------------------
    @staticmethod
    def _freshness(sku: Dict[str, Any]) -> Dict[str, Any]:
        """Surface data-freshness so rules/LLM can be cautious on stale inputs.

        Convention: the context assembler may stamp `as_of` (timestamp) and a
        `freshness` map (per-field age/source). On an Excel snapshot these are
        simply absent.
        """
        return {"as_of": sku.get("as_of"), "fields": sku.get("freshness", {})}

    def analyze(self, sku: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full rules -> triage -> (LLM) -> guardrails pipeline."""
        rule = self._apply_rules(sku)
        escalate, why = self._should_escalate(sku, rule)

        # (2) Triage: clear-cut SKUs never touch the LLM.
        if not escalate:
            decision = dict(rule)
            decision.update(decided_by="rules", escalated=False,
                            triage_reason=why or "clear-cut rule; no LLM needed",
                            llm_error=None)
            return self._finalize(sku, decision)

        # (3) Escalate: let the LLM confirm/override within the feasible menu.
        feasible = self._feasible_options(sku)
        payload = {
            "context": dict(sku),
            "data_freshness": self._freshness(sku),
            "rule_baseline": {"intent": rule.get("intent"), "reason": rule.get("reason")},
            "feasible_options": feasible,
            "instruction": ("Confirm the rule baseline or override it with another "
                            "feasible option if the context justifies it. Choose "
                            "exactly one option from feasible_options."),
        }
        try:
            raw = call_llm(self.model, self.SYSTEM_PROMPT, payload)
            data = safe_json_parse(raw)
            decision = self._from_llm(sku, data, feasible, rule)
            decision.update(decided_by="llm", llm_error=None)
        except Exception as exc:  # any transport/parse error -> rule baseline
            decision = dict(rule)
            decision.update(decided_by="rules_fallback",
                            llm_error=f"{type(exc).__name__}: {exc}")
            print(f"  [LLM FAIL: {self.name}] {decision['llm_error']} -> rule baseline")

        decision.update(escalated=True, triage_reason=why,
                        rule_baseline={"intent": rule.get("intent"),
                                       "reason": rule.get("reason")})
        return self._finalize(sku, decision)

    def _finalize(self, sku: Dict[str, Any],
                  decision: Dict[str, Any]) -> Dict[str, Any]:
        decision.setdefault("agent", self.name)
        decision.setdefault("sku", sku.get("SKU"))
        decision.setdefault("as_of", sku.get("as_of"))
        self._apply_guardrails(sku, decision)  # (4) hard safety bounds
        return decision
