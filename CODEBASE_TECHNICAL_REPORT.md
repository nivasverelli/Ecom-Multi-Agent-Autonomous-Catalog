# Amazon Aggregator Multi-Agent Prototype — Ground-Truth Technical Report

> Purpose: a faithful, code-level description of this prototype to seed a design doc.
> Every claim below is pulled from the actual source. Two framing corrections up front:
>
> 1. **There is no live `SharedContext` shared-state object in the running system.** That
>    dataclass exists only in a *dead/legacy* code cluster (`models/`, `simulator/`,
>    `utils/learning.py`, `utils/persistence.py`, `utils/data_manager.py`,
>    `utils/logging_helpers.py`) that nothing in the active pipeline imports. The real
>    "shared state" is (a) a per-SKU plain `dict` and (b) a cross-run JSON memory file.
> 2. **A feedback loop IS implemented** (a primitive contextual bandit graded run-over-run),
>    not merely stubbed — see Section 5. It is, however, *batch/manual* (you must feed the
>    next CSV snapshot), not live.

---

## 1. ARCHITECTURE OVERVIEW

### The active system (everything `main.py` / `app.py` actually use)

| Component | File | Responsibility |
|---|---|---|
| **Orchestrator / pipeline** | `main.py` | Batch driver. `learn → decide → persist → write outputs`. Loops over every SKU row, calls the two subagents then the manager, writes CSVs + memory. |
| **Pricing & Inventory subagent** | `agents/inventory.py` (`PricingInventoryAgent`) | Proposes a price action (RAISE/RAISE_SLIGHTLY/DROP/HOLD) balancing days-of-supply vs Buy-Box competitiveness. |
| **Advertising subagent** | `agents/advertising.py` (`AdvertisingAgent`) | Proposes a bid action (PAUSE/LOWER/INCREASE/MAINTAIN) to stop wasted spend / scale winners. |
| **Subagent base class** | `agents/base.py` (`HybridAgent`) | Template-method `analyze()`: `rules → triage → (LLM) → guardrails`. Both subagents inherit it. |
| **Manager / orchestrator brain** | `agents/manager.py` (`ManagerAgent`) | The P&L owner. Detects conflict between the two proposals, escalates hard cases to an LLM, enforces hard guardrails + a learned-policy floor, and emits the final per-SKU decision. |
| **Cross-run memory + RL policy** | `utils/memory.py` | Contextual-bandit policy table, reward function, JSON persistence (`manager_memory.json`). This is the manager's "brain across runs." |
| **LLM transport** | `utils/llm.py` | OpenAI-compatible client pointed at OpenRouter; `temperature=0`; raises on any error so callers fall back deterministically. |
| **JSON parsing** | `utils/parsing.py` | Strips ```` ```json ```` fences, extracts outermost `{...}`, raises `AgentParseError` on failure. |
| **Bootstrap / config** | `config.py` | `random.seed(42)`, UTF-8 console, `.env` loader, model routing constants, API-key presence check. |
| **Conversational manager** | `agents/chatbot.py` (`ChatbotAgent`) | First-person "I am the manager" chatbot. Given the decisions + policy + run history, explains *why* it did X for SKU-Y. |
| **Subagent registry** | `agents/registry.py` | `SUBAGENT_REGISTRY = [PricingInventoryAgent(), AdvertisingAgent()]`. "Add a subagent = append one line." (Note: `main.py` actually instantiates the two agents directly; the registry is the intended extension point but is not what the batch loop iterates.) |
| **Streamlit dashboard** | `app.py` (875 lines) | UI wrapper around `main.run_pipeline`, the approve/reject HITL queue, and the chatbot. |

### The legacy / dead cluster (NOT wired into the running system)

Confirmed by grep: `SharedContext`, `simulate_market`, `update_confidence_ledger`,
`append_csv_row`, `DailySimulationResult` are referenced **only within this cluster
itself** — never by `main.py`, `app.py`, or `agents/`.

| File | What it is | Status |
|---|---|---|
| `models/context.py` | `SharedContext` dataclass (working + persistent memory, `read_only_view`) | **Unused** by active pipeline |
| `models/business.py` | `BusinessObjectives`, `OperationalConstraints` | Unused |
| `models/recommendations.py` | `Recommendation`, `ManagerDecision` dataclasses | Unused |
| `models/results.py` | `DailySimulationResult` | Unused |
| `models/enums.py` | `PriceAction`, `AdAction`, `Objective`, `State` | Unused |
| `simulator/market.py` | Single-SKU daily market simulator w/ 2 scheduled events | Unused |
| `utils/learning.py` | A *different* learner: a "confidence ledger" (±0.05 nudges) | Unused |
| `utils/persistence.py` | Writes `amazon_daily_metrics.csv` (a 26-col daily audit trail) | Unused |
| `utils/data_manager.py` | `DataManager` over `master_product_table.xlsx` w/ `Current Price ($)`-style columns | Unused |
| `utils/logging_helpers.py` | Pretty per-day console logging | Unused |

> This cluster is an earlier **single-SKU, day-by-day simulation** design. The shipped
> system is a **batch-over-all-SKUs, run-over-run learning** design. They do not share an
> entry point. Anyone reading the repo cold will be misled by the `models/`/`simulator/`
> code — treat it as a previous prototype.

### Orchestration pattern (active system)

- **Synchronous, single-threaded, per-SKU sequential loop.** No async, no threads, no queues.
- Wiring (`main.py:70-83`):

```python
def decide_all(df, mem, progress_callback=None):
    pricing = PricingInventoryAgent()
    advertising = AdvertisingAgent()
    manager = ManagerAgent(mem)
    decisions = []
    for _, row in df.iterrows():
        sku = row.to_dict()
        p = pricing.analyze(sku)      # propose (price)
        a = advertising.analyze(sku)  # propose (ad)
        d = manager.decide(sku, p, a) # detect conflict, arbitrate, guardrail
        decisions.append(d)
        if progress_callback is not None:
            progress_callback(d)
    return decisions
