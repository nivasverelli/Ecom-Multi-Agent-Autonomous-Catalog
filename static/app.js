// unybrands SaaS Cockpit Client logic

let appState = {
  decisions_loaded: false,
  run_date: "",
  uploaded_filename: "",
  metrics: {
    changed: 0,
    applied: 0,
    flagged: 0,
    n_llm: 0,
    n_rules: 0,
    profit_delta: 0,
    revenue_delta: 0,
    avg_price_delta: 0
  },
  decisions: [],
  runs: [],
  policy: [],
  pending_actions: [],
  messages: [],
  has_comparison: false,
  comparison: {},
  has_uploaded_csv: false
};

let currentTab = "chat";
let uploadFile = null;

// UI Elements
const navItems = document.querySelectorAll(".nav-item");
const pageViews = document.querySelectorAll(".page-view");
const reviewBadge = document.getElementById("review-badge");

// Initial Setup
document.addEventListener("DOMContentLoaded", () => {
  setupNavigation();
  setupChatHandlers();
  setupRunControls();
  setupDashboardFilters();
  setupResetButton();
  
  // Initial load
  fetchState(true);
});

// --------------------------------------------------------------------------
// Navigation Router
// --------------------------------------------------------------------------
function setupNavigation() {
  navItems.forEach(item => {
    item.addEventListener("click", () => {
      const pageId = item.getAttribute("data-page");
      
      // Update sidebar visual state
      navItems.forEach(nav => nav.classList.remove("active"));
      item.classList.add("active");
      
      // Update visible view
      pageViews.forEach(view => {
        view.classList.remove("active");
        if (view.id === `view-${pageId}`) {
          view.classList.add("active");
        }
      });
      
      currentTab = pageId;
      renderCurrentPage();
    });
  });
}

function renderCurrentPage() {
  if (currentTab === "chat") {
    renderChatView();
  } else if (currentTab === "dashboard") {
    renderDashboardView();
  } else if (currentTab === "brain") {
    renderBrainView();
  } else if (currentTab === "data") {
    renderDataView();
  } else if (currentTab === "review") {
    renderReviewView();
  }
}

// --------------------------------------------------------------------------
// API Client Calls
// --------------------------------------------------------------------------
async function fetchState(initial = false) {
  try {
    const res = await fetch("/api/state");
    const data = await res.json();
    appState = data;
    
    // Check if decisions exist
    appState.decisions_loaded = appState.decisions && appState.decisions.length > 0;
    
    // Update badge count
    const flagged = appState.decisions ? appState.decisions.filter(d => d.requires_human_review) : [];
    if (flagged.length > 0) {
      reviewBadge.style.display = "inline-block";
      reviewBadge.textContent = flagged.length;
    } else {
      reviewBadge.style.display = "none";
    }
    
    // Auto route on initial boot if decisions exist
    if (initial) {
      if (appState.decisions_loaded) {
        document.getElementById("welcome-panel").style.display = "none";
        document.getElementById("active-run-controls").style.display = "block";
        document.getElementById("live-stats").style.display = "flex";
        document.getElementById("chat-messages").style.display = "flex";
        document.getElementById("chat-chips-box").style.display = "block";
        document.getElementById("chat-input-wrapper").style.display = "flex";
      } else {
        document.getElementById("welcome-panel").style.display = "flex";
        document.getElementById("active-run-controls").style.display = "none";
        document.getElementById("live-stats").style.display = "none";
        document.getElementById("chat-messages").style.display = "none";
        document.getElementById("chat-chips-box").style.display = "none";
        document.getElementById("chat-input-wrapper").style.display = "none";
      }
    }
    
    renderCurrentPage();
  } catch (err) {
    console.error("Error fetching state:", err);
  }
}

// --------------------------------------------------------------------------
// 1. Copilot Chat View Rendering
// --------------------------------------------------------------------------
function setupChatHandlers() {
  const chatInput = document.getElementById("chat-input-field");
  const sendBtn = document.getElementById("btn-send-message");
  
  const sendMessage = async () => {
    const text = chatInput.value.trim();
    if (!text) return;
    
    chatInput.value = "";
    
    // Append user message instantly
    appState.messages.push({ role: "user", content: text });
    renderChatMessages();
    scrollChatToBottom();
    
    // Show spinner
    const feed = document.getElementById("chat-messages");
    const spinner = document.createElement("div");
    spinner.className = "chat-bubble assistant";
    spinner.id = "chat-spinner-bubble";
    spinner.innerHTML = `
      <div class="bubble-avatar">🧠</div>
      <div class="bubble-content">
        <p>Thinking...</p>
      </div>
    `;
    feed.appendChild(spinner);
    scrollChatToBottom();
    
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ prompt: text })
      });
      await res.json();
      
      // Remove spinner and reload state
      const spin = document.getElementById("chat-spinner-bubble");
      if (spin) spin.remove();
      
      await fetchState();
      scrollChatToBottom();
    } catch (err) {
      console.error("Error sending message:", err);
      const spin = document.getElementById("chat-spinner-bubble");
      if (spin) spin.remove();
    }
  };
  
  sendBtn.addEventListener("click", sendMessage);
  chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") sendMessage();
  });
  
  // Suggestion chips prefill action
  document.querySelectorAll(".chip-item").forEach(chip => {
    chip.addEventListener("click", () => {
      chatInput.value = chip.getAttribute("data-prompt");
      chatInput.focus();
    });
  });
}

