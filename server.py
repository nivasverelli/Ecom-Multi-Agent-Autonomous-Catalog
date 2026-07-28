import os
import json
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import pandas as pd
from datetime import datetime

import config
from agents.chatbot import ChatbotAgent
from utils.memory import MEMORY_PATH, load_memory, profit_proxy
from main import (
    DEFAULT_INPUT, DECISIONS_CSV, UPDATED_MASTER_CSV,
    run_pipeline, approve_decision, reject_decision, _safe_to_csv,
    _load,
)

PORT = 8000
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
CHAT_HISTORY_PATH = "chat_history.json"

# Shared state matching app.py state variables
session_state = {
    "messages": [],
    "pending_actions": [],
    "chat_prefill": "",
    "result": None,
    "decisions": [],
    "rows_by_sku": {},
    "current_data": [],
    "run_date": "",
    "uploaded_filename": ""
}

chatbot = ChatbotAgent()


def save_chat_history():
    """Persist the chat transcript + pending actions so they survive a restart."""
    try:
        with open(CHAT_HISTORY_PATH, "w", encoding="utf-8") as fh:
            json.dump({
                "messages": session_state["messages"],
                "pending_actions": session_state["pending_actions"],
            }, fh, indent=2, default=str)
    except OSError as exc:
        print(f"[WARN] could not save chat history: {exc}")


