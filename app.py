"""Streamlit HITL decision dashboard for the Amazon manager pipeline.

Light-premium (Linear/Vercel-style) UI with a top command bar (no sidebar).
Narrative scroll: Control bar -> Business impact -> Explainability ledger ->
Human-in-the-loop review -> Learned policy -> Export. Second tab: Assistant.
"""

import os

import altair as alt
import pandas as pd
import streamlit as st

import config  # noqa: F401 - bootstrap: seed, .env, API key
from agents.chatbot import ChatbotAgent
from utils.memory import MEMORY_PATH, load_memory, profit_proxy
from main import (
    DEFAULT_INPUT, DECISIONS_CSV, UPDATED_MASTER_CSV,
    run_pipeline, approve_decision, reject_decision, _safe_to_csv,
    _load,
)

st.set_page_config(page_title="Amazon Manager", layout="wide",
                   page_icon="📦", initial_sidebar_state="collapsed")

# --------------------------------------------------------------------------
# Styling — light premium SaaS (Linear / Vercel): white, soft shadows,
# refined type, generous whitespace, restrained indigo accent.
# --------------------------------------------------------------------------
st.markdown("""
<style>
@import url('https://rsms.me/inter/inter.css');
:root {
  --bg:#f4f5f6; --panel:#ffffff; --line:#e2e8f0; --line2:#f1f5f9;
  --ink:#0f172a; --muted:#475569; --faint:#94a3b8;
  --accent:#6366f1; --accent-soft:#e0e7ff;
  --green:#10b981; --green-soft:#d1fae5; --red:#ef4444; --red-soft:#fee2e2;
  --amber:#f59e0b; --amber-soft:#fef3c7; --blue:#3b82f6; --blue-soft:#dbeafe;
}
html, body, [class*="css"] {
  font-family:'Inter','SF Pro Text',-apple-system,BlinkMacSystemFont,"Segoe UI",
              Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
}
.stApp { background:var(--bg); }
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="collapsedControl"]
  { visibility:hidden; height:0; }
[data-testid="stHeader"] { background:transparent; }
.block-container { padding-top:1.5rem; padding-bottom:4rem; max-width:1200px; }
h1,h2,h3 { color:var(--ink); letter-spacing:-0.025em; font-weight:700; }
h1 { font-size:2rem; }
h2 { font-size:1.25rem; margin:2.5rem 0 .5rem; }
hr { border-color:var(--line2); }
p, .stMarkdown { color:var(--ink); }

/* Sidebar branding override */
[data-testid="stSidebar"] {
  background-color: #0b0f19!important;
  color: #f8fafc!important;
  border-right: 1px solid #1e293b!important;
}
[data-testid="stSidebar"] button {
  background-color: transparent!important;
  color: #94a3b8!important;
  border: 1px solid transparent!important;
  text-align: left!important;
  justify-content: flex-start!important;
  border-radius: 8px!important;
  font-weight: 600!important;
  padding: 8px 12px!important;
  transition: all 0.15s ease!important;
}
[data-testid="stSidebar"] button:hover {
  background-color: rgba(255, 255, 255, 0.05)!important;
  color: #ffffff!important;
}
[data-testid="stSidebar"] button[kind="primary"] {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%)!important;
  color: #ffffff!important;
  box-shadow: 0 4px 12px rgba(99, 102, 241, 0.25)!important;
}
.sidebar-logo {
  font-size: 1.6rem;
  font-weight: 800;
  letter-spacing: -0.04em;
  color: #ffffff;
  padding: 10px 0 2px;
  display: flex;
  align-items: center;
  gap: 2px;
}
.sidebar-logo .uny {
  color: #ffffff;
}
.sidebar-logo .brands {
  color: #6366f1; /* Indigo accents */
}
.sidebar-sub {
  font-size: 0.62rem;
  font-weight: 700;
  color: #475569;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  margin-top: -6px;
  margin-bottom: 24px;
}

/* buttons */
.stButton > button {
  border-radius:10px; border:1px solid var(--line); background:var(--panel);
  padding:.45rem 1.2rem; font-weight:600; color:var(--ink); font-size:.9rem;
  transition:all .2s cubic-bezier(0.16, 1, 0.3, 1); box-shadow:0 1px 2px rgba(17,17,26,.02);
}
.stButton > button:hover {
  border-color:var(--accent); background:#fcfcfd;
  color:var(--accent); transform:translateY(-1px);
  box-shadow:0 4px 12px rgba(99, 102, 241, 0.08);
}
.stButton > button[kind="primary"] {
  background:linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  color:#fff; border:none;
  box-shadow:0 4px 14px rgba(99, 102, 241, 0.25);
}
.stButton > button[kind="primary"]:hover {
  background:linear-gradient(135deg, #4f46e5 0%, #4338ca 100%);
  color:#fff;
  box-shadow:0 6px 20px rgba(99, 102, 241, 0.35);
}

/* top command bar */
.topbar {
  background:rgba(255, 255, 255, 0.8);
  backdrop-filter:blur(12px);
  -webkit-backdrop-filter:blur(12px);
  border:1px solid rgba(229, 229, 234, 0.7);
  border-radius:16px;
  padding:16px 24px; margin-bottom:12px;
  box-shadow:0 4px 24px rgba(0, 0, 0, 0.02), 0 1px 3px rgba(0, 0, 0, 0.01);
}
.brand { font-size:1.15rem; font-weight:800; letter-spacing:-0.03em; color:var(--ink); }
.brand .dot {
  background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  color:var(--accent);
}
.brand .sub { display:block; font-size:.78rem; font-weight:600; color:var(--faint); letter-spacing:0; margin-top:2px; }

/* page intro */
.intro h1 { font-size:1.8rem; margin:.8rem 0 .2rem; }
.intro p { color:var(--muted); font-size:0.98rem; margin:0; max-width:750px; line-height:1.5; }

/* KPI cards */
.kpi-row { display:flex; gap:16px; margin:16px 0 8px; flex-wrap:wrap; }
.kpi {
  flex:1; min-width:160px; background:var(--panel); border:1px solid var(--line);
  border-radius:16px; padding:18px 20px;
  box-shadow:0 4px 18px rgba(0, 0, 0, 0.01), 0 1px 2px rgba(0, 0, 0, 0.01);
  transition:all .2s cubic-bezier(0.16, 1, 0.3, 1);
}
.kpi:hover {
  border-color:var(--accent);
  transform:translateY(-2px);
  box-shadow:0 8px 24px rgba(99, 102, 241, 0.05), 0 2px 4px rgba(0, 0, 0, 0.01);
}
.kpi .label { color:var(--faint); font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.08em; }
.kpi .value { color:var(--ink); font-size:2rem; font-weight:800; margin-top:6px; letter-spacing:-0.03em; }
.kpi .blue{color:var(--blue);} .kpi .amber{color:var(--amber);} .kpi .green{color:var(--green);} .kpi .red{color:var(--red);}

/* status line */
.statusline { color:var(--muted); font-size:.9rem; margin:4px 0 6px; }
.statusline b { color:var(--ink); }

/* pills */
.pill { display:inline-block; padding:3px 12px; border-radius:8px; font-size:.73rem; font-weight:700; letter-spacing:.01em; }
.pill-green{background:var(--green-soft);color:var(--green);}
.pill-red{background:var(--red-soft);color:var(--red);}
.pill-amber{background:var(--amber-soft);color:var(--amber);}
.pill-gray{background:#f1f5f9;color:#475569;}

/* brain badges */
.brain-llm{background:var(--accent-soft);color:var(--accent);}
.brain-rules{background:var(--green-soft);color:var(--green);}
.brain-fallbk{background:var(--amber-soft);color:var(--amber);}

/* confidence bar */
.conf-wrap{display:inline-flex;align-items:center;gap:8px;vertical-align:middle;font-size:.85rem;color:var(--muted);}
.conf-track{width:80px;height:7px;background:#e2e8f0;border-radius:4px;overflow:hidden;display:inline-block;}
.conf-fill{height:7px;border-radius:4px;background:var(--accent);}

/* review cards */
[data-testid="stExpander"] { border:1px solid var(--line)!important; border-radius:16px!important; box-shadow:0 1px 3px rgba(0,0,0,0.01)!important; background:#ffffff; transition:all 0.2s ease; }
[data-testid="stExpander"]:hover { border-color:#cbd5e1!important; box-shadow:0 4px 18px rgba(0, 0, 0, 0.02)!important; }
.rc-title{font-size:1.05rem;font-weight:700;color:var(--ink);letter-spacing:-0.015em;}
.rc-meta{color:var(--muted);font-size:.9rem;margin-top:4px;}
.rc-reason{color:var(--amber);font-size:.88rem;margin-top:8px;font-weight:600;}
.rc-notes{color:var(--muted);font-size:.86rem;margin-top:4px;font-style:italic;}
.arrow{color:var(--faint);}

/* chips */
.chip-label{color:var(--faint);font-size:.76rem;font-weight:700;text-transform:uppercase;letter-spacing:.08em;margin-bottom:8px;}

[data-testid="stDataFrame"]{border-radius:14px;overflow:hidden;border:1px solid var(--line);box-shadow:0 1px 3px rgba(0,0,0,0.01);}
[data-testid="stMetric"]{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:16px 18px;box-shadow:0 1px 2px rgba(0,0,0,0.01);}
.stTabs [data-baseweb="tab-list"]{gap:8px;}
.stTabs [data-baseweb="tab"]{border-radius:10px;padding:8px 20px;font-weight:600;transition:all 0.15s ease;}
.stTabs [data-baseweb="tab"]:hover { background:#f1f5f9; color:var(--accent); }
.stTabs [aria-selected="true"] { background:var(--accent-soft)!important; color:var(--accent)!important; }

/* Scroll panes for split cockpit layout */
.dash-pane {
  max-height: 84vh;
  overflow-y: auto;
  padding-right: 12px;
}
.chat-pane {
  max-height: 75vh;
  overflow-y: auto;
  padding-left: 12px;
  padding-right: 12px;
  display: flex;
  flex-direction: column;
}
::-webkit-scrollbar {
  width: 6px;
  height: 6px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #cbd5e1;
  border-radius: 3px;
}
::-webkit-scrollbar-thumb:hover {
  background: #94a3b8;
}

/* Centered chatbot welcome card */
.welcome-container {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: 40px 30px;
  border: 1px dashed var(--line);
  border-radius: 20px;
  background: var(--panel);
  margin-top: 40px;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.01);
}
.welcome-title {
  font-size: 1.7rem;
  font-weight: 800;
  color: var(--ink);
  letter-spacing: -0.03em;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.welcome-title .uny { color: var(--ink); }
.welcome-title .brands { color: #6366f1; }
.welcome-desc {
  font-size: 0.94rem;
  color: var(--muted);
  max-width: 480px;
  line-height: 1.5;
  margin-bottom: 24px;
}
.welcome-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
}

/* Floating Live Stats Bar */
.live-stats-bar {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 6px 14px;
  font-size: 0.8rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,0.02);
}
.live-stats-bar .metric-item {
  display: flex;
  align-items: center;
  gap: 4px;
}
.live-stats-bar .green { color: var(--green); }
.live-stats-bar .red { color: var(--red); }
.live-stats-bar .amber { color: var(--amber); }
</style>
""", unsafe_allow_html=True)

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
        
        # Ensure correct types for boolean flags and replace all NaN values with None
        for d in decisions:
            for k, v in d.items():
                if pd.isna(v):
                    d[k] = None
            for bool_col in ["changed", "applied", "requires_human_review", "conflict", "escalated"]:
                if bool_col in d:
                    d[bool_col] = bool(d[bool_col])
                    
        df = _load(input_path)
        rows_by_sku = {str(r["SKU"]): r.to_dict() for _, r in df.iterrows()}
        
        st.session_state.result = {
            "run_date": run_date,
            "df": df,
            "decisions": decisions,
            "memory": mem,
            "learn_notes": mem.get("last_learn_notes", ["Loaded last run from disk."]),
            "changed": last_run["n_changed"],
            "applied": last_run["n_applied"],
            "flagged": last_run["n_flagged"],
        }
        st.session_state.decisions = decisions
        st.session_state.rows_by_sku = rows_by_sku
        st.session_state.current_data = df.to_dict("records")
        st.session_state.run_date = run_date
        
        if mem.get("last_input_filename"):
            st.session_state.uploaded_filename = mem.get("last_input_filename")
            
        return True
    except Exception as e:
        print(f"Error loading last run state: {e}")
        return False