function renderChatView() {
  if (!appState.decisions_loaded) {
    document.getElementById("welcome-panel").style.display = "flex";
    document.getElementById("active-run-controls").style.display = "none";
    document.getElementById("live-stats").style.display = "none";
    document.getElementById("chat-messages").style.display = "none";
    document.getElementById("chat-chips-box").style.display = "none";
    document.getElementById("chat-input-wrapper").style.display = "none";
    return;
  }
  
  document.getElementById("welcome-panel").style.display = "none";
  document.getElementById("active-run-controls").style.display = "block";
  document.getElementById("live-stats").style.display = "flex";
  document.getElementById("chat-messages").style.display = "flex";
  document.getElementById("chat-chips-box").style.display = "block";
  document.getElementById("chat-input-wrapper").style.display = "flex";
  
  // Render floating metrics header
  const flagged = appState.decisions.filter(d => d.requires_human_review);
  document.getElementById("live-profit").textContent = formatCurrency(appState.profit_delta);
  document.getElementById("live-profit").className = appState.profit_delta >= 0 ? "green" : "red";
  document.getElementById("live-revenue").textContent = formatCurrency(appState.revenue_delta);
  document.getElementById("live-review").textContent = flagged.length;
  
  renderChatMessages();
  renderPendingActions();
}

function renderChatMessages() {
  const feed = document.getElementById("chat-messages");
  feed.innerHTML = "";
  
  if (!appState.messages || appState.messages.length === 0) {
    // If no messages exist yet, show a welcome statement
    feed.innerHTML = `
      <div class="chat-bubble assistant">
        <div class="bubble-avatar">🧠</div>
        <div class="bubble-content">
          <p>Hi, I am your catalog manager optimization agent. Ask me about SKU changes, execution steps, or details on what I have learned from past run logs!</p>
        </div>
      </div>
    `;
    return;
  }
  
  appState.messages.forEach(msg => {
    const bubble = document.createElement("div");
    bubble.className = `chat-bubble ${msg.role}`;
    
    const avatar = msg.role === "user" ? "👤" : "🧠";
    
    // Markdown replacement converter
    let contentHtml = msg.content
      .replace(/\n/g, "<br>")
      .replace(/\*\*(.*?)\*\*/g, "<strong>$1</strong>")
      .replace(/\*(.*?)\*/g, "<em>$1</em>")
      .replace(/`(.*?)`/g, "<code>$1</code>");
      
    bubble.innerHTML = `
      <div class="bubble-avatar">${avatar}</div>
      <div class="bubble-content">${contentHtml}</div>
    `;
    feed.appendChild(bubble);
  });
}

function scrollChatToBottom() {
  const feed = document.getElementById("chat-messages");
  feed.scrollTop = feed.scrollHeight;
}

function renderPendingActions() {
  const box = document.getElementById("pending-actions-box");
  const list = document.getElementById("pending-actions-list");
  
  if (!appState.pending_actions || appState.pending_actions.length === 0) {
    box.style.display = "none";
    return;
  }
  
  box.style.display = "block";
  list.innerHTML = "";
  
  appState.pending_actions.forEach((act, idx) => {
    const row = document.createElement("div");
    row.className = "pending-row";
    
    const actType = act.action === "CHANGE_PRICE" ? "Price move" : "Ad move";
    const details = `<code>${act.action}</code> on <b>${act.sku}</b> → <b>${act.new_value}</b>`;
    
    row.innerHTML = `
      <div class="pending-details">${details}</div>
      <div class="pending-btns">
        <button class="btn btn-primary btn-sm btn-approve-prop" data-idx="${idx}">Approve</button>
        <button class="btn btn-secondary btn-sm btn-reject-prop" data-idx="${idx}">Reject</button>
      </div>
    `;
    list.appendChild(row);
  });
  
  // Set listeners
  document.querySelectorAll(".btn-approve-prop").forEach(btn => {
    btn.addEventListener("click", async () => {
      const idx = btn.getAttribute("data-idx");
      await fetch("/api/approve_proposed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: idx })
      });
      fetchState();
    });
  });
  
  document.querySelectorAll(".btn-reject-prop").forEach(btn => {
    btn.addEventListener("click", async () => {
      const idx = btn.getAttribute("data-idx");
      await fetch("/api/reject_proposed", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ index: idx })
      });
      fetchState();
    });
  });
}

