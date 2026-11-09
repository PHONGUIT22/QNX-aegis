/**
 * ==============================================================================
 * QNX-Aegis: Real-Time Dashboard Client Engine
 * File: app.js
 * Purpose: Manages WebSocket telemetry stream, interactive Cytoscape.js IPC graph,
 *          deadlock cycle visual highlights, slog2 streaming, and QEMU lifecycle.
 * Strict zero-simulation policy: offline target shows clean disconnected state.
 * ==============================================================================
 */

(function () {
  let cy = null;
  let ws = null;
  let lastLogs = "";
  let isBooting = false;
  const API_BASE = window.location.origin.startsWith("http") ? window.location.origin : "http://localhost:8000";

  // Elements
  const targetBadge = document.getElementById("target-badge");
  const connectionBadge = document.getElementById("connection-badge");
  const connectionText = document.getElementById("connection-status-text");
  const deadlockBadge = document.getElementById("deadlock-badge");
  const deadlockDot = document.getElementById("deadlock-dot");
  const deadlockText = document.getElementById("deadlock-status-text");

  const btnStartQemu = document.getElementById("btn-start-qemu");
  const btnStartQemuIcon = document.getElementById("btn-start-qemu-icon");
  const btnStartQemuText = document.getElementById("btn-start-qemu-text");
  const btnStartOverlay = document.getElementById("btn-start-qemu-overlay");
  const cyOfflineOverlay = document.getElementById("cy-offline-overlay");

  const metricOs = document.getElementById("metric-os");
  const metricUptime = document.getElementById("metric-uptime");
  const metricThreads = document.getElementById("metric-threads");
  const metricProcs = document.getElementById("metric-procs");
  const cntRun = document.getElementById("cnt-run");
  const cntRcv = document.getElementById("cnt-rcv");
  const cntSnd = document.getElementById("cnt-snd");
  const cntRpl = document.getElementById("cnt-rpl");

  const deadlockPanel = document.getElementById("deadlock-panel");
  const deadlockPanelTag = document.getElementById("deadlock-panel-tag");
  const deadlockPanelIcon = document.getElementById("deadlock-panel-icon");
  const deadlockDetails = document.getElementById("deadlock-details");
  const aiBox = document.getElementById("ai-remediation-box");
  const aiText = document.getElementById("ai-remediation-text");
  const slogConsole = document.getElementById("slog2-console");

  const inspectTitle = document.getElementById("inspect-title");
  const inspectDetails = document.getElementById("inspect-details");

  // Initialize Cytoscape
  function initCytoscape() {
    cy = cytoscape({
      container: document.getElementById("cy-container"),
      boxSelectionEnabled: false,
      autounselectify: false,
      style: [
        {
          selector: "node",
          style: {
            "content": "data(label)",
            "text-valign": "center",
            "text-halign": "center",
            "color": "#ffffff",
            "font-size": "10px",
            "font-weight": "bold",
            "background-color": "#334155",
            "border-width": 2,
            "border-color": "#475569",
            "width": 54,
            "height": 54,
            "text-wrap": "wrap",
            "text-max-width": "50px"
          }
        },
        {
          selector: "node[state = 'RUNNING'], node[state = 'READY']",
          style: {
            "background-color": "#065f46",
            "border-color": "#10b981",
            "color": "#a7f3d0"
          }
        },
        {
          selector: "node[state = 'RECEIVE']",
          style: {
            "background-color": "#155e75",
            "border-color": "#06b6d4",
            "color": "#cffafe"
          }
        },
        {
          selector: "node[state = 'SEND']",
          style: {
            "background-color": "#78350f",
            "border-color": "#f59e0b",
            "color": "#fef3c7"
          }
        },
        {
          selector: "node[state = 'REPLY']",
          style: {
            "background-color": "#881337",
            "border-color": "#f43f5e",
            "color": "#ffe4e6"
          }
        },
        {
          selector: "node[?is_deadlocked]",
          style: {
            "background-color": "#7f1d1d",
            "border-color": "#ef4444",
            "border-width": 4,
            "shadow-blur": 15,
            "shadow-color": "#ef4444"
          }
        },
        {
          selector: "edge",
          style: {
            "width": 2,
            "line-color": "#475569",
            "target-arrow-color": "#475569",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "label": "data(label)",
            "font-size": "8px",
            "color": "#94a3b8",
            "text-rotation": "autorotate"
          }
        },
        {
          selector: "edge[?is_deadlock_edge]",
          style: {
            "width": 3,
            "line-color": "#ef4444",
            "target-arrow-color": "#ef4444",
            "line-style": "dashed",
            "color": "#fca5a5"
          }
        }
      ],
      elements: []
    });

    cy.on("tap", "node", function (evt) {
      const d = evt.target.data();
      inspectTitle.textContent = `${d.proc_name} (TID ${d.tid})`;
      inspectDetails.innerHTML = `
        <span>PID: <b class="text-cyan-400">${d.pid}</b></span>
        <span>TID: <b class="text-cyan-400">${d.tid}</b></span>
        <span>State: <span class="state-tag state-${d.state}">${d.state}</span></span>
        <span>Channel: <b class="text-white">${d.chid > 0 ? d.chid : "N/A"}</b></span>
        <span>Conn: <b class="text-white">${d.coid > 0 ? d.coid : "N/A"}</b></span>
      `;
    });
  }

  // Update Cytoscape Graph with Telemetry
  function updateGraph(threads, deadlockAnalysis) {
    if (!cy) return;

    const deadlockedNodes = new Set(deadlockAnalysis ? deadlockAnalysis.affected_threads : []);
    const elements = [];
    const nodeIds = new Set();

    threads.forEach(t => {
      const id = `${t.pid}:${t.tid}`;
      nodeIds.add(id);
      const isDl = deadlockedNodes.has(id) || t.is_deadlocked;

      elements.push({
        group: "nodes",
        data: {
          id: id,
          pid: t.pid,
          tid: t.tid,
          proc_name: t.name || "proc",
          label: `${t.name || "proc"}\n[${t.state}]`,
          state: t.state,
          priority: t.priority,
          chid: t.chid,
          coid: t.coid,
          is_deadlocked: isDl
        }
      });
    });

    // Add Deadlock Edges
    if (deadlockAnalysis && deadlockAnalysis.cycles) {
      deadlockAnalysis.cycles.forEach((cycle, cIdx) => {
        const nodes = cycle.cycle_nodes;
        for (let i = 0; i < nodes.length - 1; i++) {
          const src = nodes[i];
          const dst = nodes[i + 1];
          if (nodeIds.has(src) && nodeIds.has(dst)) {
            elements.push({
              group: "edges",
              data: {
                id: `dl-${cIdx}-${src}-${dst}`,
                source: src,
                target: dst,
                label: "MsgSend WAIT",
                is_deadlock_edge: true
              }
            });
          }
        }
      });
    }

    // Connect standard IPC endpoints
    threads.forEach(t => {
      if (t.coid > 0 && (t.state === "SEND" || t.state === "REPLY")) {
        const src = `${t.pid}:${t.tid}`;
        const targetServer = threads.find(s => s.chid === t.coid && s.state === "RECEIVE");
        if (targetServer) {
          const dst = `${targetServer.pid}:${targetServer.tid}`;
          const edgeId = `ipc-${src}-${dst}`;
          if (!elements.find(e => e.data && e.data.id === edgeId)) {
            elements.push({
              group: "edges",
              data: {
                id: edgeId,
                source: src,
                target: dst,
                label: `coid ${t.coid}`,
                is_deadlock_edge: false
              }
            });
          }
        }
      }
    });

    cy.json({ elements: elements });
    cy.layout({
      name: "cose",
      animate: false,
      padding: 30,
      nodeRepulsion: 450000,
      idealEdgeLength: 80
    }).run();
  }

  // Update UI Panels with Telemetry
  function handleTelemetry(data) {
    if (!data) return;

    // === OFFLINE STATE (Strict Zero Simulation) ===
    if (data.connected === false) {
      targetBadge.className = "px-3 py-1 rounded-full text-xs font-semibold bg-rose-950 text-rose-400 border border-rose-800 flex items-center gap-2";
      targetBadge.innerHTML = `<span class="w-2 h-2 rounded-full bg-rose-500 animate-ping"></span><span>Target: OFFLINE</span>`;

      connectionBadge.className = "px-3 py-1 rounded-full text-xs font-medium bg-slate-800 text-slate-400 border border-slate-700 flex items-center gap-2";
      connectionText.textContent = "QEMU SSH: Disconnected";

      deadlockBadge.className = "px-3 py-1 rounded-full text-xs font-medium bg-slate-800 text-slate-500 border border-slate-700 flex items-center gap-2";
      deadlockDot.className = "w-2 h-2 rounded-full bg-slate-500";
      deadlockText.textContent = "IPC: Offline";

      metricOs.textContent = "OFFLINE (Start QNX 8.0 VM)";
      metricUptime.textContent = "Target VM stopped";
      metricThreads.textContent = "0";
      metricProcs.textContent = "0";
      cntRun.textContent = "0";
      cntRcv.textContent = "0";
      cntSnd.textContent = "0";
      cntRpl.textContent = "0";

      deadlockPanel.className = "bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col";
      deadlockPanelTag.className = "text-[10px] px-2 py-0.5 rounded font-mono bg-slate-800 text-slate-400";
      deadlockPanelTag.textContent = "STATUS: OFFLINE";
      deadlockPanelIcon.textContent = "⚡";
      deadlockDetails.innerHTML = `<p class="text-slate-400">Waiting for QNX 8.0 QEMU VM to boot...</p>`;
      aiBox.classList.add("hidden");

      if (cyOfflineOverlay) cyOfflineOverlay.classList.remove("hidden");
      if (cy) cy.elements().remove();

      inspectTitle.textContent = "Target Offline";
      inspectDetails.innerHTML = `<span>PID: -</span><span>TID: -</span><span>State: -</span><span>Channel: -</span><span>Conn: -</span>`;

      if (btnStartQemu && !isBooting) {
        btnStartQemu.disabled = false;
        btnStartQemu.className = "px-3.5 py-1.5 rounded-full text-xs font-bold bg-cyan-600 hover:bg-cyan-500 text-white transition flex items-center gap-1.5 shadow-md shadow-cyan-950";
        if (btnStartQemuIcon) btnStartQemuIcon.textContent = "🚀";
        if (btnStartQemuText) btnStartQemuText.textContent = "Start QNX 8.0 QEMU VM";
      }

      const slogText = data.slog2_tail || "[OFFLINE] Waiting for QNX 8.0 VM...";
      if (slogText !== lastLogs) {
        lastLogs = slogText;
        slogConsole.innerHTML = `<div class="terminal-line warn">${slogText}</div>`;
      }
      return;
    }

    // === LIVE TARGET CONNECTED ===
    if (cyOfflineOverlay) cyOfflineOverlay.classList.add("hidden");

    if (btnStartQemu && !isBooting) {
      btnStartQemu.disabled = true;
      btnStartQemu.className = "px-3.5 py-1.5 rounded-full text-xs font-medium bg-slate-800 text-emerald-400 border border-emerald-800/50 flex items-center gap-1.5 cursor-default";
      if (btnStartQemuIcon) btnStartQemuIcon.textContent = "🟢";
      if (btnStartQemuText) btnStartQemuText.textContent = "QNX VM Running";
    }

    // 1. System Info
    if (data.system) {
      metricOs.textContent = `${data.system.os || "QNX 8.0"} (${data.system.architecture || "x86_64"})`;
      metricUptime.textContent = `Uptime: ${data.system.uptime_sec || 0}s | Host: ${data.system.hostname || "qnx"}`;
      metricThreads.textContent = data.system.num_threads || (data.threads ? data.threads.length : 0);
      metricProcs.textContent = data.system.num_processes || 0;

      targetBadge.className = "px-3 py-1 rounded-full text-xs font-medium bg-emerald-950 text-emerald-300 border border-emerald-800 flex items-center gap-2";
      targetBadge.innerHTML = `
        <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
        <span>Target: ${data.system.os || "QNX Neutrino 8.0"}</span>
      `;

      connectionBadge.className = "px-3 py-1 rounded-full text-xs font-medium bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center gap-2";
      connectionText.textContent = "QEMU SSH: Active (127.0.0.1:2222)";
    }

    // 2. Thread State Counts
    const threads = data.threads || [];
    let r = 0, rcv = 0, snd = 0, rpl = 0;
    threads.forEach(t => {
      const s = (t.state || "").toUpperCase();
      if (s === "RUNNING" || s === "READY") r++;
      else if (s === "RECEIVE") rcv++;
      else if (s === "SEND") snd++;
      else if (s === "REPLY") rpl++;
    });
    cntRun.textContent = r;
    cntRcv.textContent = rcv;
    cntSnd.textContent = snd;
    cntRpl.textContent = rpl;

    // 3. Deadlock Detective Analysis
    const da = data.deadlock_analysis || {};
    if (da.deadlock_detected) {
      deadlockBadge.className = "px-3 py-1 rounded-full text-xs font-bold bg-rose-950 text-rose-400 border border-rose-800 flex items-center gap-2 pulse-red";
      deadlockDot.className = "w-2 h-2 rounded-full bg-rose-400";
      deadlockText.textContent = `DEADLOCK (${da.cycle_count} cycles)`;

      deadlockPanel.className = "bg-slate-900 border border-rose-800 rounded-xl p-4 flex flex-col shadow-lg shadow-rose-950/40";
      deadlockPanelTag.className = "text-[10px] px-2 py-0.5 rounded font-mono bg-rose-950 text-rose-400 border border-rose-800";
      deadlockPanelTag.textContent = "CRITICAL DEADLOCK";
      deadlockPanelIcon.textContent = "🚨";

      let cyclesHtml = "";
      da.cycles.forEach(c => {
        cyclesHtml += `
          <div class="p-2.5 rounded bg-rose-950/40 border border-rose-900/60 font-mono text-[11px]">
            <div class="text-rose-300 font-bold mb-1">Wait Cycle: ${c.cycle_path}</div>
            <div class="text-slate-400">${c.description}</div>
          </div>
        `;
      });
      deadlockDetails.innerHTML = cyclesHtml;

      if (da.remediation_advice) {
        aiBox.classList.remove("hidden");
        aiText.textContent = da.remediation_advice;
      }
    } else {
      deadlockBadge.className = "px-3 py-1 rounded-full text-xs font-bold bg-slate-800 text-slate-400 border border-slate-700 flex items-center gap-2";
      deadlockDot.className = "w-2 h-2 rounded-full bg-emerald-400";
      deadlockText.textContent = "IPC: Nominal";

      deadlockPanel.className = "bg-slate-900 border border-slate-800 rounded-xl p-4 flex flex-col";
      deadlockPanelTag.className = "text-[10px] px-2 py-0.5 rounded font-mono bg-slate-800 text-slate-400";
      deadlockPanelTag.textContent = "STATUS: CLEAN";
      deadlockPanelIcon.textContent = "🛡";

      deadlockDetails.innerHTML = `<p class="text-slate-400">${da.summary || "No circular waits detected in the QNX message-passing pipeline."}</p>`;
      aiBox.classList.add("hidden");
    }

    // 4. Update Slog2 Console
    const slogText = data.slog2_tail || "";
    if (slogText && slogText !== lastLogs) {
      lastLogs = slogText;
      const lines = slogText.split("\n");
      slogConsole.innerHTML = "";
      lines.slice(-50).forEach(l => {
        const trimmed = l.trim();
        if (!trimmed) return;
        const div = document.createElement("div");
        div.className = "terminal-line";
        if (trimmed.includes("CRIT") || trimmed.includes("DEADLOCK")) div.classList.add("crit");
        else if (trimmed.includes("ERROR") || trimmed.includes("FAIL")) div.classList.add("error");
        else if (trimmed.includes("WARN")) div.classList.add("warn");
        else div.classList.add("info");
        div.textContent = trimmed;
        slogConsole.appendChild(div);
      });
      slogConsole.scrollTop = slogConsole.scrollHeight;
    }

    // 5. Update Cytoscape Graph
    updateGraph(threads, da);
  }

  // WebSocket Connection Setup
  function connectWebSocket() {
    const wsProto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${wsProto}//${window.location.host}/ws/telemetry`;

    try {
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        connectionBadge.className = "px-3 py-1 rounded-full text-xs font-medium bg-emerald-950 text-emerald-400 border border-emerald-800 flex items-center gap-2";
        connectionText.textContent = "Telemetry: Live (WS)";
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "telemetry_update") {
            handleTelemetry(msg.payload);
          }
        } catch (e) {
          console.error("Error parsing WS telemetry:", e);
        }
      };

      ws.onclose = () => {
        connectionBadge.className = "px-3 py-1 rounded-full text-xs font-medium bg-amber-950 text-amber-400 border border-amber-800 flex items-center gap-2";
        connectionText.textContent = "Polling (REST Fallback)";
        setTimeout(connectWebSocket, 4000);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch (e) {
      console.warn("WebSocket unavailable, falling back to REST poll.");
    }
  }

  // Fallback REST Polling
  async function pollTelemetry() {
    try {
      const res = await fetch(`${API_BASE}/api/telemetry`);
      if (res.ok) {
        const data = await res.json();
        handleTelemetry(data);
      }
    } catch (e) {
      console.warn("REST poll failed:", e);
    }
  }

  // Start QEMU VM Action
  async function startQemuVm() {
    if (isBooting) return;
    isBooting = true;

    const setStatus = (msg) => {
      if (btnStartQemuText) btnStartQemuText.textContent = msg;
      if (btnStartQemuIcon) btnStartQemuIcon.textContent = "⏳";
      if (btnStartOverlay) btnStartOverlay.innerHTML = `<span>⏳ ${msg}</span>`;
      if (btnStartQemu) btnStartQemu.disabled = true;
      if (btnStartOverlay) btnStartOverlay.disabled = true;
    };

    setStatus("Booting QNX 8.0 VM (5-10s)...");

    try {
      const res = await fetch(`${API_BASE}/api/qemu/start`, { method: "POST" });
      const data = await res.json();
      if (data.success) {
        setStatus("Target Connected & Provisioned!");
        setTimeout(() => {
          isBooting = false;
          pollTelemetry();
        }, 1200);
      } else {
        alert("Failed to start QEMU VM: " + (data.error || "Unknown error"));
        isBooting = false;
        pollTelemetry();
      }
    } catch (err) {
      alert("Error triggering QEMU launch: " + err);
      isBooting = false;
      pollTelemetry();
    }
  }

  // Action Button Listeners
  if (btnStartQemu) btnStartQemu.addEventListener("click", startQemuVm);
  if (btnStartOverlay) btnStartOverlay.addEventListener("click", startQemuVm);

  document.getElementById("btn-trigger-deadlock").addEventListener("click", async () => {
    try {
      const res = await fetch(`${API_BASE}/api/trigger/deadlock`, { method: "POST" });
      const data = await res.json();
      if (!data.success && data.error) {
        alert(data.error);
      }
      setTimeout(pollTelemetry, 600);
    } catch (e) {
      alert("Failed to dispatch deadlock demo: " + e);
    }
  });

  document.getElementById("btn-trigger-healthy").addEventListener("click", async () => {
    try {
      const res = await fetch(`${API_BASE}/api/trigger/healthy`, { method: "POST" });
      const data = await res.json();
      if (!data.success && data.error) {
        alert(data.error);
      }
      setTimeout(pollTelemetry, 600);
    } catch (e) {
      alert("Failed to dispatch healthy demo: " + e);
    }
  });

  document.getElementById("btn-trigger-crash").addEventListener("click", async () => {
    try {
      const res = await fetch(`${API_BASE}/api/trigger/crash`, { method: "POST" });
      const data = await res.json();
      if (!data.success && data.error) {
        alert(data.error);
      }
      setTimeout(pollTelemetry, 600);
    } catch (e) {
      alert("Failed to trigger crash demo: " + e);
    }
  });

  document.getElementById("btn-run-detective").addEventListener("click", async () => {
    try {
      const res = await fetch(`${API_BASE}/api/detective`, { method: "POST" });
      const data = await res.json();
      alert(`Deadlock Detective Report:\n${data.summary}\n${data.remediation_advice || ""}`);
      pollTelemetry();
    } catch (e) {
      alert("Failed to run detective: " + e);
    }
  });

  document.getElementById("btn-reset-layout").addEventListener("click", () => {
    if (cy) {
      cy.layout({ name: "cose", animate: true }).run();
    }
  });

  document.getElementById("btn-poll-refresh").addEventListener("click", () => {
    pollTelemetry();
  });

  document.getElementById("btn-clear-logs").addEventListener("click", () => {
    slogConsole.innerHTML = "";
  });

  // Initialization
  document.addEventListener("DOMContentLoaded", () => {
    initCytoscape();
    connectWebSocket();
    pollTelemetry();
    setInterval(pollTelemetry, 3000);
  });
})();
