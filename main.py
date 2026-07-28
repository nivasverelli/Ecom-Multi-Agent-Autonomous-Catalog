"""Batch manager run over the full SKU table.

No live data: each run grades the PREVIOUS run's applied decisions against the
new CSV's actual numbers (learning), then makes today's decisions for all SKUs.

Flow:
  load CSV/XLSX -> LEARN (grade prior run, update policy) -> DECIDE (subagents +
  manager, per SKU) -> write memory + two CSV outputs.

Outputs:
  manager_decisions.csv        full report: every SKU, old->new, intent, reason,
                               confidence, changed?, requires_human_review?
  master_sku_data_updated.csv  the master with AUTO-applied changes (flagged rows
                               keep their old price pending human approval); can be
                               fed back in as the next run's input.

Run:  python main.py [optional_input_path]
"""

import sys
from datetime import datetime
from typing import Any, Dict, List

import pandas as pd

import config  # noqa: F401 - bootstrap: seed, .env, API key

from agents.inventory import PricingInventoryAgent
from agents.advertising import AdvertisingAgent
from agents.manager import ManagerAgent
from utils.memory import (
    load_memory, save_memory, compute_reward, update_policy,
    make_last_decision_entry,
)

DEFAULT_INPUT = "master_sku_data.xlsx"
DECISIONS_CSV = "manager_decisions.csv"
UPDATED_MASTER_CSV = "master_sku_data_updated.csv"


def _load(path: str) -> pd.DataFrame:
    if path.lower().endswith(".csv"):
        return pd.read_csv(path)
    return pd.read_excel(path, sheet_name="Master SKU Data")


def learn_from_prior_run(mem: Dict[str, Any], df: pd.DataFrame) -> List[str]:
    """Grade each applied decision from last run against the current numbers."""
    prior = mem.get("last_decisions", {})
    if not prior:
        return ["first run - no prior decisions to grade."]
    by_sku = {str(r["SKU"]): r for _, r in df.iterrows()}
    notes: List[str] = []
    total = 0.0
    for sku_id, rec in prior.items():
        now = by_sku.get(str(sku_id))
        if now is None:
            continue
        reward, why = compute_reward(rec.get("pre", {}), now.to_dict())
        total += reward
        # credit BOTH levers' chosen actions in the buckets we acted in.
        update_policy(mem, rec["price_bucket"], rec["price_intent"], reward)
        update_policy(mem, rec["ad_bucket"], rec["ad_intent"], reward)
        notes.append(f"  {sku_id} [{rec['price_intent']}/{rec['ad_intent']}] "
                     f"reward {reward:+.2f} ({'; '.join(why)})")
    notes.insert(0, f"graded {len(prior)} prior decisions, total reward {total:+.2f}")
    return notes


def decide_all(df: pd.DataFrame, mem: Dict[str, Any], progress_callback=None):
    pricing = PricingInventoryAgent()
    advertising = AdvertisingAgent()
    manager = ManagerAgent(mem)
    decisions: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        sku = row.to_dict()
        p = pricing.analyze(sku)
        a = advertising.analyze(sku)
        d = manager.decide(sku, p, a)
        decisions.append(d)
        if progress_callback is not None:
            progress_callback(d)
    return decisions


def record_decisions(mem: Dict[str, Any], df: pd.DataFrame,
                     decisions: List[Dict[str, Any]], run_date: str) -> None:
    """Snapshot APPLIED decisions so the next run can grade them."""
    by_sku = {str(r["SKU"]): r for _, r in df.iterrows()}
    last: Dict[str, Any] = {}
    for d in decisions:
        if not d["applied"]:
            continue
        row = by_sku.get(str(d["sku"]))
        if row is None:
            continue
        last[str(d["sku"])] = make_last_decision_entry(d, row.to_dict(), run_date)
    mem["last_decisions"] = last


def write_updated_master(df: pd.DataFrame, decisions: List[Dict[str, Any]],
                         path: str) -> None:
    """Apply AUTO decisions to a copy of the master and save it.

    Price -> Current_Price (real lever). Ad bid % -> applied to Ad_Spend_7D as a
    proxy (no bid column exists), with ACOS mechanically recomputed. Flagged rows
    are left unchanged (pending human approval).
    """
    out = df.copy()
    idx = {str(r["SKU"]): i for i, (_, r) in enumerate(df.iterrows())}
    for d in decisions:
        if not d["applied"]:
            continue
        i = idx.get(str(d["sku"]))
        if i is None:
            continue
        if d["new_price"] is not None:
            out.at[out.index[i], "Current_Price"] = d["new_price"]
        pct = d.get("bid_change_pct") or 0.0
        if pct and "Ad_Spend_7D" in out.columns:
            new_spend = round(float(out.at[out.index[i], "Ad_Spend_7D"]) * (1 + pct), 2)
            out.at[out.index[i], "Ad_Spend_7D"] = new_spend
            if "Ad_Sales_7D" in out.columns and "ACOS" in out.columns:
                sales = float(out.at[out.index[i], "Ad_Sales_7D"])
                out.at[out.index[i], "ACOS"] = round(new_spend / sales, 4) if sales else 0.0
    _safe_to_csv(out, path)