// Setup input upload triggers and main Run buttons
function setupRunControls() {
  const pickerRadios = document.querySelectorAll('input[name="input_source"]');
  const activePickerRadios = document.querySelectorAll('input[name="active_input_source"]');
  
  const welcomeUploadBox = document.getElementById("csv-upload-box");
  const activeUploadBox = document.getElementById("active-csv-upload-box");
  
  const welcomeFileInput = document.getElementById("catalog-file-input");
  const activeFileInput = document.getElementById("active-catalog-file-input");
  
  const welcomeTrigger = document.getElementById("btn-upload-trigger");
  const activeTrigger = document.getElementById("btn-active-upload-trigger");
  
  const welcomeStatus = document.getElementById("upload-status-text");
  const activeStatus = document.getElementById("active-upload-status-text");
  
  const welcomeRunBtn = document.getElementById("btn-run-optimization");
  const activeRunBtn = document.getElementById("btn-active-run");
  
  const updateSourcePicker = (val, activePanel = false) => {
    const box = activePanel ? activeUploadBox : welcomeUploadBox;
    if (val === "Upload a CSV") {
      box.style.display = "flex";
    } else {
      box.style.display = "none";
    }
  };
  
  pickerRadios.forEach(rad => {
    rad.addEventListener("change", () => updateSourcePicker(rad.value, false));
  });
  
  activePickerRadios.forEach(rad => {
    rad.addEventListener("change", () => updateSourcePicker(rad.value, true));
  });
  
  // Upload triggers
  welcomeTrigger.addEventListener("click", () => welcomeFileInput.click());
  activeTrigger.addEventListener("click", () => activeFileInput.click());
  
  const handleFileUpload = async (file, statusEl) => {
    if (!file) return;
    statusEl.textContent = `Uploading: ${file.name}...`;
    
    const formData = new FormData();
    formData.append("catalog", file);
    
    try {
      const res = await fetch("/api/upload", {
        method: "POST",
        body: formData
      });
      const data = await res.json();
      if (data.success) {
        statusEl.textContent = `✓ Uploaded: ${file.name}`;
      } else {
        statusEl.textContent = `⚠ Upload Error: ${data.error}`;
      }
    } catch (err) {
      statusEl.textContent = `⚠ Failed: ${err}`;
    }
  };
  
  welcomeFileInput.addEventListener("change", () => handleFileUpload(welcomeFileInput.files[0], welcomeStatus));
  activeFileInput.addEventListener("change", () => handleFileUpload(activeFileInput.files[0], activeStatus));
  
  // Pipeline Trigger Functions
  const executePipelineRun = async (sourceVal, btnEl) => {
    const originalText = btnEl.textContent;
    btnEl.disabled = true;
    btnEl.textContent = "Running Analysis Pipeline...";
    
    try {
      const res = await fetch("/api/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: sourceVal })
      });
      const data = await res.json();
      
      btnEl.disabled = false;
      btnEl.textContent = originalText;
      
      if (data.success) {
        // Redraw page
        await fetchState();
      } else {
        alert(`Pipeline Execution Error: ${data.error}`);
      }
    } catch (err) {
      btnEl.disabled = false;
      btnEl.textContent = originalText;
      alert(`API Connection Failed: ${err}`);
    }
  };
  
  welcomeRunBtn.addEventListener("click", () => {
    const selectedSource = document.querySelector('input[name="input_source"]:checked').value;
    executePipelineRun(selectedSource, welcomeRunBtn);
  });
  
  activeRunBtn.addEventListener("click", () => {
    const selectedSource = document.querySelector('input[name="active_input_source"]:checked').value;
    executePipelineRun(selectedSource, activeRunBtn);
  });
}

// --------------------------------------------------------------------------
// 2. Dashboard View Rendering & Interactive Chart
// --------------------------------------------------------------------------
let scatterCanvas = null;

function renderDashboardView() {
  const empty = document.querySelector("#view-dashboard .no-data-msg");
  const content = document.querySelector("#view-dashboard .dashboard-content");
  
  if (!appState.decisions_loaded) {
    empty.style.display = "flex";
    content.style.display = "none";
    return;
  }
  
  empty.style.display = "none";
  content.style.display = "block";
  
  // Run badge title text
  const decisions = appState.decisions;
  const n_llm = decisions.filter(d => d.decided_by === "llm").length;
  const n_rules = decisions.length - n_llm;
  document.getElementById("dash-run-badge").innerHTML = `✓ Analysed <b>${decisions.length}</b> SKUs at <b>${appState.run_date}</b> — <b>${n_llm}</b> LLM, <b>${n_rules}</b> rules.`;
  
  // Render Pi Opportunity Alerts
  renderPiAlerts();
  
  // KPI Metrics
  const changed = decisions.filter(d => d.changed).length;
  const flagged = decisions.filter(d => d.requires_human_review).length;
  
  document.getElementById("dash-changed").textContent = changed;
  document.getElementById("dash-flagged").textContent = flagged;
  document.getElementById("dash-avg-price").textContent = `${appState.avg_price_delta >= 0 ? "+" : ""}${roundTo(appState.avg_price_delta * 100, 1)}%`;
  document.getElementById("dash-profit").textContent = formatCurrency(appState.profit_delta);
  document.getElementById("dash-profit").className = `kpi-val ${appState.profit_delta >= 0 ? "green" : "red"}`;
  document.getElementById("dash-revenue").textContent = formatCurrency(appState.revenue_delta);
  
  // Setup dropdown values dynamically for explainability filters
  populateFilterOptions();
  
  // Render ledger list table
  renderLedgerTable();
  
  // Render Custom HTML5 Canvas Scatter Plot
  setTimeout(drawScatterPlot, 100);
}