```

- Full pipeline (`main.py:183-217` `run_pipeline`):
  `load CSV/XLSX → learn_from_prior_run(mem, df) → decide_all → record_decisions →
  append run summary → mem["review_queue"] = flagged → save_memory → write
  manager_decisions.csv + master_sku_data_updated.csv`.
- The same `run_pipeline` is shared by the CLI (`main.run`) and the Streamlit UI (`app.py:470`).
- Each subagent internally may make **one** LLM call (only if it escalates); the manager may
  make **one** LLM call (only if it escalates). So a single SKU costs 0–3 LLM calls.

---

## 2. SHARED STATE DESIGN

There are **two** distinct "state" objects in the active system. Neither is the
`SharedContext` dataclass (that is the legacy cluster).

### (A) Per-SKU working state = a plain `dict` (one CSV row)

Built in the loop as `sku = row.to_dict()`. It is the raw CSV schema (28 columns). Header
from `master_sku_data_updated.csv`:

```
SKU, ASIN, Brand, Product Name, Category, Unit_COGS, FBA_Fee, Amazon_Referral_Fee_%,
Current_Price, Buy_Box_Price, Lowest_Competitor_Price, Is_Buy_Box_Winner,
Competitor_Count, Sales_Rank, Available_Inventory, Inbound_Inventory,
Historical_Velocity_7D, Days_of_Supply, Storage_Type, Ad_Impressions_7D, Ad_Clicks_7D,
Ad_Spend_7D, Ad_Orders_7D, Ad_Sales_7D, CPC, CTR, ACOS, ROAS
```

Example real row (SKU-1000):
```
SKU-1000, B0100000, Aerovo, Aerovo Sleeping Bag, Outdoor, 13.1, 4.25, 0.08, 44.42,
44.29, 45.54, False, 11, 54463, 1422, 468, 205, 48.6, Standard, 26253, 391, 551.31,
38, 1687.96, 1.41, 0.0149, 0.3266, 3.06
```

**Read/write by field (active system):**

| Field | Read by | Written by |
|---|---|---|
| `Days_of_Supply` | pricing (`_apply_rules`, triage), manager `_objective`/`_finalize` stockout, `price_state` bucket | input CSV only |
| `Is_Buy_Box_Winner` | pricing rules/triage, manager `_objective`, `price_state` bucket | input CSV only |
| `Current_Price` | pricing rules/menu/guardrails, manager option menu / margin floor / band clamp / change %; `profit_proxy` | **rewritten** by `write_updated_master` (→ `new_price`) for AUTO-applied SKUs |
| `Lowest_Competitor_Price` | pricing (DROP target), manager DROP option | input CSV only |
| `Competitor_Count` | pricing monopoly rule | input CSV only |
| `Historical_Velocity_7D` | pricing/manager exposure triage; reward `profit_proxy` | input CSV only |
| `ACOS` | advertising rules/triage, `ad_state` bucket, reward ACOS penalty | **rewritten** by `write_updated_master` when a bid change is applied (recomputed = `Ad_Spend_7D/Ad_Sales_7D`) |
| `Ad_Clicks_7D`, `Ad_Orders_7D` | advertising click-drain / unicorn rules | input CSV only |
| `Ad_Spend_7D` | advertising spend triage; reward proxy | **rewritten** as bid-change *proxy*: `new_spend = old * (1 + bid_change_pct)` |
| `Ad_Sales_7D` | used to recompute ACOS after bid change | input CSV only |
| `Unit_COGS`, `FBA_Fee`, `Amazon_Referral_Fee_%` | manager `_margin_floor_price`; reward proxy | input CSV only |
| `Category` | bucket keys (`price_bucket_key`/`ad_bucket_key`) | input CSV only |

Subagents/manager **never mutate the SKU dict**; they return new decision dicts. The only
place SKU fields are written back is `write_updated_master` / `_apply_to_master` in
`main.py`, which produces the *next run's* input file.

### (B) Cross-run state = `manager_memory.json` (the manager's brain)

Schema documented in `utils/memory.py:7-21` and `_empty_memory()`:

```python
def _empty_memory():
    return {"version": 1, "runs": [], "last_decisions": {},
            "policy": {}, "review_queue": []}