mem = load_memory()

# --------------------------------------------------------------------------
# Session state
# --------------------------------------------------------------------------
if "chatbot" not in st.session_state:
    st.session_state.chatbot = ChatbotAgent()
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_actions" not in st.session_state:
    st.session_state.pending_actions = []
if "chat_prefill" not in st.session_state:
    st.session_state.chat_prefill = ""

# Auto-restore state on startup if not already loaded
if "decisions" not in st.session_state:
    load_last_run_state()

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _file_metrics_compare(current_df: pd.DataFrame) -> dict:
    """Compare loaded DataFrame metrics against master_sku_data.xlsx as the baseline."""
    try:
        baseline_df = _load("master_sku_data.xlsx")
        
        # Calculate comparison metrics
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
        
        return {
            "current_rev": current_rev, "baseline_rev": baseline_rev, "rev_diff": rev_diff, "rev_pct": rev_pct,
            "current_price": current_price, "baseline_price": baseline_price, "price_diff": price_diff, "price_pct": price_pct,
            "current_spend": current_spend, "baseline_spend": baseline_spend, "spend_diff": spend_diff, "spend_pct": spend_pct,
            "has_comparison": True
        }
    except Exception as e:
        print(f"Error comparing file metrics: {e}")
        return {"has_comparison": False}