function renderPiAlerts() {
  const container = document.getElementById("dash-pi-alerts");
  const listEl = document.getElementById("dash-alerts-list");
  if (!container || !listEl) return;
  
  const decisions = appState.decisions || [];
  const changedDecisions = decisions.filter(d => d.changed);
  
  if (changedDecisions.length === 0) {
    container.style.display = "none";
    return;
  }
  
  // Sort by absolute profit impact descending
  const sorted = [...changedDecisions].sort((a, b) => Math.abs(b.profit_delta || 0) - Math.abs(a.profit_delta || 0));
  const topAlerts = sorted.slice(0, 3);
  
  listEl.innerHTML = "";
  
  topAlerts.forEach(d => {
    let icon = "🟢";
    if (d.profit_delta < 0) {
      icon = d.requires_human_review ? "🟡" : "🔵";
    }
    
    let brainText = "";
    if (d.requires_human_review) {
      brainText = "HITL Queue Action Needed";
    } else if (d.decided_by === "llm") {
      brainText = "Tier 3: LLM Cognitive Arbitration";
    } else if (d.decided_by === "rules" || d.decided_by === "rule") {
      brainText = "Tier 1: Guardrail Clamp";
    } else {
      brainText = "Tier 2: RL Bandit Policy";
    }
    
    const profitText = d.profit_delta >= 0 ? `+$${roundTo(d.profit_delta, 0)}` : `-$${roundTo(Math.abs(d.profit_delta), 0)}`;
    const profitClass = d.profit_delta >= 0 ? "green" : "red";
    
    const item = document.createElement("div");
    item.className = "pi-alert-item";
    item.innerHTML = `
      <span class="pi-alert-icon">${icon}</span>
      <span class="pi-alert-sku">[${d.sku}]</span>
      <span class="pi-alert-text"><b>${d.product_name}</b>: Price optimized from $${d.old_price} to $${d.new_price} by <i>${brainText}</i>.</span>
      <span class="pi-alert-impact ${profitClass}">Impact: ${profitText}</span>
    `;
    listEl.appendChild(item);
  });
  
  container.style.display = "block";
}

function populateFilterOptions() {
  const catSel = document.getElementById("filter-category");
  const intentSel = document.getElementById("filter-intent");
  const brainSel = document.getElementById("filter-brain");
  
  // Store selected options
  const selectedCat = catSel.value;
  const selectedIntent = intentSel.value;
  const selectedBrain = brainSel.value;
  
  // Gather unique values
  const categories = [...new Set(appState.decisions.map(d => d.category).filter(Boolean))].sort();
  const intents = [...new Set(appState.decisions.map(d => d.price_intent).filter(Boolean))].sort();
  const brains = [...new Set(appState.decisions.map(d => d.decided_by).filter(Boolean))].sort();
  
  // Populate Categories
  catSel.innerHTML = '<option value="all">All Categories</option>';
  categories.forEach(c => {
    catSel.innerHTML += `<option value="${c}">${c}</option>`;
  });
  
  // Populate Intents
  intentSel.innerHTML = '<option value="all">All Movements</option>';
  intents.forEach(i => {
    intentSel.innerHTML += `<option value="${i}">${i}</option>`;
  });
  
  // Populate Brains
  brainSel.innerHTML = '<option value="all">All Brains</option>';
  brains.forEach(b => {
    const label = b === "llm" ? "LLM" : b === "rules" ? "RULES" : b.toUpperCase();
    brainSel.innerHTML += `<option value="${b}">${label}</option>`;
  });
  
  // Restore selections if values still exist
  if (categories.includes(selectedCat)) catSel.value = selectedCat;
  if (intents.includes(selectedIntent)) intentSel.value = selectedIntent;
  if (brains.includes(selectedBrain)) brainSel.value = selectedBrain;
}

function setupDashboardFilters() {
  const filters = ["filter-category", "filter-intent", "filter-brain", "filter-status"];
  filters.forEach(id => {
    document.getElementById(id).addEventListener("change", renderLedgerTable);
  });
}