def _safe_to_csv(df: pd.DataFrame, path: str) -> bool:
    """Write a CSV, but warn instead of crashing if the file is locked (open in
    Excel). Returns True on success."""
    try:
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return True
    except OSError as exc:
        print(f"  [WARN] could not write {path}: {exc}. "
              "Close it if it is open in Excel, then retry.")
        return False


def _apply_to_master(decision: Dict[str, Any], path: str = UPDATED_MASTER_CSV) -> bool:
    """Apply one decision's price/bid change to the updated-master CSV in place."""
    upd = pd.read_csv(path)
    mask = upd["SKU"].astype(str) == str(decision["sku"])
    if not mask.any():
        return False
    if decision.get("new_price") is not None:
        upd.loc[mask, "Current_Price"] = decision["new_price"]
    pct = decision.get("bid_change_pct") or 0.0
    if pct and "Ad_Spend_7D" in upd.columns:
        upd.loc[mask, "Ad_Spend_7D"] = (upd.loc[mask, "Ad_Spend_7D"] * (1 + pct)).round(2)
        if "Ad_Sales_7D" in upd.columns and "ACOS" in upd.columns:
            sales = upd.loc[mask, "Ad_Sales_7D"].replace(0, pd.NA)
            upd.loc[mask, "ACOS"] = (upd.loc[mask, "Ad_Spend_7D"] / sales).round(4).fillna(0.0)
    return _safe_to_csv(upd, path)


def approve_decision(decision: Dict[str, Any], row: Dict[str, Any],
                     run_date: str) -> bool:
    """Human approves a flagged SKU: apply it to the master, record it in memory
    so the next run grades it, and drop it from the review queue. Returns False if
    the master file could not be written (e.g. open in Excel)."""
    if not _apply_to_master(decision):
        return False
    mem = load_memory()
    mem.setdefault("last_decisions", {})[str(decision["sku"])] = \
        make_last_decision_entry(decision, row, run_date)
    mem["review_queue"] = [d for d in mem.get("review_queue", [])
                           if str(d.get("sku")) != str(decision["sku"])]
    save_memory(mem)
    return True


def reject_decision(decision: Dict[str, Any]) -> None:
    """Human rejects a flagged SKU: discard the proposal (no master/memory change
    beyond removing it from the review queue)."""
    mem = load_memory()
    mem["review_queue"] = [d for d in mem.get("review_queue", [])
                           if str(d.get("sku")) != str(decision["sku"])]
    save_memory(mem)


def run_pipeline(input_path: str = DEFAULT_INPUT, input_filename: str = None, progress_callback=None) -> Dict[str, Any]:
    """Learn -> decide -> persist -> write outputs. Returns everything the UI
    needs (df, decisions, memory, learn notes). Shared by the CLI and Streamlit."""
    run_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df = _load(input_path)
    mem = load_memory()

    learn_notes = learn_from_prior_run(mem, df)
    decisions = decide_all(df, mem, progress_callback=progress_callback)

    changed = sum(1 for d in decisions if d["changed"])
    applied = sum(1 for d in decisions if d["applied"])
    flagged = sum(1 for d in decisions if d["requires_human_review"])

    record_decisions(mem, df, decisions, run_date)
    mem.setdefault("runs", []).append({
        "run_id": len(mem.get("runs", [])) + 1, "date": run_date,
        "n_skus": len(df), "n_changed": changed, "n_applied": applied,
        "n_flagged": flagged,
    })
    mem["review_queue"] = [d for d in decisions if d["requires_human_review"]]
    
    # Store input paths and notes for session restoration
    mem["last_input_path"] = input_path
    mem["last_input_filename"] = input_filename or input_path
    mem["last_learn_notes"] = learn_notes
    
    save_memory(mem)

    _safe_to_csv(pd.DataFrame(decisions), DECISIONS_CSV)
    write_updated_master(df, decisions, UPDATED_MASTER_CSV)

    return {"run_date": run_date, "df": df, "decisions": decisions, "memory": mem,
            "learn_notes": learn_notes, "changed": changed, "applied": applied,
            "flagged": flagged}


def run(input_path: str = DEFAULT_INPUT) -> None:
    print("=" * 70)
    print(f" MANAGER BATCH RUNS  |  input: {input_path}")
    print("=" * 70)
    r = run_pipeline(input_path)

    print(f"\n[LEARN] run {r['run_date']}")
    for n in r["learn_notes"]:
        print(n if n.startswith("  ") else f"  {n}")
    print(f"\n[DECIDE] {len(r['df'])} SKUs -> changed={r['changed']}  "
          f"auto-applied={r['applied']}  flagged_for_human={r['flagged']}")
    print("\n[OUTPUT]")
    print(f"  decisions report   -> {DECISIONS_CSV}")
    print(f"  updated master     -> {UPDATED_MASTER_CSV}")
    print(f"  manager memory     -> manager_memory.json")
    if r["flagged"]:
        print(f"\n  {r['flagged']} SKU(s) need human review (requires_human_review column).")
    print("=" * 70)


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_INPUT)