_INTENT_COLOR = {
    "RAISE_PRICE": "green", "RAISE_PRICE_SLIGHTLY": "green", "DROP_PRICE": "red",
    "HOLD_PRICE": "gray", "INCREASE_BIDS": "green", "LOWER_BIDS": "amber",
    "PAUSE_ADS": "red", "MAINTAIN_ADS": "gray",
}
_BRAIN_CLASS = {"llm": "brain-llm", "rules": "brain-rules", "rules_fallback": "brain-fallbk"}
_BRAIN_LABEL = {"llm": "LLM", "rules": "RULES", "rules_fallback": "FALLBACK"}


def pill(text: str) -> str:
    return f'<span class="pill pill-{_INTENT_COLOR.get(text, "gray")}">{text}</span>'


def brain_badge(decided_by: str) -> str:
    cls = _BRAIN_CLASS.get(decided_by, "brain-rules")
    label = _BRAIN_LABEL.get(decided_by, str(decided_by).upper())
    return f'<span class="pill {cls}">{label}</span>'


def conf_bar(conf: float) -> str:
    pct = max(0.0, min(1.0, conf or 0.0))
    return (
        f'<span class="conf-wrap">'
        f'<span class="conf-track"><span class="conf-fill" style="width:{pct*100:.0f}%"></span></span>'
        f'{pct*100:.0f}%</span>'
    )