function renderLedgerTable() {
  const tbody = document.getElementById("ledger-table-body");
  tbody.innerHTML = "";
  
  const cat = document.getElementById("filter-category").value;
  const intent = document.getElementById("filter-intent").value;
  const brain = document.getElementById("filter-brain").value;
  const status = document.getElementById("filter-status").value;
  
  let filtered = appState.decisions;
  
  if (cat !== "all") filtered = filtered.filter(d => d.category === cat);
  if (intent !== "all") filtered = filtered.filter(d => d.price_intent === intent);
  if (brain !== "all") filtered = filtered.filter(d => d.decided_by === brain);
  
  if (status === "applied") {
    filtered = filtered.filter(d => d.applied);
  } else if (status === "flagged") {
    filtered = filtered.filter(d => d.requires_human_review);
  } else if (status === "unchanged") {
    filtered = filtered.filter(d => !d.changed);
  }
  
  if (filtered.length === 0) {
    tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;color:var(--text-faint);padding:24px;">No matching SKU records found</td></tr>';
    return;
  }
  
  filtered.forEach(d => {
    const tr = document.createElement("tr");
    
    const sku = d.sku;
    const prod = d.product_name;
    const category = d.category;
    const oldPx = `$${roundTo(d.old_price, 2)}`;
    const newPx = `$${roundTo(d.new_price, 2)}`;
    
    // Format badges
    const movePill = `<span class="pill pill-${getIntentColor(d.price_intent)}">${d.price_intent}</span>`;
    const adPill = `<span class="pill pill-${getIntentColor(d.ad_intent)}">${d.ad_intent || "MAINTAIN_ADS"}</span>`;
    
    // Cascading tiers badges matching style.css classes
    let badgeClass = "badge-rule";
    let badgeLabel = "Rule Guard";
    
    if (d.requires_human_review) {
      badgeClass = "badge-hitl";
      badgeLabel = "HITL Queue";
    } else if (d.decided_by === "llm") {
      badgeClass = "badge-llm";
      badgeLabel = "LLM Engine";
    } else if (d.decided_by === "bandit" || d.decided_by === "rl") {
      badgeClass = "badge-rl";
      badgeLabel = "RL Bandit";
    } else if (d.decided_by === "rules" || d.decided_by === "rule") {
      badgeClass = "badge-rule";
      badgeLabel = "Rule Guard";
    }
    const brainBadge = `<span class="${badgeClass}">${badgeLabel}</span>`;
    
    const confPct = Math.round((d.confidence || 0) * 100);
    const confBar = `
      <div class="conf-wrap">
        <div class="conf-track"><div class="conf-fill" style="width:${confPct}%"></div></div>
        <span>${confPct}%</span>
      </div>
    `;
    
    const reasoning = getReasoningOneLiner(d);
    
    tr.innerHTML = `
      <td><b>${sku}</b></td>
      <td title="${prod}">${prod.length > 24 ? prod.substring(0, 24) + "..." : prod}</td>
      <td>${category}</td>
      <td>${oldPx}</td>
      <td>${newPx}</td>
      <td>${movePill}</td>
      <td>${adPill}</td>
      <td>${brainBadge}</td>
      <td>${confBar}</td>
      <td title="${reasoning}"><i>${reasoning.length > 50 ? reasoning.substring(0, 50) + "..." : reasoning}</i></td>
    `;
    tbody.appendChild(tr);
  });
}