```

Layout (real values from the live file):

```json
{
  "version": 1,
  "runs": [ {"run_id":6, "date":"2026-06-22 18:19:27", "n_skus":100,
             "n_changed":18, "n_applied":18, "n_flagged":0} ],
  "last_decisions": {                      // snapshot graded next run
    "SKU-1003": {"date":"...","category":"Outdoor",
                 "price_bucket":"Outdoor|overstock|losing_box",
                 "ad_bucket":"Outdoor|mid_acos",
                 "price_intent":"RAISE_PRICE_SLIGHTLY","old_price":74.39,"new_price":75.88,
                 "ad_intent":"MAINTAIN_ADS","bid_change_pct":0.0,
                 "pre":{"price":74.39,"cogs":21.74,"fba_fee":7.03,"referral_pct":0.12,
                        "velocity":443,"ad_spend":728.33}}
  },
  "policy": {                              // the contextual bandit value table
    "Outdoor|overstock|losing_box": {
      "DROP_PRICE":          {"count":4,"total_reward":-5747.28,"avg_reward":-1436.82},
      "RAISE_PRICE_SLIGHTLY":{"count":2,"total_reward":275.38, "avg_reward":137.69}
    },
    "Outdoor|mid_acos": {
      "LOWER_BIDS":  {"count":12,"total_reward":-150.0, "avg_reward":-12.5},
      "MAINTAIN_ADS":{"count":18,"total_reward":-7008.26,"avg_reward":-389.35}
    }
    // ... ~60 buckets total
  },
  "review_queue": [],
  "last_input_path": "uploaded_input.csv",
  "last_input_filename": "master_sku_data_day3.csv",
  "last_learn_notes": [ "graded 14 prior decisions, total reward -8245.90", ... ]
}
```

**Read/write by field:**

| Field | Read by | Written by |
|---|---|---|
| `policy` | manager `_should_escalate` (cold check), `_llm_decide` (annotates options w/ avg_reward/times_tried), `_finalize` (policy floor + confidence temper); chatbot | `update_policy` during `learn_from_prior_run` |
| `last_decisions` | `learn_from_prior_run` (grading); restored by UI | `record_decisions` after each run; `approve_decision` on HITL approve |
| `runs` | UI/chatbot history | `run_pipeline` appends one summary per run |
| `review_queue` | UI HITL queue | `run_pipeline` (= flagged decisions); `approve/reject_decision` remove items |
| `last_input_path/filename/learn_notes` | UI session restoration | `run_pipeline` |

**Initialization:** `load_memory()` returns `_empty_memory()` if no file exists (first run);
otherwise loads JSON and `setdefault`s any missing top-level keys (forward-compatible).

**Update after each cycle (`run_pipeline`, `main.py:197-213`):**
```python
record_decisions(mem, df, decisions, run_date)          # snapshot APPLIED decisions
mem.setdefault("runs", []).append({...summary...})
mem["review_queue"] = [d for d in decisions if d["requires_human_review"]]
mem["last_input_path"] = input_path
save_memory(mem)                                        # write JSON
```
Note the **policy** itself is updated *at the start of the next run* by
`learn_from_prior_run` (grading last run against the freshly-supplied numbers), not at the
end of the current run.

---

## 3. PROPOSE STEP

Both subagents share `HybridAgent.analyze()` (`agents/base.py:71-109`):

```python
def analyze(self, sku):
    rule = self._apply_rules(sku)                 # 1. deterministic baseline
    escalate, why = self._should_escalate(sku, rule)  # 2. triage
    if not escalate:
        decision = dict(rule); decision.update(decided_by="rules", escalated=False, ...)
        return self._finalize(sku, decision)      # clear-cut → ship rule answer free
    feasible = self._feasible_options(sku)
    payload = {"context": dict(sku), "rule_baseline": {...},
               "feasible_options": feasible, "instruction": "Confirm ... or override ..."}
    try:
        raw = call_llm(self.model, self.SYSTEM_PROMPT, payload)  # 3. bounded LLM
        decision = self._from_llm(sku, safe_json_parse(raw), feasible, rule)
    except Exception as exc:                        # any error → rule baseline
        decision = dict(rule); decision.update(decided_by="rules_fallback", llm_error=...)
    return self._finalize(sku, decision)           # 4. guardrails
```

### Pricing subagent — inputs and rule logic (`agents/inventory.py:63-90`)

Inputs read from SKU: `Days_of_Supply`, `Is_Buy_Box_Winner`, `Current_Price`,
`Lowest_Competitor_Price`, `Competitor_Count` (+ `Historical_Velocity_7D` for triage).

```python
def _apply_rules(self, sku):
    dos = sku.get("Days_of_Supply"); is_winner = bool(sku.get("Is_Buy_Box_Winner", False))
    price = sku.get("Current_Price"); comp = sku.get("Lowest_Competitor_Price")
    comp_count = sku.get("Competitor_Count")

    if dos is not None and price is not None and dos < CRITICAL_STOCK_DAYS:   # < 14
        return self._mk(sku, RAISE_PRICE, round(price*(1+RAISE_PCT),2), "...")        # +5%
    if comp_count == 0 and is_winner and price is not None:                  # monopoly
        return self._mk(sku, RAISE_PRICE_SLIGHTLY, round(price*(1+RAISE_SLIGHTLY_PCT),2), "...")  # +2%
    if (dos is not None and dos > OVERSTOCK_DAYS and not is_winner           # > 45 & losing
            and comp is not None and comp > 0):
        return self._mk(sku, DROP_PRICE, round(comp - UNDERCUT_DELTA, 2), "...")  # comp - $0.01
    return self._mk(sku, HOLD_PRICE, round(price,2) if price else None, "No rule triggered; hold price.")
