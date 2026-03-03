"use strict";

const API_BASE = "http://127.0.0.1:8765";

const els = {
  statusText: document.getElementById("statusText"),
  synthUp: document.getElementById("synthUp"),
  synthDown: document.getElementById("synthDown"),
  edgeValue: document.getElementById("edgeValue"),
  horizonLabel: document.getElementById("horizonLabel"),
  signalPrimary: document.getElementById("signalPrimary"),
  refRow: document.getElementById("refRow"),
  refLabel: document.getElementById("refLabel"),
  signalRef: document.getElementById("signalRef"),
  strength: document.getElementById("strength"),
  assetName: document.getElementById("assetName"),
  marketType: document.getElementById("marketType"),
  confFill: document.getElementById("confFill"),
  confText: document.getElementById("confText"),
  analysisText: document.getElementById("analysisText"),
  noTrade: document.getElementById("noTrade"),
  invalidationText: document.getElementById("invalidationText"),
  lastUpdate: document.getElementById("lastUpdate"),
  refreshBtn: document.getElementById("refreshBtn"),
};

function fmtCentsFromProb(p) {
  if (p == null || p === undefined) return "—";
  return Math.round(p * 100) + "¢";
}

function fmtEdge(v) {
  if (v == null || v === undefined) return "—";
  return (v >= 0 ? "+" : "") + v + "%";
}

function fmtApiTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return d.toLocaleTimeString() + " " + d.toLocaleDateString();
}

function confidenceColor(score) {
  if (score >= 0.7) return "#22c55e";
  if (score >= 0.4) return "#f59e0b";
  return "#ef4444";
}

async function activeSupportedTab() {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
  const tab = tabs && tabs[0];
  if (!tab || !tab.url || !tab.url.startsWith("https://polymarket.com/")) return null;
  return tab;
}

async function getContextFromPage(tabId) {
  try {
    const response = await chrome.tabs.sendMessage(tabId, { type: "synth:getContext" });
    return response && response.ok ? response.context : null;
  } catch (_e) {
    return null;
  }
}

async function fetchEdge(slug) {
  const res = await fetch(API_BASE + "/api/edge?slug=" + encodeURIComponent(slug));
  if (!res.ok) return null;
  return await res.json();
}

function render(state) {
  els.statusText.textContent = state.status;
  els.synthUp.textContent = state.synthUp;
  els.synthDown.textContent = state.synthDown;
  els.edgeValue.textContent = state.edge;
  els.horizonLabel.textContent = state.horizonLabel || "Primary";
  els.signalPrimary.textContent = state.signalPrimary;
  els.refLabel.textContent = state.refLabel || "Reference";
  els.signalRef.textContent = state.signalRef;
  els.refRow.style.display = state.signalRef === "—" ? "none" : "";
  els.strength.textContent = state.strength;
  els.assetName.textContent = state.asset || "—";
  els.marketType.textContent = state.marketType || "—";
  els.analysisText.textContent = state.analysis;
  els.noTrade.classList.toggle("hidden", !state.noTrade);
  els.invalidationText.textContent = state.invalidation;
  els.lastUpdate.textContent = state.lastUpdate;
  els.confFill.style.width = state.confPct + "%";
  els.confFill.style.background = state.confColor;
  els.confText.textContent = state.confText;
}

const EMPTY = {
  synthUp: "—", synthDown: "—", edge: "—",
  horizonLabel: "Primary", signalPrimary: "—",
  refLabel: "Reference", signalRef: "—",
  strength: "—", asset: "—", marketType: "—",
  analysis: "—", noTrade: false, invalidation: "—",
  confPct: 0, confColor: "#9ca3af", confText: "—",
  lastUpdate: "—",
};

async function refresh() {
  render(Object.assign({}, EMPTY, { status: "Refreshing…" }));

  const tab = await activeSupportedTab();
  if (!tab) {
    render(Object.assign({}, EMPTY, {
      status: "Open a Polymarket event tab to view Synth data.",
      analysis: "No active market tab found.",
    }));
    return;
  }

  const ctx = await getContextFromPage(tab.id);
  if (!ctx || !ctx.slug) {
    render(Object.assign({}, EMPTY, {
      status: "Could not read market context from page.",
      analysis: "Reload the page and try refresh again.",
    }));
    return;
  }

  const edge = await fetchEdge(ctx.slug);
  if (!edge || edge.error) {
    render(Object.assign({}, EMPTY, {
      status: "Market not supported by Synth for this slug.",
      analysis: edge && edge.error ? edge.error : "No data",
    }));
    return;
  }

  var synthProbUp = edge.synth_probability_up != null ? edge.synth_probability_up : edge.synth_probability;
  var conf = edge.confidence_score != null ? edge.confidence_score : 0.5;
  var confPct = Math.round(conf * 100);
  var horizon = edge.horizon || "24h";
  var mtype = edge.market_type || "daily";

  // Build signal labels based on response shape
  var horizonLabel = horizon;
  var signalPrimary = (edge.signal || "—") + " " + fmtEdge(edge.edge_pct);
  var refLabel = "Reference";
  var signalRef = "—";

  // Dual-horizon (daily/hourly): use 1h/24h fields
  if (edge.signal_1h && edge.signal_24h) {
    horizonLabel = "1 h";
    signalPrimary = edge.signal_1h + " " + fmtEdge(edge.edge_1h_pct);
    refLabel = "24 h";
    signalRef = edge.signal_24h + " " + fmtEdge(edge.edge_24h_pct);
  } else if (edge.ref_signal) {
    // Short-horizon with reference context
    refLabel = edge.ref_horizon || "Ref";
    signalRef = edge.ref_signal + " " + fmtEdge(edge.ref_edge_pct);
  }

  render({
    status: "Synced — " + (edge.asset || "BTC") + " " + horizon + " forecast.",
    synthUp: fmtCentsFromProb(synthProbUp),
    synthDown: synthProbUp == null ? "—" : fmtCentsFromProb(1 - synthProbUp),
    edge: fmtEdge(edge.edge_pct),
    horizonLabel: horizonLabel,
    signalPrimary: signalPrimary,
    refLabel: refLabel,
    signalRef: signalRef,
    strength: edge.strength || "—",
    asset: edge.asset || "BTC",
    marketType: mtype,
    analysis: edge.explanation || "No explanation available.",
    invalidation: edge.invalidation || "—",
    noTrade: !!edge.no_trade_warning,
    confPct: confPct,
    confColor: confidenceColor(conf),
    confText: (conf >= 0.7 ? "High" : conf >= 0.4 ? "Medium" : "Low") + " (" + confPct + "%)",
    lastUpdate: fmtApiTime(edge.current_time),
  });
}

els.refreshBtn.addEventListener("click", refresh);
refresh();
setInterval(refresh, 15000);