def kpi_row(items) -> None:
    cards = "".join(
        f'<div class="kpi"><div class="label">{lab}</div>'
        f'<div class="value {cls}">{val}</div></div>'
        for lab, val, cls in items
    )
    st.markdown(f'<div class="kpi-row">{cards}</div>', unsafe_allow_html=True)


def _policy_df(mem: dict) -> pd.DataFrame:
    rows = [{"situation": b, "action": a, "times tried": s["count"],
             "avg reward ($)": round(s["avg_reward"], 2)}
            for b, acts in mem.get("policy", {}).items() for a, s in acts.items()]
    df = pd.DataFrame(rows)
    return df.sort_values("avg reward ($)", ascending=False) if not df.empty else df


def _impact(decisions, rows_by_sku):
    """Static (pre-elasticity) profit + revenue delta over CHANGED rows, using the
    SAME profit_proxy the RL reward uses (constant volume before/after by design)."""
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
    return profit_delta, revenue_delta, avg_price_delta


def reasoning_line(d: dict) -> str:
    """A non-empty, human-readable one-liner for EVERY row (rules and llm)."""
    notes = str(d.get("manager_notes") or "").strip()
    if notes.lower() == "nan":
        notes = ""
    price_r = str(d.get("price_reason") or "").strip()
    if price_r.lower() == "nan":
        price_r = ""
    ad_r = str(d.get("ad_reason") or "").strip()
    if ad_r.lower() == "nan":
        ad_r = ""
    parts = []
    if d.get("decided_by") == "llm" and notes:
        parts.append(notes)
    if price_r:
        parts.append(price_r)
    if d.get("ad_intent") not in ("MAINTAIN_ADS", None) and ad_r:
        parts.append(ad_r)
    if d.get("decided_by") != "llm" and notes:
        parts.append(notes)
    seen, uniq = set(), []
    for p in parts:
        if p and p not in seen:
            seen.add(p)
            uniq.append(p)
    return " · ".join(uniq) or f"{d.get('price_intent', 'HOLD_PRICE')} (deterministic rule)"


def _scatter(decisions):
    sdf = pd.DataFrame([
        {"SKU": d["sku"], "Old price": d["old_price"], "New price": d["new_price"],
         "Move": ("Raise" if (d["new_price"] or 0) > (d["old_price"] or 0)
                  else "Drop" if (d["new_price"] or 0) < (d["old_price"] or 0)
                  else "Hold"),
         "Brain": d.get("decided_by", "rules"), "Why": reasoning_line(d)}
        for d in decisions
        if d.get("old_price") is not None and d.get("new_price") is not None
    ])
    if sdf.empty:
        return None
    lo = float(sdf[["Old price", "New price"]].min().min()) * 0.95
    hi = float(sdf[["Old price", "New price"]].max().max()) * 1.05
    diag = alt.Chart(pd.DataFrame({"x": [lo, hi], "y": [lo, hi]})).mark_line(
        strokeDash=[5, 5], color="#d4d4dc").encode(x="x", y="y")
    pts = alt.Chart(sdf).mark_circle(size=95, opacity=0.85).encode(
        x=alt.X("Old price", scale=alt.Scale(domain=[lo, hi]), title="Old price ($)"),
        y=alt.Y("New price", scale=alt.Scale(domain=[lo, hi]), title="New price ($)"),
        color=alt.Color("Move", scale=alt.Scale(
            domain=["Raise", "Drop", "Hold"],
            range=["#0f9d58", "#d23f3f", "#9ca3af"]),
            legend=alt.Legend(orient="top-right", title=None)),
        tooltip=["SKU", "Old price", "New price", "Move", "Brain", "Why"])
    return ((diag + pts).properties(height=380)
            .configure_view(strokeWidth=0)
            .configure_axis(grid=True, gridColor="#f0f0f3", domainColor="#e5e5ea",
                            tickColor="#e5e5ea", labelColor="#6b7280",
                            titleColor="#6b7280", titleFontWeight="normal")
            .configure_legend(labelColor="#374151"))


def _run_summary_md(result, rows_by_sku) -> str:
    """A first-person summary the manager posts to the chat after a run."""
    decisions = result["decisions"]
    changed = sum(1 for d in decisions if d["changed"])
    applied = sum(1 for d in decisions if d["applied"])
    flagged = sum(1 for d in decisions if d["requires_human_review"])
    n_llm = sum(1 for d in decisions if d.get("decided_by") == "llm")
    n_rules = len(decisions) - n_llm
    profit_delta, revenue_delta, avg_price_delta = _impact(decisions, rows_by_sku)

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


def _do_run(input_path: str) -> None:
    uploaded_name = st.session_state.get("uploaded_filename")
    with st.spinner("Manager is analysing your catalogue — calling the LLM only on "
                    "escalated SKUs. This can take a moment."):
        result = run_pipeline(input_path, input_filename=uploaded_name)
    rows_by_sku = {str(r["SKU"]): r.to_dict() for _, r in result["df"].iterrows()}
    st.session_state.result = result
    st.session_state.decisions = result["decisions"]
    st.session_state.rows_by_sku = rows_by_sku
    st.session_state.current_data = result["df"].to_dict("records")
    st.session_state.run_date = result["run_date"]
    # the manager reports back in the chat
    st.session_state.messages.append(
        {"role": "assistant", "content": _run_summary_md(result, rows_by_sku)})
    st.session_state.just_ran = True