function drawScatterPlot() {
  const canvas = document.getElementById("scatter-chart");
  if (!canvas) return;
  
  const ctx = canvas.getContext("2d");
  const parent = canvas.parentElement;
  
  // Set width dynamically
  canvas.width = parent.clientWidth;
  canvas.height = 380;
  
  const width = canvas.width;
  const height = canvas.height;
  
  // Margins
  const margin = { top: 30, right: 30, bottom: 50, left: 60 };
  
  // Extract points
  const points = appState.decisions.map(d => ({
    sku: d.sku,
    old_price: parseFloat(d.old_price) || 0,
    new_price: parseFloat(d.new_price) || 0,
    brain: d.decided_by || "rules",
    why: getReasoningOneLiner(d),
    intent: d.price_intent
  })).filter(p => p.old_price > 0 && p.new_price > 0);
  
  if (points.length === 0) {
    ctx.clearRect(0, 0, width, height);
    ctx.font = "14px Inter";
    ctx.fillStyle = "#94a3b8";
    ctx.fillText("No price movements available to plot.", width/2 - 100, height/2);
    return;
  }
  
  // Get scale domains
  const minVal = Math.min(...points.map(p => Math.min(p.old_price, p.new_price))) * 0.95;
  const maxVal = Math.max(...points.map(p => Math.max(p.old_price, p.new_price))) * 1.05;
  
  // Scale mapping functions
  const xScale = (val) => margin.left + ((val - minVal) / (maxVal - minVal)) * (width - margin.left - margin.right);
  const yScale = (val) => height - margin.bottom - ((val - minVal) / (maxVal - minVal)) * (height - margin.top - margin.bottom);
  
  ctx.clearRect(0, 0, width, height);
  
  // Draw Grid Lines & Axes borders
  ctx.strokeStyle = "#f0f0f3";
  ctx.lineWidth = 1;
  
  // Draw 5 grid lines
  for (let i = 0; i <= 4; i++) {
    const val = minVal + (i / 4) * (maxVal - minVal);
    const x = xScale(val);
    const y = yScale(val);
    
    // Vertical grid line
    ctx.beginPath();
    ctx.moveTo(x, margin.top);
    ctx.lineTo(x, height - margin.bottom);
    ctx.stroke();
    
    // Horizontal grid line
    ctx.beginPath();
    ctx.moveTo(margin.left, y);
    ctx.lineTo(width - margin.right, y);
    ctx.stroke();
    
    // Labels
    ctx.font = "11px Inter";
    ctx.fillStyle = "#6b7280";
    ctx.textAlign = "right";
    ctx.fillText(`$${Math.round(val)}`, margin.left - 10, y + 4);
    
    ctx.textAlign = "center";
    ctx.fillText(`$${Math.round(val)}`, x, height - margin.bottom + 20);
  }
  
  // Axes Titles
  ctx.font = "12px Inter";
  ctx.fillStyle = "#374151";
  ctx.textAlign = "center";
  ctx.fillText("Old Price ($)", margin.left + (width - margin.left - margin.right) / 2, height - 10);
  
  ctx.save();
  ctx.translate(15, margin.top + (height - margin.top - margin.bottom) / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText("New Price ($)", 0, 0);
  ctx.restore();
  
  // Draw Diagonal Dashed Line (No-move line)
  ctx.strokeStyle = "#cbd5e1";
  ctx.lineWidth = 1.5;
  ctx.setLineDash([5, 5]);
  ctx.beginPath();
  ctx.moveTo(xScale(minVal), yScale(minVal));
  ctx.lineTo(xScale(maxVal), yScale(maxVal));
  ctx.stroke();
  ctx.setLineDash([]); // Reset line dash
  
  // Draw Points
  points.forEach(p => {
    const cx = xScale(p.old_price);
    const cy = yScale(p.new_price);
    
    let color = "#9ca3af"; // Hold
    if (p.new_price > p.old_price) {
      color = "#10b981"; // Raise (Green)
    } else if (p.new_price < p.old_price) {
      color = "#ef4444"; // Drop (Red)
    }
    
    ctx.beginPath();
    ctx.arc(cx, cy, 6, 0, 2 * Math.PI);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 1;
    ctx.stroke();
  });
}

// --------------------------------------------------------------------------
// 3. Manager Brain View Rendering
// --------------------------------------------------------------------------
function renderBrainView() {
  const tbody = document.getElementById("policy-table-body");
  const emptyInfo = document.getElementById("policy-empty-info");
  const table = document.getElementById("policy-table");
  
  tbody.innerHTML = "";
  
  if (!appState.policy || appState.policy.length === 0) {
    emptyInfo.style.display = "block";
    table.style.display = "none";
    return;
  }
  
  emptyInfo.style.display = "none";
  table.style.display = "table";
  
  appState.policy.forEach(row => {
    const tr = document.createElement("tr");
    
    tr.innerHTML = `
      <td><code>${row.situation}</code></td>
      <td><code>${row.action}</code></td>
      <td>${row.times_tried}</td>
      <td class="${row.avg_reward >= 0 ? 'green' : 'red'}" style="font-weight: 700;">
        ${row.avg_reward >= 0 ? '+' : ''}$${row.avg_reward.toFixed(2)}
      </td>
    `;
    tbody.appendChild(tr);
  });
}

// --------------------------------------------------------------------------
// 4. Data & History View Rendering
// --------------------------------------------------------------------------
function renderDataView() {
  const empty = document.querySelector("#view-data .no-data-msg");
  const content = document.querySelector("#view-data .data-content");
  
  if (!appState.decisions_loaded) {
    empty.style.display = "flex";
    content.style.display = "none";
    return;
  }
  
  empty.style.display = "none";
  content.style.display = "block";
  
  // Active catalog title
  const activeFileName = appState.uploaded_filename || "master_sku_data.xlsx";
  document.getElementById("loaded-catalog-title").innerHTML = `Currently Loaded Catalog: <b>${activeFileName}</b>`;
  
  // Baseline comparisons
  const compWidgets = document.getElementById("comparison-widgets");
  if (appState.has_comparison) {
    compWidgets.style.display = "grid";
    const comp = appState.comparison;
    
    // Rev
    document.getElementById("comp-rev").textContent = formatCurrency(comp.current_rev);
    document.getElementById("comp-rev-pct").textContent = `${comp.rev_pct >= 0 ? "+" : ""}${roundTo(comp.rev_pct * 100, 1)}% vs baseline`;
    document.getElementById("comp-rev-pct").className = `pct-change ${comp.rev_pct >= 0 ? "green" : "red"}`;
    
    // Price
    document.getElementById("comp-price").textContent = `$${comp.current_price.toFixed(2)}`;
    document.getElementById("comp-price-pct").textContent = `${comp.price_pct >= 0 ? "+" : ""}${roundTo(comp.price_pct * 100, 1)}% vs baseline`;
    document.getElementById("comp-price-pct").className = `pct-change ${comp.price_pct >= 0 ? "green" : "red"}`;
    
    // Spend
    document.getElementById("comp-spend").textContent = formatCurrency(comp.current_spend);
    document.getElementById("comp-spend-pct").textContent = `${comp.spend_pct >= 0 ? "+" : ""}${roundTo(comp.spend_pct * 100, 1)}% vs baseline`;
    document.getElementById("comp-spend-pct").className = `pct-change ${comp.spend_pct >= 0 ? "red" : "green"}`; // Spend drop is green
  } else {
    compWidgets.style.display = "none";
  }
  
  // Historical runs log
  const tbody = document.getElementById("history-table-body");
  tbody.innerHTML = "";
  
  const runs = appState.memory && appState.memory.runs ? appState.memory.runs : [];
  
  if (runs.length === 0) {
    tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:var(--text-faint);padding:16px;">No runs recorded in logs yet</td></tr>';
    return;
  }
  
  // Render runs in reverse chronological order
  [...runs].reverse().forEach(run => {
    const tr = document.createElement("tr");
    const sourceName = run.input_filename || "master_sku_data.xlsx";
    
    tr.innerHTML = `
      <td><b>${run.date}</b></td>
      <td>${sourceName}</td>
      <td>${run.n_skus || 0}</td>
      <td>${run.n_changed || 0}</td>
      <td>${run.n_applied || 0}</td>
      <td><span class="pill ${run.n_flagged > 0 ? 'pill-amber' : 'pill-gray'}">${run.n_flagged || 0}</span></td>
    `;
    tbody.appendChild(tr);
  });
}

// --------------------------------------------------------------------------
// 5. Review Queue View Rendering
// --------------------------------------------------------------------------
function renderReviewView() {
  const empty = document.querySelector("#view-review .no-data-msg");
  const content = document.querySelector("#view-review .review-content");
  
  if (!appState.decisions_loaded) {
    empty.style.display = "flex";
    content.style.display = "none";
    return;
  }
  
  empty.style.display = "none";
  content.style.display = "block";
  
  const flagged = appState.decisions.filter(d => d.requires_human_review);
  document.getElementById("review-flagged-count").textContent = flagged.length;
  
  const emptyMsg = document.getElementById("review-empty-message");
  const container = document.getElementById("review-cards-container");
  
  container.innerHTML = "";
  
  if (flagged.length === 0) {
    emptyMsg.style.display = "block";
    return;
  }
  
  emptyMsg.style.display = "none";
  
  flagged.forEach(d => {
    const card = document.createElement("div");
    card.className = "review-card";
    
    const sku = d.sku;
    const decidedBy = d.decided_by || "rules";
    const confPct = Math.round((d.confidence || 0) * 100);
    const confBar = `
      <div class="conf-wrap">
        <div class="conf-track"><div class="conf-fill" style="width:${confPct}%"></div></div>
        <span>${confPct}%</span>
      </div>
    `;
    
    const priceHtml = `$${d.old_price} <span class="arrow">→</span> <b>$${d.new_price}</b> <span class="pill pill-${getIntentColor(d.price_intent)}">${d.price_intent}</span>`;
    const conflictHtml = d.conflict ? ' &nbsp;<span class="pill pill-amber">CONFLICT</span>' : "";
    
    const brainClass = decidedBy === "llm" ? "brain-llm" : decidedBy === "rules" ? "brain-rules" : "brain-fallbk";
    const brainLabel = decidedBy === "llm" ? "LLM" : decidedBy === "rules" ? "RULES" : "FALLBACK";
    const brainBadge = `<span class="pill ${brainClass}">${brainLabel}</span>`;
    
    card.innerHTML = `
      <div class="rc-info">
        <div class="rc-title">${sku} · ${d.product_name}</div>
        <div class="rc-meta">${d.category} · objective: <b>${d.objective}</b></div>
        <div class="rc-meta">Price: ${priceHtml} &nbsp;·&nbsp; Ads: <span class="pill pill-${getIntentColor(d.ad_intent)}">${d.ad_intent || "MAINTAIN_ADS"}</span> (${d.bid_change_pct >= 0 ? '+' : ''}${Math.round(d.bid_change_pct * 100)}%)</div>
        <div class="rc-meta">Brain: ${brainBadge} &nbsp;·&nbsp; Confidence: ${confBar}${conflictHtml}</div>
        <div class="rc-reason">⚠ ${d.review_reason}</div>
        ${d.escalated && d.triage_reason ? `<div class="rc-notes">↑ Escalated: ${d.triage_reason}</div>` : ""}
        
        <details class="rc-expander">
          <summary>See full reasoning</summary>
          <div class="rc-expander-content">
            ${decidedBy === "llm" 
              ? `<p><b>LLM rationale:</b> ${d.manager_notes || "N/A"}</p>${d.rejected_reason ? `<p><b>What was rejected:</b> ${d.rejected_reason}</p>` : ""}`
              : `<p>Handled by <b>deterministic rules</b> — no LLM call.</p>${d.manager_notes ? `<p><b>Manager notes:</b> ${d.manager_notes}</p>` : ""}`
            }
            ${d.price_reason ? `<p><b>Pricing agent:</b> ${d.price_reason}</p>` : ""}
            ${d.ad_reason ? `<p><b>Ads agent:</b> ${d.ad_reason}</p>` : ""}
          </div>
        </details>
      </div>
      
      <div class="rc-actions">
        <button class="btn btn-primary btn-approve-sku" data-sku="${sku}">Approve</button>
        <button class="btn btn-secondary btn-reject-sku" data-sku="${sku}">Reject</button>
      </div>
    `;
    container.appendChild(card);
  });
  
  // Set Action listeners
  document.querySelectorAll(".btn-approve-sku").forEach(btn => {
    btn.addEventListener("click", async () => {
      const sku = btn.getAttribute("data-sku");
      btn.disabled = true;
      btn.textContent = "Approving...";
      
      try {
        const res = await fetch("/api/approve_decision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sku: sku })
        });
        const data = await res.json();
        if (data.success) {
          await fetchState();
        } else {
          alert(`Approval failed: ${data.error}`);
          btn.disabled = false;
          btn.textContent = "Approve";
        }
      } catch (err) {
        alert(`Network error: ${err}`);
        btn.disabled = false;
        btn.textContent = "Approve";
      }
    });
  });
  
  document.querySelectorAll(".btn-reject-sku").forEach(btn => {
    btn.addEventListener("click", async () => {
      const sku = btn.getAttribute("data-sku");
      btn.disabled = true;
      btn.textContent = "Rejecting...";
      
      try {
        const res = await fetch("/api/reject_decision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ sku: sku })
        });
        const data = await res.json();
        if (data.success) {
          await fetchState();
        } else {
          alert(`Rejection failed: ${data.error}`);
          btn.disabled = false;
          btn.textContent = "Reject";
        }
      } catch (err) {
        alert(`Network error: ${err}`);
        btn.disabled = false;
        btn.textContent = "Reject";
      }
    });
  });
}