```

**Output schema** (`_mk` baseline; LLM path adds `confidence`, `primary_risk`,
`agree_with_rule`, `reason`←reasoning):
```python
{"agent":"PricingInventoryAgent","sku":...,"intent":"DROP_PRICE",
 "current_price":91.67,"suggested_price":84.25,"reason":"...",
 "confidence":0.7,"primary_risk":"...","agree_with_rule":False,    # LLM path only
 "decided_by":"llm"|"rules"|"rules_fallback","escalated":bool,"triage_reason":...}
```

### Advertising subagent — rule logic (`agents/advertising.py:66-89`)

Inputs: `ACOS`, `Ad_Clicks_7D`, `Ad_Orders_7D` (+ `Ad_Spend_7D` for triage).

```python
def _apply_rules(self, sku):
    acos = sku.get("ACOS"); clicks = sku.get("Ad_Clicks_7D"); orders = sku.get("Ad_Orders_7D")
    if clicks and orders is not None and clicks > CLICK_DRAIN_MIN_CLICKS and orders == 0:  # >20 clicks, 0 orders
        return self._mk(sku, PAUSE_ADS, PAUSE_BID_PCT, "...")          # -100%
    if acos is not None and acos > ACOS_DANGER:                        # ACOS > 0.35
        return self._mk(sku, LOWER_BIDS, LOWER_BID_PCT, "...")         # -25%
    if acos is not None and orders is not None and 0.0 < acos < ACOS_UNICORN_MAX and orders > UNICORN_MIN_ORDERS:
        return self._mk(sku, INCREASE_BIDS, INCREASE_BID_PCT, "...")   # +20% (ACOS<0.15 & >5 orders)
    return self._mk(sku, MAINTAIN_ADS, MAINTAIN_BID_PCT, "...")        # 0%
```

**Output schema:**
```python
{"agent":"AdvertisingAgent","sku":...,"intent":"LOWER_BIDS",
 "suggested_bid_change_pct":-0.25,"reason":"...",
 "confidence":0.62,"primary_risk":"...","agree_with_rule":True, ...}
```

**Triage (when an LLM call is spent)** — the "gray zones" plus high stakes:
- Pricing (`inventory.py:118-139`): near-stockout `14 ≤ DoS < 20`; near-overstock
  `40 < DoS ≤ 45`; losing-box-above-competitor judgment; revenue exposure
  `price*velocity ≥ 2000`.
- Advertising (`advertising.py:108-123`): near-danger ACOS `0.28–0.35`; near-unicorn ACOS
  `0.15–0.20` w/ >5 orders; high spend `Ad_Spend_7D ≥ 500`.

**Guardrails on the proposal** (`_apply_guardrails`): pricing clamps RAISE/HOLD to ±10% band
(DROP exempt, only floored to `MIN_PRICE=0.01`); advertising clamps bid change to
`[-100%, +50%]`.

---

## 4. CONFLICT DETECTION & RESOLUTION (the manager)

### How conflict is detected — a "velocity-effect sign product"

Each intent is mapped to its effect on sales velocity (`agents/manager.py:60-73`):

```python
def _velocity_effect_price(intent):
    if intent == DROP_PRICE: return 1            # cutting price → pushes volume UP
    if intent in {RAISE_PRICE, RAISE_PRICE_SLIGHTLY}: return -1   # raising → volume DOWN
    return 0                                      # HOLD → neutral

def _velocity_effect_ad(intent):
    if intent == INCREASE_BIDS: return 1         # more ads → volume UP
    if intent in {PAUSE_ADS, LOWER_BIDS}: return -1   # less ads → volume DOWN
    return 0
```

**Conflict = the two levers push velocity in opposite directions:**
```python
conflict = (_velocity_effect_price(price_rec["intent"])
            * _velocity_effect_ad(ad_rec["intent"]) < 0)
```
(Product `< 0` ⇒ one is `+1` and the other `-1`.) This same expression is computed in
`_should_escalate`, in the `_llm_decide` payload, and again in `_finalize` (stored as the
`conflict` column).

Concrete conflicting pairs the rule flags:
- DROP_PRICE (push volume up) **vs** PAUSE_ADS or LOWER_BIDS (pull volume down).
- RAISE_PRICE / RAISE_PRICE_SLIGHTLY (pull down) **vs** INCREASE_BIDS (push up).
Non-conflicting: anything with HOLD_PRICE or MAINTAIN_ADS (effect 0), or both pushing the
same way.

### What escalates to the LLM (`_should_escalate`, manager.py:141-166)

The manager spends an LLM call if **any** of:
1. **agents conflict** (the sign product above).
2. **high economic exposure**: `Current_Price * Historical_Velocity_7D ≥ ESCALATE_EXPOSURE (50000)`.
3. **low subagent confidence**: a *changed* proposal with pricing `confidence < 0.60`.
4. **uncertain / cold policy**: a non-HOLD price intent whose bucket/action has
   `count < MIN_SAMPLES_TRUSTED (3)`.

Otherwise → deterministic rule path. (In the live run: 36/100 escalated, 64/100 rules.)

### The resolution functions

**Conflict / hard case → LLM arbitration** (`_llm_decide`, manager.py:213-268). The payload
hands the LLM the SKU context, both agent recommendations, the active objective, the
conflict flag, and **policy evidence** — every price/ad option annotated with its historical
`avg_reward` and `times_tried`:

```python
def annotate(opts, bucket):
    out = []
    for o in opts:
        avg, n = policy_value(self.mem, bucket, o["intent"])
        out.append({**o, "avg_reward": avg, "times_tried": n})
    return out