def render_data_controls():
    """Renders the file uploader and Run button inline. Returns input_path and whether Run was clicked."""
    input_path = DEFAULT_INPUT
    default_index = 0
    if "result" in st.session_state:
        last_path = st.session_state.result.get("memory", {}).get("last_input_path", "")
        if last_path == "uploaded_input.csv" or (not last_path and os.path.exists("uploaded_input.csv")):
            default_index = 1
            
    c_src, c_run = st.columns([3, 1])
    with c_src:
        src = st.radio("Input source", ["master_sku_data.xlsx", "Upload a CSV"],
                       index=default_index, horizontal=True, key="data_src_radio")
        if src == "Upload a CSV":
            if os.path.exists("uploaded_input.csv"):
                input_path = "uploaded_input.csv"
            up = st.file_uploader("Upload next-day SKU CSV", type=["csv"], key="catalog_file_uploader")
            if up is not None:
                input_path = "uploaded_input.csv"
                st.session_state.uploaded_filename = up.name
                with open(input_path, "wb") as fh:
                    fh.write(up.getbuffer())
                st.caption(f"✓ Using uploaded CSV: {up.name}")
            elif os.path.exists("uploaded_input.csv"):
                uploaded_name = st.session_state.get("uploaded_filename") or "uploaded_input.csv"
                st.caption(f"✓ Using existing uploaded CSV: {uploaded_name}")
        else:
            st.caption("Using baseline master_sku_data.xlsx catalog")
            
    with c_run:
        st.markdown("<div style='height:24px;'></div>", unsafe_allow_html=True)
        run_clicked = st.button("Run ▸", type="primary", use_container_width=True, key="catalog_run_btn")
        
    return input_path, run_clicked


def check_decisions_loaded() -> bool:
    if "decisions" not in st.session_state:
        st.info("No active catalog optimization run found. Please select your catalog and click **Run ▸** on the **Copilot Chat** page to populate the cockpit.")
        return False
    return True


def render_chat_view():
    st.markdown('<div class="intro"><h1>AI Copilot</h1><p>Talk to the catalog optimization agent. Review decisions, request rationale, or ask policy questions.</p></div>', unsafe_allow_html=True)
    
    decisions = st.session_state.get("decisions")
    
    # 1. Floating live metrics bar on top right if decisions exist
    if decisions:
        profit_delta, revenue_delta, avg_price_delta = _impact(decisions, st.session_state.rows_by_sku)
        flagged_count = len([d for d in decisions if d["requires_human_review"]])
        
        st.markdown(
            f'<div class="live-stats-bar">'
            f'<div class="metric-item">🟢 Profit Impact: <b class="green">${profit_delta:+,.0f}</b></div>'
            f'<div class="metric-item">🔵 Revenue Impact: <b class="blue">${revenue_delta:+,.0f}</b></div>'
            f'<div class="metric-item">🟡 Awaiting Review: <b class="amber">{flagged_count}</b></div>'
            f'</div>',
            unsafe_allow_html=True
        )
        st.markdown("<br>", unsafe_allow_html=True)
        
        with st.expander("Catalog Input & Execution Panel", expanded=False):
            input_path, run_clicked = render_data_controls()
            if run_clicked:
                _do_run(input_path)
                st.rerun()
    else:
        # Centered welcome container
        st.markdown(
            '<div class="welcome-container">'
            '<div class="welcome-title"><span class="uny">uny</span><span class="brands">brands</span> Copilot</div>'
            '<div class="welcome-desc">Welcome to your Amazon Catalog Optimization Assistant. Connect a catalog data source and execute the agent to start.</div>'
            '</div>',
            unsafe_allow_html=True
        )
        st.markdown("<br>", unsafe_allow_html=True)
        input_path, run_clicked = render_data_controls()
        if run_clicked:
            _do_run(input_path)
            st.rerun()
        return

    # Chat messages list
    st.markdown('<div class="chat-pane">', unsafe_allow_html=True)
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    st.markdown('</div>', unsafe_allow_html=True)
    
    # Suggestions chips
    st.markdown('<div class="chip-label">Ask Copilot</div>', unsafe_allow_html=True)
    chips = [
        "Why did you make your most impactful price change?",
        "Which calls came from your RL memory vs the LLM?",
        "What has your memory learned so far?",
        "Which SKUs did you flag for me, and why?",
        "Summarise today's pricing changes by category.",
    ]
    chip_rows = [chips[:3], chips[3:]]
    for row_idx, chip_row in enumerate(chip_rows):
        chip_cols = st.columns(len(chip_row))
        for i, (col, q) in enumerate(zip(chip_cols, chip_row)):
            if col.button(q[:26] + "…" if len(q) > 26 else q, key=f"chat_chip_{row_idx}_{i}", use_container_width=True):
                st.session_state.chat_prefill = q
                
    st.divider()

    # Pending actions proposed by chat Copilot
    if st.session_state.pending_actions:
        st.warning("Proposed actions awaiting your approval")
        for i, a in enumerate(list(st.session_state.pending_actions)):
            col1, col2, col3 = st.columns([4, 1, 1])
            col1.write(f"`{a.get('action')}` on **{a.get('sku')}** → {a.get('new_value')}")
            if col2.button("Approve", key=f"chat_ap_{i}", type="primary"):
                sku = str(a.get("sku"))
                price = a.get("new_value") if a.get("action") == "CHANGE_PRICE" else None
                spend = a.get("new_value") if a.get("action") == "CHANGE_AD_SPEND" else None
                wrote = True
                if os.path.exists(UPDATED_MASTER_CSV):
                    upd = pd.read_csv(UPDATED_MASTER_CSV)
                    m = upd["SKU"].astype(str) == sku
                    if price is not None:
                        upd.loc[m, "Current_Price"] = price
                    if spend is not None:
                        upd.loc[m, "Ad_Spend_7D"] = spend
                    wrote = _safe_to_csv(upd, UPDATED_MASTER_CSV)
                if wrote:
                    st.session_state.pending_actions.pop(i)
                    st.rerun()
                else:
                    st.error(f"Couldn't write {UPDATED_MASTER_CSV} — close it in Excel.")
            if col3.button("Reject", key=f"chat_rj_{i}"):
                st.session_state.pending_actions.pop(i)
                st.rerun()
        st.divider()
        
    prefill = st.session_state.get("chat_prefill", "")
    if prefill:
        st.session_state.chat_prefill = ""
    user_input = st.chat_input("Ask about decisions, catalog changes, or policy…")
    prompt = prefill or user_input
    
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        with st.spinner("Thinking…"):
            resp = st.session_state.chatbot.handle_message(
                prompt,
                current_data=st.session_state.current_data,
                decisions=st.session_state.decisions,
                policy=mem.get("policy", {}),
                runs=mem.get("runs", []),
            )
        reply = resp.get("response", "I couldn't generate a response.")
        st.session_state.messages.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.markdown(reply)
            if st.session_state.get("run_date"):
                st.caption(f"Run: {st.session_state.run_date}")
        if resp.get("proposed_actions"):
            st.session_state.pending_actions.extend(resp["proposed_actions"])
        st.rerun()