// --------------------------------------------------------------------------
// 6. Settings Reset Button
// --------------------------------------------------------------------------
function setupResetButton() {
  const resetBtn = document.getElementById("btn-reset");
  resetBtn.addEventListener("click", async () => {
    if (!confirm("Are you sure you want to reset all learning, wipe decisions history, and clear session state? This cannot be undone.")) return;
    
    resetBtn.disabled = true;
    resetBtn.textContent = "Resetting...";
    
    try {
      const res = await fetch("/api/reset", { method: "POST" });
      const data = await res.json();
      resetBtn.disabled = false;
      resetBtn.textContent = "Reset Learning";
      
      if (data.success) {
        // Clear variables, route back to chat page welcome panel
        await fetchState(true);
        // Force navigate to chat tab
        document.querySelector('[data-page="chat"]').click();
      } else {
        alert("Failed to reset database.");
      }
    } catch (err) {
      resetBtn.disabled = false;
      resetBtn.textContent = "Reset Learning";
      alert("Failed to connect to backend server.");
    }
  });
}

// --------------------------------------------------------------------------
// Helpers
// --------------------------------------------------------------------------
function formatCurrency(val) {
  const abs = Math.abs(val);
  const formatted = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 }).format(abs);
  return `${val >= 0 ? "+" : "-"}${formatted}`;
}