# ... LLM must return final_price_intent + final_ad_intent chosen from the menus;
# infeasible choices raise ValueError → deterministic fallback.
```

**No conflict / clear-cut → deterministic pass-through** (`_rule_decide`, manager.py:189-210).
This is essentially a pass-through of the subagent intents, with **one** encoded heuristic:

```python
# rule-path heuristic: don't raise price while losing the buy box
if objective == PRESERVE_RANK and price_intent in _PRICE_UP:
    notes.append("rank protection: held price instead of raising while losing the buy box")
    price_intent, new_price, overrode = HOLD_PRICE, sku.get("Current_Price"), True
```

### The hard rules/heuristics encoded right now (all in `_finalize`, manager.py:271-360)

Both paths funnel through `_finalize`, which applies, **in order**:

- **(a) Stockout protection (hard, overrides the LLM):** if `Days_of_Supply < STOCKOUT_FLOOR_DAYS (14)`:
  - cancel any `DROP_PRICE` → `HOLD_PRICE`;
  - cancel any `INCREASE_BIDS` → `MAINTAIN_ADS`.
- **(b) Policy floor (hard veto of a proven-losing move):** if the chosen price intent (≠HOLD)
  has `policy_is_negative` (≥3 samples **and** avg reward `< -1.0`) → force `HOLD_PRICE`;
  same for ad intent → `MAINTAIN_ADS`. *(This is the most-frequently-firing override in the
  live logs — e.g. `policy floor: LOWER_BIDS avg -12.5/12 runs -> MAINTAIN`.)*
- **(c) Margin floor:** if `new_price < (COGS + FBA)/(1-referral) + $0.01` → raise to that floor.
- **(d) Band clamp:** for non-DROP moves, clamp `new_price` to `±MAX_PRICE_CHANGE_PCT (10%)`
  of current.
- **(e) HITL gate** → sets `requires_human_review`:
  - price move `> HITL_BIG_MOVE_PCT (15%)`;
  - any change on a SKU with `Current_Price ≥ HITL_HIGH_PRICE ($200)`;
  - changed + non-HOLD + zero policy data + high price.
  - `applied = changed and not requires_review`.
- **(f) Confidence:** base (subagent/LLM) confidence ±0.15 depending on the policy track
  record for the chosen bucket/action.

**Priority ladder / objective** (`_objective`, manager.py:101-107):
`AVOID_STOCKOUT` (if DoS<14) → `PRESERVE_RANK` (if not Buy-Box winner) → `MAXIMIZE_PROFIT`.

### Does the manager ever just pass proposals through?

Yes. When `_should_escalate` returns False (clear-cut, non-conflicting, in-policy SKU), the
manager runs `_rule_decide`, which keeps the subagents' intents verbatim **except** the
rank-protection heuristic — then still applies the `_finalize` guardrails. So even
"pass-through" decisions can be silently modified by the stockout/policy/margin/band rules.

---

## 5. LOGGING / FEEDBACK LOOP

### What gets logged each run

Three artifacts, all in the project root:

1. **`manager_decisions.csv`** — full per-SKU report, **27 columns** (one row per SKU). The
   schema *is* the manager's `_finalize` return dict:
   ```
   sku, asin, brand, product_name, category, objective, conflict, old_price, new_price,
   price_intent, price_change_pct, price_reason, ad_intent, bid_change_pct, ad_reason,
   changed, applied, requires_human_review, review_reason, confidence, manager_notes,
   rejected_reason, price_bucket, ad_bucket, decided_by, escalated, triage_reason
   ```
2. **`master_sku_data_updated.csv`** — the master with AUTO-applied changes (price →
   `Current_Price`; bid % → `Ad_Spend_7D` proxy w/ `ACOS` recomputed; flagged rows left
   unchanged). Designed to be fed back in as the next run's input.
3. **`manager_memory.json`** — `runs[]` summaries, `last_decisions{}` snapshot,
   `policy{}` bandit table, `review_queue[]`, plus `last_learn_notes` (human-readable grade
   lines). Storage = **flat JSON file** (no DB).

> The legacy `utils/persistence.py` writes a different file, `amazon_daily_metrics.csv`
> (26 columns incl. `daily_profit`, `confidence_ledger`, `lesson_learned`), but it is part
> of the dead simulator cluster and never runs.

### Is there a mechanism that reads past logs to influence future decisions?

**Yes — implemented, but primitive and batch-only.** This is the closed loop:

- At the **start** of each run, `learn_from_prior_run` (`main.py:47-67`) reads
  `mem["last_decisions"]` (last run's APPLIED decisions) and grades each against the *new*
  CSV's actual numbers:
  ```python
  reward, why = compute_reward(rec["pre"], now.to_dict())
  update_policy(mem, rec["price_bucket"], rec["price_intent"], reward)
  update_policy(mem, rec["ad_bucket"],    rec["ad_intent"],    reward)
  ```
- **Reward** (`utils/memory.py:119-140`) = Δ(profit proxy) − guardrail penalties:
  `profit_proxy = (price − cogs − fba − price*referral) * velocity − ad_spend`, then
  `−$50` if it landed below the stockout floor, `−$25` if ACOS ended above 0.35.
- **Policy** is a contextual bandit: `policy[bucket][action] = {count, total_reward,
  avg_reward}` updated as a running average. Buckets are coarse
  `Category|supply_state|box_state` (price) and `Category|acos_band` (ad).
- That learned policy then **feeds forward** into the *next* decisions in three concrete ways:
  (1) cold-policy escalation trigger; (2) `avg_reward`/`times_tried` evidence injected into
  the manager's LLM prompt; (3) the **hard policy floor** that vetoes proven-losing actions
  in `_finalize`. The live policy table already encodes real learning, e.g.
  `Outdoor|overstock|losing_box → DROP_PRICE avg_reward -1436.82 (4 runs)` now auto-vetoes
  DROP_PRICE there.

So the loop is real and has run 6 times (see `runs[]`). **Caveats / honest limits:**
- It is **not live**: learning only happens when a *human supplies the next CSV snapshot*
  (the "actual numbers"). There is no market simulator in this path and no scheduler.
- Credit assignment is crude: the *same* whole-SKU reward is attributed to **both** the price
  lever and the ad lever (`update_policy` called twice with the same reward).
- Reward is a static profit *proxy*; it cannot observe true demand response to a price change
  (the next CSV's velocity is whatever the user supplies, not a simulated reaction).
- Buckets are coarse and category-scoped; a brand-new category/situation is "cold" with 0 data.
- The separate `utils/learning.py` confidence-ledger (a different ±0.05 nudging learner) is
  **not** wired in — do not confuse it with the bandit.

### What would be needed to fully close the loop (assessment)

1. A **ground-truth outcome source** — either real Amazon SP-API metrics N days after a
   decision, or a demand simulator (the legacy `simulator/market.py` is a starting point) —
   so reward reflects the market's actual reaction rather than a re-supplied snapshot.
2. **Per-lever credit assignment** (counterfactual or at least separated reward signals) so
   price and ad actions are not both blamed/credited identically.
3. **Decision→outcome joining by timestamp** (a proper event log / DB keyed by SKU+date),
   replacing the single-slot `last_decisions` (which only remembers the *immediately
   previous* run).
4. **Exploration policy** (epsilon-greedy / UCB). Today selection is rules+LLM+hard floor;
   the bandit only vetoes and tempers confidence — it never deliberately explores.
5. Migration from a flat JSON file to a real store as SKU count grows.

---

## 6. SAMPLE RUN (real end-to-end trace)

> The pipeline was **not executed** for this report (it would call the LLM and overwrite the
> CSV/JSON outputs, and requires a live `OPENROUTER_API_KEY`). The following is a real trace
> reconstructed from the committed inputs (`uploaded_input.csv`), the committed outputs
> (`manager_decisions.csv`), and `manager_memory.json` — i.e. an actual run that already
> happened (run_id 6, 2026-06-22 18:19:27). SKU-1003 is a textbook conflict case.

**① Initial state (input row, `uploaded_input.csv` SKU-1003):**
```
Category=Outdoor  Current_Price=74.39  Lowest_Competitor_Price=75.29  Competitor_Count=5
Is_Buy_Box_Winner=False  Days_of_Supply=79.7  Historical_Velocity_7D=443
ACOS=0.1958  Ad_Spend_7D=728.33  Ad_Clicks_7D=421  Ad_Orders_7D=50
Unit_COGS=21.74  FBA_Fee=7.03  Amazon_Referral_Fee_%=0.12
```

**② Proposals**
- *Pricing subagent:* rule fires **Overstocked & losing box** (DoS 79.7 > 45, not winner,
  comp>0) → **DROP_PRICE** to `comp − 0.01 = $75.28`. (Triage also true: revenue exposure
  74.39×443 ≈ 32,955 ≥ 2000, plus losing-box-above/near-competitor → LLM consulted; reason
  recorded: *"overstocked with 79.7 days of supply … dropping the price to $75.28 … to
  liquidate excess inventory."*)
- *Advertising subagent:* proposes **LOWER_BIDS** (−25%) — reason: *"ACOS … 19.58% …
  lowering bids could help improve profitability … CTR is low."*

**③ Conflict detected (manager):**
`_velocity_effect_price(DROP_PRICE)=+1` × `_velocity_effect_ad(LOWER_BIDS)=−1 = −1 < 0` →
**conflict = True** → `_should_escalate` returns `(True, "agents conflict")` → LLM arbitration.

**④ Manager resolution (`_llm_decide` + `_finalize`):**
- LLM is shown policy evidence for the Outdoor buckets, including
  `DROP_PRICE avg_reward −1436.82 (4 runs)` and `RAISE_PRICE_SLIGHTLY avg_reward +137.69
  (2 runs)` for `Outdoor|overstock|losing_box`, and `LOWER_BIDS avg_reward −12.5 (12 runs)`
  for `Outdoor|mid_acos`.
- LLM picks **RAISE_PRICE_SLIGHTLY** ($75.88) + (initially) LOWER_BIDS.
  `rejected_reason`: *"DROP_PRICE has a very negative average reward historically … HOLD_PRICE
  also negative."*
- `_finalize` guardrails: **policy floor** fires on ads —
  `LOWER_BIDS avg −12.5 / 12 runs (< −1.0, ≥3 samples)` → vetoed to **MAINTAIN_ADS**
  (`manager_notes`: *"policy floor: LOWER_BIDS avg -12.5/12 runs -> MAINTAIN"*). Price
  +2% is within band, above margin floor, < 15% → no HITL.
- Confidence tempered up to **1.0** (RAISE_PRICE_SLIGHTLY had positive policy avg).

**⑤ Final logged decision (`manager_decisions.csv` SKU-1003):**
```
old_price=74.39  new_price=75.88  price_intent=RAISE_PRICE_SLIGHTLY  price_change_pct=0.02
ad_intent=MAINTAIN_ADS  bid_change_pct=0.0  objective=preserve_organic_rank  conflict=True
decided_by=llm  escalated=True  triage_reason="agents conflict"
changed=True  applied=True  requires_human_review=False  confidence=1.0
price_bucket=Outdoor|overstock|losing_box  ad_bucket=Outdoor|mid_acos
manager_notes="Raising the price slightly … positive average reward historically …;
               policy floor: LOWER_BIDS avg -12.5/12 runs -> MAINTAIN"