def render_dashboard_view():
    if not check_decisions_loaded():
        return
        
    st.markdown('<div class="intro"><h1>Dashboard</h1><p>Business impact analysis, price movement chart, and decisions explainability ledger.</p></div>', unsafe_allow_html=True)
    
    decisions = st.session_state.decisions
    changed = sum(1 for d in decisions if d["changed"])
    flagged = [d for d in decisions if d["requires_human_review"]]
    applied = sum(1 for d in decisions if d["applied"])
    n_llm   = sum(1 for d in decisions if d.get("decided_by") == "llm")
    n_rules = len(decisions) - n_llm

    st.markdown(
        f'<div class="statusline">✓ Analysed <b>{len(decisions)}</b> SKUs at '
        f'{st.session_state.run_date} — <b>{n_llm}</b> LLM, <b>{n_rules}</b> rules.</div>', 
        unsafe_allow_html=True
    )

    profit_delta, revenue_delta, avg_price_delta = _impact(decisions, st.session_state.rows_by_sku)
    kpi_row([
        ("SKUs changed", changed, ""),
        ("Avg price Δ", f"{avg_price_delta:+.1%}", ""),
        ("Proj. profit impact", f"${profit_delta:+,.0f}", "green" if profit_delta >= 0 else "red"),
        ("Proj. revenue impact", f"${revenue_delta:+,.0f}", "blue"),
        ("Awaiting review", len(flagged), "amber"),
    ])
    st.caption("Static projection (constant volume). Real price elasticity is learned across runs by the RL loop.")

    chart = _scatter(decisions)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)
        st.caption("Above the dashed line = price raised · below = price dropped · hover any point for details.")

    # Explainability ledger
    st.markdown("## Explainability ledger")
    ddf = pd.DataFrame(decisions)
    ddf["Agent Reasoning"] = [reasoning_line(d) for d in decisions]
    f1, f2, f3, f4 = st.columns(4)
    cat     = f1.multiselect("Category", sorted(ddf["category"].dropna().unique()), key="dash_cat_sel")
    pintent = f2.multiselect("Price intent", sorted(ddf["price_intent"].dropna().unique()), key="dash_pr_sel")
    brain   = f3.multiselect("Brain", sorted(ddf["decided_by"].dropna().unique()) if "decided_by" in ddf else [], key="dash_br_sel")
    status  = f4.selectbox("Status", ["all", "applied", "flagged", "unchanged"], key="dash_st_sel")

    view = ddf.copy()
    if cat:
        view = view[view["category"].isin(cat)]
    if pintent:
        view = view[view["price_intent"].isin(pintent)]
    if brain:
        view = view[view["decided_by"].isin(brain)]
    if status == "applied":
        view = view[view["applied"]]
    elif status == "flagged":
        view = view[view["requires_human_review"]]
    elif status == "unchanged":
        view = view[~view["changed"]]

    cols_show = [c for c in [
        "sku", "product_name", "category", "old_price", "new_price",
        "price_intent", "ad_intent", "decided_by", "confidence", "Agent Reasoning",
    ] if c in view.columns]
    st.dataframe(
        view[cols_show], use_container_width=True, height=360, hide_index=True,
        column_config={
            "sku": st.column_config.TextColumn("SKU"),
            "product_name": st.column_config.TextColumn("Product"),
            "category": st.column_config.TextColumn("Category"),
            "price_intent": st.column_config.TextColumn("Price move"),
            "ad_intent": st.column_config.TextColumn("Ad move"),
            "decided_by": st.column_config.TextColumn("Brain"),
            "Agent Reasoning": st.column_config.TextColumn("Agent reasoning", width="large"),
            "old_price": st.column_config.NumberColumn("Old $", format="$%.2f"),
            "new_price": st.column_config.NumberColumn("New $", format="$%.2f"),
            "confidence": st.column_config.ProgressColumn(
                "Conf.", min_value=0.0, max_value=1.0, format="%.0f%%"),
        })