function roundTo(val, dec) {
  return parseFloat(val || 0).toFixed(dec);
}

function getIntentColor(intent) {
  const colors = {
    "RAISE_PRICE": "green", "RAISE_PRICE_SLIGHTLY": "green", "DROP_PRICE": "red",
    "HOLD_PRICE": "gray", "INCREASE_BIDS": "green", "LOWER_BIDS": "amber",
    "PAUSE_ADS": "red", "MAINTAIN_ADS": "gray",
  };
  return colors[intent] || "gray";
}

function getReasoningOneLiner(d) {
  const notes = (d.manager_notes || "").trim();
  if (notes.toLowerCase() === "nan") return "";
  
  const price_r = (d.price_reason || "").trim();
  if (price_r.toLowerCase() === "nan") return "";
  
  const ad_r = (d.ad_reason || "").trim();
  if (ad_r.toLowerCase() === "nan") return "";
  
  const parts = [];
  if (d.decided_by === "llm" && notes) parts.push(notes);
  if (price_r) parts.push(price_r);
  if (d.ad_intent !== "MAINTAIN_ADS" && d.ad_intent && ad_r) parts.push(ad_r);
  if (d.decided_by !== "llm" && notes) parts.push(notes);
  
  // Deduplicate
  const uniq = [...new Set(parts)];
  return uniq.join(" · ") || `${d.price_intent || "HOLD_PRICE"} (deterministic rule)`;
}