rejected_reason="DROP_PRICE … very negative average reward … HOLD_PRICE also negative …"
```

**⑥ What persists for the next run:** because `applied=True`, `record_decisions` writes a
`last_decisions["SKU-1003"]` snapshot (the `pre` block + chosen intents/buckets). The next
CSV the user uploads will be graded against it, updating
`policy["Outdoor|overstock|losing_box"]["RAISE_PRICE_SLIGHTLY"]`.

**Aggregate of that run (100 SKUs):** `decided_by` rules=64 / llm=36; conflict True=26;
escalated=36; changed=18; applied=18; flagged=0. (Cross-check: `runs[5]` = n_changed 18,
n_applied 18, n_flagged 0.)

---

## 7. WHAT'S ACTUALLY BUILT vs. STUBBED

### Fully functional
- The **rules engines** for both subagents (deterministic, first-match-wins) — complete and tested via each file's `__main__` mock block.
- The **HybridAgent template** (rules → triage → LLM → guardrails) with hard deterministic fallback on any LLM error/parse failure/infeasible choice.
- The **manager** conflict detection, LLM arbitration, and all `_finalize` guardrails (stockout, policy floor, margin floor, band clamp, HITL gate, confidence tempering).
- The **contextual-bandit learning loop** (grade prior run → update policy → feed forward) — genuinely working and already populated with 6 runs of data.
- **Persistence**: `manager_memory.json` + both CSV outputs, with Excel-lock-safe writes (`_safe_to_csv` warns instead of crashing).
- **HITL workflow**: `review_queue`, `approve_decision`, `reject_decision`.
- **Streamlit UI** (`app.py`) and the **chatbot** (`agents/chatbot.py`).

### Partially implemented / simplifications a reviewer must know
- **Ads "applied" via a proxy.** There is no bid column; a bid % change is applied to
  `Ad_Spend_7D` (`new = old*(1+pct)`) and `ACOS` is mechanically recomputed as
  `Ad_Spend/Ad_Sales`. `main.py:101-108` says so explicitly.
- **Reward is a proxy, not measured.** `profit_proxy` is a per-unit-margin×velocity−ad_spend
  estimate; "absolute scale does not matter … only the run-over-run delta" (`memory.py:111`).
- **Shared whole-SKU reward credited to both levers** (price and ad) — crude credit assignment.
- **`last_decisions` holds only the most recent run** (single slot per SKU); deeper history
  lives only in the aggregated `policy` table, not as joinable decision→outcome events.
- **Learning is human-paced** — it advances only when someone uploads the next CSV.
- **Registry vs. loop mismatch:** `agents/registry.py` is presented as the extension point,
  but `decide_all` instantiates `PricingInventoryAgent`/`AdvertisingAgent` directly and does
  not iterate the registry.

### Placeholder / dead / TODO
- **Entire `models/` + `simulator/` + `utils/{learning,persistence,data_manager,logging_helpers}.py`
  cluster is dead code** for the shipped pipeline (the old single-SKU daily-sim design).
  `SharedContext`, `simulate_market`, `update_confidence_ledger`, `append_csv_row` are never
  called by `main.py`/`app.py`. A design-doc reader should not assume `SharedContext` is the
  live state model.
- `utils/data_manager.py` targets a different file (`master_product_table.xlsx`) with
  different column names (`Current Price ($)`, `Revenue ($)`) and **hardcoded assumptions**:
  `COGS = 40% of price`, `Ad Spend = 10% of revenue`. Not used.

### Hardcoded values / magic numbers to flag
- Pricing: critical `<14d`, overstock `>45d`, raise `+5%`/`+2%`, undercut `$0.01`, band `±10%`.
- Advertising: click-drain `>20 clicks & 0 orders`, ACOS danger `0.35`, unicorn `<0.15 & >5 orders`,
  bids `pause −100% / lower −25% / increase +20%`, clamp `[-100%,+50%]`.
- Manager: band `10%`, HITL big-move `15%`, HITL high-price `$200`, margin buffer `$0.01`,
  LLM-escalation exposure `$50,000`, low-confidence `0.60`.
- Memory/reward: stockout floor `14d`, ACOS ceiling `0.35`, stockout penalty `$50`, ACOS
  penalty `$25`, `MIN_SAMPLES_TRUSTED=3`, `POLICY_NEG_THRESHOLD=-1.0`.
- Both LLM models default to `openai/gpt-4o-mini`; `random.seed(42)`.
- Legacy `simulator/market.py` demand formula hardcodes a `$24.99` reference price and `$120`
  ad-spend baseline (dead code, but informative for the simulator design).

---

## 8. TECH STACK

- **Language:** Python 3 (uses `str.reconfigure`, dataclasses, f-strings).
- **Dependencies** (`requirements.txt`): `pandas` (tabular I/O), `openai` (the OpenAI Python
  SDK, **pointed at OpenRouter** via `base_url="https://openrouter.ai/api/v1"`), `streamlit`
  (dashboard), `openpyxl` (read `.xlsx`). Plus `altair` (charts, imported in `app.py`).
  Stdlib: `json, re, random, os, csv, dataclasses, enum, abc, datetime`.
- **LLM routing** (`config.py`): `SPECIALIST_MODEL` and `MANAGER_MODEL`, both default
  `openai/gpt-4o-mini`, overridable via `.env`. Design intent (comments): specialists cheap,
  manager can be upgraded to a stronger model. All calls `temperature=0` for determinism;
  **the LLM is forbidden from doing arithmetic** — every number is pre-computed and passed in.
- **Why hybrid (from the docstrings):** rules are the free, vectorizable, real-time floor and
  the fallback; the LLM is a bounded judgment layer spent only on escalated SKUs; the bandit
  generalizes by `category × situation × action` so new SKUs inherit behavior. The manager
  doc frames it as a tiered cascade (Tier-1 rules → Tier-2 policy → Tier-3 LLM → HITL gate)
  meant to scale "from this 100-SKU sample to millions of SKUs."
- **Reproducibility:** `config.py` runs `random.seed(42)` at import (before the legacy
  simulator samples its two event days); UTF-8 console reconfigure so printing LLM Unicode
  never crashes on Windows.

### Synthetic / sample data
- **Source files:** `master_sku_data.xlsx` (sheet `"Master SKU Data"`, the default input),
  its CSV twin `master_sku_data_updated.csv`, and `uploaded_input.csv` (the last UI upload,
  labeled `master_sku_data_day3.csv`).
- **Shape:** 100 SKUs, IDs `SKU-1000 … SKU-1099`, ASINs `B0100000…`, fabricated brands
  (e.g. *Aerovo*) and product names (*Aerovo Sleeping Bag*), across categories *Outdoor,
  Kitchen, Electronics Accessories, Fitness, Home Decor, Travel, Pet Supplies, Lighting*.
  28 columns spanning economics (COGS/FBA/referral/price), Buy-Box & competition, inventory
  (`Available_Inventory`, `Days_of_Supply`, `Storage_Type ∈ {Standard, Oversize}`), and a
  full 7-day ad block (impressions/clicks/spend/orders/sales/CPC/CTR/ACOS/ROAS) — internally
  consistent (e.g. `ACOS ≈ Ad_Spend_7D / Ad_Sales_7D`, `ROAS ≈ 1/ACOS`).
- **How it was generated:** **no data-generation script exists in the repo** — the synthetic
  table appears pre-generated and shipped as the `.xlsx`. The "days" (`master_sku_data_day3`)
  are produced by the pipeline itself: `write_updated_master` emits
  `master_sku_data_updated.csv`, which is re-uploaded as the next day's input, which is how
  the run-over-run learning is driven.

---

## Key file map (for the design doc author)

- Orchestration: `main.py`
- Subagents: `agents/base.py`, `agents/inventory.py`, `agents/advertising.py`
- Manager: `agents/manager.py`
- Learning/memory: `utils/memory.py` → `manager_memory.json`
- LLM/parse/config: `utils/llm.py`, `utils/parsing.py`, `config.py`
- UI + chatbot: `app.py`, `agents/chatbot.py`
- **Ignore (legacy, unused):** `models/*`, `simulator/*`, `utils/learning.py`,
  `utils/persistence.py`, `utils/data_manager.py`, `utils/logging_helpers.py`