def load_chat_history():
    """Restore the chat transcript + pending actions from the previous session."""
    if not os.path.exists(CHAT_HISTORY_PATH):
        return
    try:
        with open(CHAT_HISTORY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        session_state["messages"] = data.get("messages", [])
        session_state["pending_actions"] = data.get("pending_actions", [])
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[WARN] could not load chat history: {exc}")


def load_last_run_state():
    mem = load_memory()
    if not mem.get("runs"):
        return False
    last_run = mem["runs"][-1]
    run_date = last_run["date"]
    
    input_path = mem.get("last_input_path")
    if not input_path:
        if os.path.exists("uploaded_input.csv"):
            input_path = "uploaded_input.csv"
        else:
            input_path = "master_sku_data.xlsx"
            
    if not os.path.exists(input_path):
        return False
    if not os.path.exists(DECISIONS_CSV):
        return False
        
    try:
        decisions_df = pd.read_csv(DECISIONS_CSV)
        decisions = decisions_df.to_dict("records")
        
        for d in decisions:
            for k, v in d.items():
                if pd.isna(v):
                    d[k] = None
            for bool_col in ["changed", "applied", "requires_human_review", "conflict", "escalated"]:
                if bool_col in d:
                    d[bool_col] = bool(d[bool_col])
                    
        df = _load(input_path)
        rows_by_sku = {str(r["SKU"]): r.to_dict() for _, r in df.iterrows()}
        
        session_state["result"] = {
            "run_date": run_date,
            "df": None, # Will drop DataFrame from state to keep JSON serializable
            "memory": mem,
            "learn_notes": mem.get("last_learn_notes", ["Loaded last run from disk."]),
            "changed": last_run["n_changed"],
            "applied": last_run["n_applied"],
            "flagged": last_run["n_flagged"],
        }
        session_state["decisions"] = decisions
        session_state["rows_by_sku"] = rows_by_sku
        session_state["current_data"] = df.to_dict("records")
        session_state["run_date"] = run_date
        
        if mem.get("last_input_filename"):
            session_state["uploaded_filename"] = mem.get("last_input_filename")
            
        return True
    except Exception as e:
        print(f"Error loading last run state: {e}")
        return False

# Initialize state on boot
load_last_run_state()
load_chat_history()

def _run_summary_md(result, rows_by_sku) -> str:
    decisions = result["decisions"]
    changed = sum(1 for d in decisions if d["changed"])
    applied = sum(1 for d in decisions if d["applied"])
    flagged = sum(1 for d in decisions if d["requires_human_review"])
    n_llm = sum(1 for d in decisions if d.get("decided_by") == "llm")
    n_rules = len(decisions) - n_llm
    
    # Calculate profit delta and revenue delta
    profit_delta = revenue_delta = 0.0
    price_deltas = []
    for d in decisions:
        if not d.get("changed"):
            continue
        row = rows_by_sku.get(str(d["sku"]))
        if row is None or d.get("old_price") is None or d.get("new_price") is None:
            continue
        cogs   = float(row.get("Unit_COGS", 0) or 0)
        fee    = float(row.get("FBA_Fee", 0) or 0)
        refpct = float(row.get("Amazon_Referral_Fee_%", 0) or 0)
        vel    = float(row.get("Historical_Velocity_7D", 0) or 0)
        spend  = float(row.get("Ad_Spend_7D", 0) or 0)
        op, npx = float(d["old_price"]), float(d["new_price"])
        new_spend = spend * (1 + (d.get("bid_change_pct") or 0.0))
        profit_delta  += (profit_proxy(npx, cogs, fee, refpct, vel, new_spend)
                          - profit_proxy(op, cogs, fee, refpct, vel, spend))
        revenue_delta += (npx - op) * vel
        if op:
            price_deltas.append((npx - op) / op)
    avg_price_delta = (sum(price_deltas) / len(price_deltas)) if price_deltas else 0.0

    moves = sorted(
        [d for d in decisions if d["changed"] and d.get("old_price") and d.get("new_price")],
        key=lambda d: abs(d["new_price"] - d["old_price"]), reverse=True)[:3]
    move_lines = "\n".join(
        f"- **{d['sku']}** ({d['category']}): ${d['old_price']} → ${d['new_price']} "
        f"· {d['price_intent']} · _{d.get('decided_by','rules')}_"
        for d in moves) or "- No price changes this run."

    learn = result.get("learn_notes", [])
    learn_line = (f"\n\n📚 I also graded my **previous** run: {learn[0]}"
                  if learn and not learn[0].startswith("first run") else "")

    return (
        f"✅ **Done — I analysed {len(decisions)} SKUs.**\n\n"
        f"- Changed **{changed}**, auto-applied **{applied}**, flagged **{flagged}** for your review.\n"
        f"- I used my **LLM brain on {n_llm}** escalated SKUs and **rules + memory on {n_rules}**.\n"
        f"- Projected profit impact **${profit_delta:+,.0f}**, revenue **${revenue_delta:+,.0f}**, "
        f"avg price move **{avg_price_delta:+.1%}** _(static, pre-elasticity)_.\n\n"
        f"**Biggest moves:**\n{move_lines}{learn_line}\n\n"
        f"Ask me *why* I made any call — I'll tell you whether it came from my "
        f"RL memory or the LLM. The flagged ones are waiting for you in the "
        f"**Review Queue**."
    )

class CockpitHandler(BaseHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        if path == "/api/state":
            mem = load_memory()
            response_state = dict(session_state)
            
            # Recalculate dynamic statistics
            profit_delta = revenue_delta = avg_price_delta = 0.0
            price_deltas = []
            decisions = session_state["decisions"]
            rows_by_sku = session_state["rows_by_sku"]
            
            for d in decisions:
                if not d.get("changed"):
                    continue
                row = rows_by_sku.get(str(d["sku"]))
                if row is None or d.get("old_price") is None or d.get("new_price") is None:
                    continue
                cogs   = float(row.get("Unit_COGS", 0) or 0)
                fee    = float(row.get("FBA_Fee", 0) or 0)
                refpct = float(row.get("Amazon_Referral_Fee_%", 0) or 0)
                vel    = float(row.get("Historical_Velocity_7D", 0) or 0)
                spend  = float(row.get("Ad_Spend_7D", 0) or 0)
                op, npx = float(d["old_price"]), float(d["new_price"])
                new_spend = spend * (1 + (d.get("bid_change_pct") or 0.0))
                profit_delta  += (profit_proxy(npx, cogs, fee, refpct, vel, new_spend)
                                  - profit_proxy(op, cogs, fee, refpct, vel, spend))
                revenue_delta += (npx - op) * vel
                if op:
                    price_deltas.append((npx - op) / op)
            avg_price_delta = (sum(price_deltas) / len(price_deltas)) if price_deltas else 0.0
            
            # File baseline comparison
            has_comparison = False
            comparison = {}
            if session_state["current_data"]:
                try:
                    baseline_df = _load("master_sku_data.xlsx")
                    current_df = pd.DataFrame(session_state["current_data"])
                    
                    current_rev = (current_df["Current_Price"] * current_df["Historical_Velocity_7D"]).sum()
                    baseline_rev = (baseline_df["Current_Price"] * baseline_df["Historical_Velocity_7D"]).sum()
                    rev_diff = current_rev - baseline_rev
                    rev_pct = (rev_diff / baseline_rev) if baseline_rev else 0.0
                    
                    current_price = current_df["Current_Price"].mean()
                    baseline_price = baseline_df["Current_Price"].mean()
                    price_diff = current_price - baseline_price
                    price_pct = (price_diff / baseline_price) if baseline_price else 0.0
                    
                    current_spend = current_df["Ad_Spend_7D"].sum()
                    baseline_spend = baseline_df["Ad_Spend_7D"].sum()
                    spend_diff = current_spend - baseline_spend
                    spend_pct = (spend_diff / baseline_spend) if baseline_spend else 0.0
                    
                    has_comparison = True
                    comparison = {
                        "current_rev": current_rev, "baseline_rev": baseline_rev, "rev_pct": rev_pct,
                        "current_price": current_price, "baseline_price": baseline_price, "price_pct": price_pct,
                        "current_spend": current_spend, "baseline_spend": baseline_spend, "spend_pct": spend_pct
                    }
                except Exception as ex:
                    print("Error calculating comparison:", ex)
            
            # Format memory policy
            policy_rows = []
            for b, acts in mem.get("policy", {}).items():
                for a, s in acts.items():
                    policy_rows.append({
                        "situation": b, "action": a, "times_tried": s["count"],
                        "avg_reward": round(s["avg_reward"], 2)
                    })
            policy_rows = sorted(policy_rows, key=lambda x: x["avg_reward"], reverse=True)
            
            response_state.update({
                "memory": mem,
                "policy": policy_rows,
                "profit_delta": profit_delta,
                "revenue_delta": revenue_delta,
                "avg_price_delta": avg_price_delta,
                "has_comparison": has_comparison,
                "comparison": comparison,
                "has_uploaded_csv": os.path.exists("uploaded_input.csv")
            })
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(response_state).encode("utf-8"))
            return
            
        elif path == "/api/download/decisions":
            if os.path.exists(DECISIONS_CSV):
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", f"attachment; filename={DECISIONS_CSV}")
                self.send_cors_headers()
                self.end_headers()
                with open(DECISIONS_CSV, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Decisions CSV not found")
            return
            
        elif path == "/api/download/master":
            if os.path.exists(UPDATED_MASTER_CSV):
                self.send_response(200)
                self.send_header("Content-Type", "text/csv")
                self.send_header("Content-Disposition", f"attachment; filename={UPDATED_MASTER_CSV}")
                self.send_cors_headers()
                self.end_headers()
                with open(UPDATED_MASTER_CSV, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404, "Updated Master CSV not found")
            return

        # Fallback to serve static files
        if path == "/":
            path = "/index.html"
            
        local_path = os.path.join(STATIC_DIR, path.lstrip("/"))
        if os.path.exists(local_path) and os.path.isfile(local_path):
            self.send_response(200)
            
            # Basic content-type mapper
            if local_path.endswith(".html"):
                self.send_header("Content-Type", "text/html")
            elif local_path.endswith(".css"):
                self.send_header("Content-Type", "text/css")
            elif local_path.endswith(".js"):
                self.send_header("Content-Type", "application/javascript")
            elif local_path.endswith(".png"):
                self.send_header("Content-Type", "image/png")
            elif local_path.endswith(".ico"):
                self.send_header("Content-Type", "image/x-icon")
                
            self.send_cors_headers()
            self.end_headers()
            with open(local_path, "rb") as f:
                self.wfile.write(f.read())
        else:
            self.send_error(404, f"File not found: {path}")

    def do_POST(self):
        parsed_url = urllib.parse.urlparse(self.path)
        path = parsed_url.path
        
        content_length = int(self.headers.get("Content-Length", 0))
        post_data = self.rfile.read(content_length) if content_length > 0 else b""
        
        if path == "/api/run":
            # Parse input parameters
            try:
                params = json.loads(post_data.decode("utf-8")) if post_data else {}
            except:
                params = {}
                
            input_source = params.get("source", "master_sku_data.xlsx")
            input_path = "uploaded_input.csv" if input_source == "Upload a CSV" else DEFAULT_INPUT
            
            if input_source == "Upload a CSV" and not os.path.exists("uploaded_input.csv"):
                # Fallback to default if no file was uploaded
                input_path = DEFAULT_INPUT
                
            uploaded_name = session_state["uploaded_filename"]
            
            try:
                result = run_pipeline(input_path, input_filename=uploaded_name)
                rows_by_sku = {str(r["SKU"]): r.to_dict() for _, r in result["df"].iterrows()}
                
                # Format decisions to clear NaN values
                decisions = result["decisions"]
                for d in decisions:
                    for k, v in d.items():
                        if pd.isna(v):
                            d[k] = None
                    for bool_col in ["changed", "applied", "requires_human_review", "conflict", "escalated"]:
                        if bool_col in d:
                            d[bool_col] = bool(d[bool_col])
                
                session_state["result"] = {
                    "run_date": result["run_date"],
                    "df": None,
                    "memory": load_memory(),
                    "learn_notes": result.get("learn_notes", ["Loaded last run from disk."]),
                    "changed": len([d for d in decisions if d["changed"]]),
                    "applied": len([d for d in decisions if d["applied"]]),
                    "flagged": len([d for d in decisions if d["requires_human_review"]])
                }
                session_state["decisions"] = decisions
                session_state["rows_by_sku"] = rows_by_sku
                session_state["current_data"] = result["df"].to_dict("records")
                session_state["run_date"] = result["run_date"]
                
                # Add pipeline completion details directly to chatbot assistant feed
                summary = _run_summary_md(result, rows_by_sku)
                session_state["messages"].append({"role": "assistant", "content": summary})
                save_chat_history()
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "run_date": result["run_date"]}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/chat":
            try:
                params = json.loads(post_data.decode("utf-8"))
                prompt = params.get("prompt", "")
                
                session_state["messages"].append({"role": "user", "content": prompt})
                
                mem = load_memory()
                resp = chatbot.handle_message(
                    prompt,
                    current_data=session_state["current_data"],
                    decisions=session_state["decisions"],
                    policy=mem.get("policy", {}),
                    runs=mem.get("runs", []),
                )
                
                reply = resp.get("response", "I couldn't generate a response.")
                session_state["messages"].append({"role": "assistant", "content": reply})
                
                if resp.get("proposed_actions"):
                    session_state["pending_actions"].extend(resp["proposed_actions"])

                save_chat_history()

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True, "reply": reply}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/approve_decision":
            try:
                params = json.loads(post_data.decode("utf-8"))
                sku = str(params.get("sku"))
                
                target = None
                for d in session_state["decisions"]:
                    if str(d["sku"]) == sku:
                        target = d
                        break
                        
                if target and approve_decision(target, session_state["rows_by_sku"][sku], session_state["run_date"]):
                    target.update(applied=True, requires_human_review=False, review_reason="approved by human")
                    _safe_to_csv(pd.DataFrame(session_state["decisions"]), DECISIONS_CSV)
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "Failed to approve decision (close Excel if open)"}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/reject_decision":
            try:
                params = json.loads(post_data.decode("utf-8"))
                sku = str(params.get("sku"))
                
                target = None
                for d in session_state["decisions"]:
                    if str(d["sku"]) == sku:
                        target = d
                        break
                        
                if target:
                    reject_decision(target)
                    target.update(requires_human_review=False, changed=False, review_reason="rejected by human")
                    _safe_to_csv(pd.DataFrame(session_state["decisions"]), DECISIONS_CSV)
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "SKU not found"}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/approve_proposed":
            try:
                params = json.loads(post_data.decode("utf-8"))
                idx = int(params.get("index"))
                
                if 0 <= idx < len(session_state["pending_actions"]):
                    action = session_state["pending_actions"][idx]
                    sku = str(action.get("sku"))
                    val = action.get("new_value")
                    act_type = action.get("action")
                    
                    wrote = True
                    if os.path.exists(UPDATED_MASTER_CSV):
                        upd = pd.read_csv(UPDATED_MASTER_CSV)
                        m = upd["SKU"].astype(str) == sku
                        if act_type == "CHANGE_PRICE":
                            upd.loc[m, "Current_Price"] = val
                        elif act_type == "CHANGE_AD_SPEND":
                            upd.loc[m, "Ad_Spend_7D"] = val
                        wrote = _safe_to_csv(upd, UPDATED_MASTER_CSV)
                        
                    if wrote:
                        session_state["pending_actions"].pop(idx)
                        save_chat_history()
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json")
                        self.send_cors_headers()
                        self.end_headers()
                        self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                    else:
                        self.send_response(400)
                        self.send_header("Content-Type", "application/json")
                        self.send_cors_headers()
                        self.end_headers()
                        self.wfile.write(json.dumps({"error": "Failed to update master (close Excel)"}).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.send_cors_headers()
                    self.end_headers()
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/reject_proposed":
            try:
                params = json.loads(post_data.decode("utf-8"))
                idx = int(params.get("index"))
                
                if 0 <= idx < len(session_state["pending_actions"]):
                    session_state["pending_actions"].pop(idx)
                    save_chat_history()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
                else:
                    self.send_response(404)
                    self.send_cors_headers()
                    self.end_headers()
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/upload":
            try:
                # Find boundary and parse multi-part form data manually (robust, zero-dependency)
                content_type = self.headers.get("Content-Type", "")
                boundary = content_type.split("boundary=")[-1].encode("utf-8")
                
                parts = post_data.split(b"--" + boundary)
                filename = "uploaded_input.csv"
                file_content = b""
                
                for part in parts:
                    if b"Content-Disposition:" in part:
                        headers_part, body_part = part.split(b"\r\n\r\n", 1)
                        header_str = headers_part.decode("utf-8")
                        
                        # Find original file name
                        if 'filename="' in header_str:
                            orig_name = header_str.split('filename="')[-1].split('"')[0]
                            session_state["uploaded_filename"] = orig_name
                            
                        # Strip trailing \r\n
                        if body_part.endswith(b"\r\n"):
                            body_part = body_part[:-2]
                        file_content = body_part
                        break
                        
                if file_content:
                    with open("uploaded_input.csv", "wb") as f:
                        f.write(file_content)
                    
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True, "filename": session_state["uploaded_filename"]}).encode("utf-8"))
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_cors_headers()
                    self.end_headers()
                    self.wfile.write(json.dumps({"error": "No file content extracted"}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return
            
        elif path == "/api/reset":
            try:
                if os.path.exists(MEMORY_PATH):
                    os.remove(MEMORY_PATH)
                if os.path.exists(DECISIONS_CSV):
                    os.remove(DECISIONS_CSV)
                if os.path.exists("uploaded_input.csv"):
                    os.remove("uploaded_input.csv")
                if os.path.exists(CHAT_HISTORY_PATH):
                    os.remove(CHAT_HISTORY_PATH)

                for k in ["result", "decisions", "rows_by_sku", "current_data", "run_date", "uploaded_filename"]:
                    session_state[k] = [] if isinstance(session_state[k], list) else {} if isinstance(session_state[k], dict) else ""
                session_state["messages"] = []
                session_state["pending_actions"] = []
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
            return

        self.send_error(404, f"API endpoint not found: {path}")

def run_server():
    os.makedirs(STATIC_DIR, exist_ok=True)
    server = HTTPServer(("0.0.0.0", PORT), CockpitHandler)
    print(f"unybrands Catalog Cockpit running on http://localhost:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Server...")
        server.server_close()

if __name__ == "__main__":
    run_server()