def render_manager_view():
    st.markdown('<div class="intro"><h1>Manager Brain</h1><p>Agent cascading tiers configuration and learned reinforcement learning policies.</p></div>', unsafe_allow_html=True)
    
    st.markdown("### Tiered Cascade Framework")
    st.markdown(
        "Our catalog manager relies on a Tiered cascading orchestrator to optimize decisions:\n"
        "- **Tier 1 (Deterministic Rules):** Safe clamps enforcing baseline floors and caps.\n"
        "- **Tier 2 (RL Policy):** Reinforcement-learning bandit model that matches situations and evaluates reward histories.\n"
        "- **Tier 3 (LLM cascades):** Advanced LLM arbitration invoked on conflicting or complex pricing calls.\n"
        "- **Gate (HITL Triage):** User approval checks for high-exposure catalog decisions."
    )
    
    st.markdown("### What the Manager has Learned")
    pdf = _policy_df(mem)
    if pdf.empty:
        st.info("No reinforcement learning policy has been formed yet. The next execution will evaluate yesterday's choices and populate memory.")
    else:
        st.dataframe(pdf, use_container_width=True, height=360, hide_index=True)


def render_data_view():
    if not check_decisions_loaded():
        return
        
    st.markdown('<div class="intro"><h1>Data & History</h1><p>Compare catalog uploads, track run history, and download decisions datasets.</p></div>', unsafe_allow_html=True)
    
    df = st.session_state.result["df"]
    current_input_name = st.session_state.get("uploaded_filename") or "master_sku_data.xlsx"
    st.markdown(f"### Currently Loaded Catalog: **{current_input_name}**")
    
    metrics_comp = _file_metrics_compare(df)
    if metrics_comp.get("has_comparison"):
        c_rev_col, c_price_col, c_spend_col = st.columns(3)
        c_rev_col.metric(
            "File Total Revenue", 
            f"${metrics_comp['current_rev']:,.0f}", 
            f"{metrics_comp['rev_pct']:+.1%} vs master baseline"
        )
        c_price_col.metric(
            "File Average Price", 
            f"${metrics_comp['current_price']:.2f}", 
            f"{metrics_comp['price_pct']:+.1%} vs master baseline"
        )
        c_spend_col.metric(
            "File Ad Spend", 
            f"${metrics_comp['current_spend']:,.0f}", 
            f"{metrics_comp['spend_pct']:+.1%} vs master baseline"
        )
        
    st.markdown("### Execution Run History")
    if mem.get("runs"):
        st.dataframe(pd.DataFrame(mem["runs"]), use_container_width=True, height=200, hide_index=True)
    else:
        st.caption("No historical runs yet.")
        
    st.markdown("### Download Catalog Reports")
    e1, e2 = st.columns(2)
    if os.path.exists(DECISIONS_CSV):
        e1.download_button("Decisions report (CSV)", open(DECISIONS_CSV, "rb"),
                           file_name=DECISIONS_CSV, use_container_width=True, key="dl_dec_csv")
    if os.path.exists(UPDATED_MASTER_CSV):
        e2.download_button("Updated master (CSV)", open(UPDATED_MASTER_CSV, "rb"),
                           file_name=UPDATED_MASTER_CSV, use_container_width=True, key="dl_mast_csv")


def render_review_view():
    if not check_decisions_loaded():
        return
        
    decisions = st.session_state.decisions
    flagged = [d for d in decisions if d["requires_human_review"]]
    
    st.markdown(f'<div class="intro"><h1>Review Queue</h1><p>Human-in-the-loop review queue containing <b>{len(flagged)}</b> pending SKU optimization decisions.</p></div>', unsafe_allow_html=True)
    
    if not flagged:
        st.success("All clear — there are currently no pending decisions in the queue.")
        return
        
    for d in flagged:
        sku = str(d["sku"])
        decided_by = d.get("decided_by", "rules")
        conf = d.get("confidence") or 0.0
        conflict = d.get("conflict", False)
        escalated = d.get("escalated", False)

        with st.container(border=True):
            info, btns = st.columns([7, 2])
            with info:
                price_html = (f'${d["old_price"]} <span class="arrow">→</span> '
                              f'<b>${d["new_price"]}</b> {pill(d["price_intent"])}')
                conflict_html = (' &nbsp;<span class="pill pill-amber">CONFLICT</span>'
                                 if conflict else "")
                st.markdown(
                    f'<div class="rc-title">{sku} · {d["product_name"]}</div>'
                    f'<div class="rc-meta">{d["category"]} · objective: <b>{d["objective"]}</b></div>'
                    f'<div class="rc-meta">Price: {price_html} &nbsp;·&nbsp; '
                    f'Ads: {pill(d["ad_intent"])} ({d["bid_change_pct"]:+.0%})</div>'
                    f'<div class="rc-meta">Brain: {brain_badge(decided_by)} &nbsp;·&nbsp; '
                    f'Confidence: {conf_bar(conf)}{conflict_html}</div>'
                    f'<div class="rc-reason">⚠ {d["review_reason"]}</div>',
                    unsafe_allow_html=True)
                if escalated and d.get("triage_reason"):
                    st.markdown(f'<div class="rc-notes">↑ Escalated: {d["triage_reason"]}</div>',
                                unsafe_allow_html=True)

                with st.expander("See full reasoning"):
                    if decided_by == "llm":
                        if d.get("manager_notes"):
                            st.markdown(f"**LLM rationale:** {d['manager_notes']}")
                        if d.get("rejected_reason"):
                            st.markdown(f"**What was rejected:** {d['rejected_reason']}")
                    else:
                        st.markdown("Handled by **deterministic rules** — no LLM call.")
                        if d.get("manager_notes"):
                            st.markdown(f"**Manager notes:** {d['manager_notes']}")
                    if d.get("price_reason"):
                        st.markdown(f"**Pricing agent:** {d['price_reason']}")
                    if d.get("ad_reason"):
                        st.markdown(f"**Ads agent:** {d['ad_reason']}")

            with btns:
                if st.button("Approve", key=f"queue_ap_{sku}", type="primary",
                             use_container_width=True):
                    if approve_decision(d, st.session_state.rows_by_sku[sku],
                                        st.session_state.run_date):
                        d.update(applied=True, requires_human_review=False,
                                 review_reason="approved by human")
                        _safe_to_csv(pd.DataFrame(st.session_state.decisions), DECISIONS_CSV)
                        st.rerun()
                    else:
                        st.error(f"Couldn't write {UPDATED_MASTER_CSV} — close it in Excel.")
                if st.button("Reject", key=f"queue_rj_{sku}", use_container_width=True):
                    reject_decision(d)
                    d.update(requires_human_review=False, changed=False,
                             review_reason="rejected by human")
                    _safe_to_csv(pd.DataFrame(st.session_state.decisions), DECISIONS_CSV)
                    st.rerun()


# ==========================================================================
# SIDEBAR BRanding & Controls (unybrands theme)
# ==========================================================================
with st.sidebar:
    st.markdown('<div class="sidebar-logo"><span class="uny">uny</span><span class="brands">brands</span></div>', unsafe_allow_html=True)
    st.markdown('<div class="sidebar-sub">AMAZON CATALOG COCKPIT</div>', unsafe_allow_html=True)
    st.markdown('<br>', unsafe_allow_html=True)
            
    st.markdown("<hr style='border-color:#1e293b;'><div style='height:10px;'></div>", unsafe_allow_html=True)
    st.caption("Settings")
    if st.button("Reset Learning", key="reset_learning_btn", use_container_width=True):
        if os.path.exists(MEMORY_PATH):
            os.remove(MEMORY_PATH)
        if os.path.exists(DECISIONS_CSV):
            os.remove(DECISIONS_CSV)
        if os.path.exists("uploaded_input.csv"):
            os.remove("uploaded_input.csv")
        for k in ["result", "decisions", "rows_by_sku", "current_data", "run_date", "uploaded_filename"]:
            if k in st.session_state:
                del st.session_state[k]
        st.success("Memory & last run reset.")
        st.rerun()


# ==========================================================================
# PAGE VIEW DISPATCHER (Split-Screen Cockpit)
# ==========================================================================
decisions = st.session_state.get("decisions", [])
flagged = [d for d in decisions if d["requires_human_review"]]
review_label = f"🔔 Review Queue ({len(flagged)})" if flagged else "🔔 Review Queue"

col_dash, col_chat = st.columns([1.35, 1])

with col_dash:
    st.markdown('<div class="dash-pane">', unsafe_allow_html=True)
    tab_dash, tab_brain, tab_data, tab_review = st.tabs([
        "📊 Dashboard", "🧠 Manager Brain", "📁 Data & History", review_label
    ])
    with tab_dash:
        render_dashboard_view()
    with tab_brain:
        render_manager_view()
    with tab_data:
        render_data_view()
    with tab_review:
        render_review_view()
    st.markdown('</div>', unsafe_allow_html=True)

with col_chat:
    render_chat_view()
