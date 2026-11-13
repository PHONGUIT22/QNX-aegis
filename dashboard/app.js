/**
 * QNX-Aegis Live Telemetry Workstation (ZOC Terminal + btop TUI)
 * Connects strictly to real QNX 8.0 /api/telemetry, WebSocket, Detective,
 * ClockCycles() Benchmark, QCC Hot-Patcher, and MCP JSON-RPC Studio endpoints.
 */

const state = {
    cy: null,
    ws: null,
    pollIntervalMs: 2000,
    pollTimer: null,
    activeHistory: new Array(60).fill(0),
    ipcHistory: new Array(60).fill(0),
    latestTelemetry: null,
    latestDetective: null,
    selectedNodeKey: null,
    lastStructuralSig: null,
    lastDetectiveSig: null,
    procFilterText: '',
    mcpTools: [],
    selectedMcpTool: null,
    rpcSeq: 1,
    isBootingQemu: false,
    detectiveInFlight: false,
    detectivePromise: null,
    justResolvedByHotpatch: false,
    hotpatchResolutionSummary: '',
};

// Unicode Braille 2x4 vertical column bitmasks (left & right columns, levels 0..4)
const BRAILLE_LEFT = [0x00, 0x40, 0x44, 0x46, 0x47];
const BRAILLE_RIGHT = [0x00, 0x80, 0xa0, 0xb0, 0xb8];

function $(id) {
    return document.getElementById(id);
}

function escapeHtml(text) {
    if (text === null || text === undefined) return '';
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function formatUptime(totalSec) {
    const sec = Math.max(0, Math.floor(Number(totalSec) || 0));
    const h = String(Math.floor(sec / 3600)).padStart(2, '0');
    const m = String(Math.floor((sec % 3600) / 60)).padStart(2, '0');
    const s = String(sec % 60).padStart(2, '0');
    return `${h}:${m}:${s}`;
}

function formatFreq(cyclesPerSec) {
    const hz = Number(cyclesPerSec) || 0;
    if (hz <= 0) return '--';
    if (hz >= 1e9) return `${(hz / 1e9).toFixed(2)} GHz`;
    if (hz >= 1e6) return `${(hz / 1e6).toFixed(1)} MHz`;
    return `${hz.toLocaleString()} Hz`;
}

function renderAsciiMeter(ratio, width = 16) {
    const clamped = Math.max(0, Math.min(1, Number(ratio) || 0));
    const filled = Math.round(clamped * width);
    return '■'.repeat(filled) + '□'.repeat(width - filled);
}

function renderBrailleSparkline(samples) {
    // Pair 60 samples into 30 dual-column Unicode Braille glyphs
    const padded = samples.slice(-60);
    while (padded.length < 60) padded.unshift(0);
    let out = '';
    for (let i = 0; i < padded.length; i += 2) {
        const l = Math.max(0, Math.min(4, Math.round((padded[i] || 0) * 4)));
        const r = Math.max(0, Math.min(4, Math.round((padded[i + 1] || 0) * 4)));
        out += String.fromCharCode(0x2800 | BRAILLE_LEFT[l] | BRAILLE_RIGHT[r]);
    }
    return out;
}

function threadKey(t) {
    return `${t.pid}:${t.tid}`;
}

function classifyStateGroup(rawState) {
    const st = String(rawState || '').toUpperCase();
    if (st.includes('RECEIVE')) return 'RCV';
    if (st.includes('SEND')) return 'SND';
    if (st.includes('REPLY')) return 'RPL';
    if (st.includes('RUN') || st.includes('READY')) return 'RUN';
    return 'OTHER';
}

function stateBadgeColor(rawState, isDeadlocked) {
    if (isDeadlocked) return '#bf616a';
    const st = String(rawState || '').toUpperCase();
    if (st.includes('SEND')) return '#ebcb8b';
    if (st.includes('RECEIVE')) return '#88c0d0';
    if (st.includes('REPLY')) return '#b48ead';
    if (st.includes('RUN') || st.includes('READY')) return '#a3be8c';
    return '#81a1c1';
}

/* ============================================================================
 * Button Loading States, Live Action Feedback Bar & Panel Focus/Flash Helpers
 * ========================================================================= */
function setButtonBusy(btnId, busy, busyLabel = 'Working...') {
    const btn = $(btnId);
    if (!btn) return;
    if (busy) {
        if (!btn.dataset.origHtml) {
            btn.dataset.origHtml = btn.innerHTML;
        }
        btn.disabled = true;
        btn.classList.add('btn-active-action');
        btn.innerHTML = `<span>⏳ ${escapeHtml(busyLabel)}</span>`;
    } else {
        if (btn.dataset.origHtml) {
            btn.innerHTML = btn.dataset.origHtml;
        }
        btn.classList.remove('btn-active-action');
        btn.disabled = false;
    }
}

function setActionFeedback(level, message, quickActions = [], isHtml = false) {
    const badge = $('feedback-badge');
    const cmdEl = $('feedback-cmd');
    const rightEl = $('feedback-right');

    const map = {
        idle: { cls: 'tag-idle', label: 'READY' },
        running: { cls: 'tag-running', label: 'RUNNING' },
        ok: { cls: 'tag-ok', label: 'DONE' },
        warn: { cls: 'tag-warn', label: 'WARN' },
        crit: { cls: 'tag-crit', label: 'CRITICAL' },
    };
    const spec = map[level] || map.idle;

    if (badge) {
        badge.className = `feedback-tag ${spec.cls}`;
        badge.textContent = spec.label;
    }
    if (cmdEl) {
        if (isHtml) {
            cmdEl.innerHTML = message;
        } else {
            cmdEl.textContent = message;
        }
    }
    if (rightEl) {
        rightEl.innerHTML = '';
        for (const act of quickActions) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'feedback-focus-btn';
            btn.textContent = act.label;
            btn.addEventListener('click', () => {
                if (typeof act.onClick === 'function') {
                    act.onClick();
                } else if (act.panelId) {
                    focusAndFlashPanel(act.panelId, Boolean(act.scrollSlog));
                }
            });
            rightEl.appendChild(btn);
        }
    }
}

function focusAndFlashPanel(panelId, scrollSlogConsole = false) {
    const btopWorkspace = $('btop-workspace');
    if (btopWorkspace && btopWorkspace.classList.contains('hidden')) {
        switchZocTab('btop');
    }
    const panel = $(panelId);
    if (panel) {
        panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        panel.classList.remove('panel-flash');
        void panel.offsetWidth;
        panel.classList.add('panel-flash');
    }
    if (scrollSlogConsole) {
        const consoleEl = $('slog2-console');
        if (consoleEl) consoleEl.scrollTop = consoleEl.scrollHeight;
    }
}

function zoomToDeadlockedNodes() {
    const btopWorkspace = $('btop-workspace');
    if (btopWorkspace && btopWorkspace.classList.contains('hidden')) {
        switchZocTab('btop');
    }
    focusAndFlashPanel('panel-ipc-topology');

    const cy = state.cy;
    if (!cy) return;

    const deadlockedIds = new Set(
        Array.isArray(state.latestDetective?.affected_threads)
            ? state.latestDetective.affected_threads.map(String)
            : []
    );
    const threads = state.latestTelemetry?.threads || [];
    for (const t of threads) {
        if (t.is_deadlocked) deadlockedIds.add(threadKey(t));
    }

    let collection = cy.collection();
    for (const id of deadlockedIds) {
        const ele = cy.getElementById(id);
        if (ele && ele.length) collection = collection.union(ele);
    }

    if (collection.length > 0) {
        cy.$(':selected').unselect();
        collection.select();
        const firstId = collection[0].id();
        selectThreadByKey(firstId, false);
        requestAnimationFrame(() => {
            cy.resize();
            cy.animate({ fit: { eles: collection, padding: 64 }, duration: 420 });
        });
    } else if (cy.nodes().length > 0) {
        requestAnimationFrame(() => {
            cy.resize();
            cy.animate({ fit: { eles: cy.elements(), padding: 32 }, duration: 350 });
        });
    }
}

/* ============================================================================
 * Initialization & ZOC Clock / Session Tabs
 * ========================================================================= */
document.addEventListener('DOMContentLoaded', () => {
    initZocClock();
    initCytoscape();
    bindEventListeners();
    fetchTelemetry();
    connectWebSocket();
    restartPollTimer();
    loadMcpTools();
});

function initZocClock() {
    const tick = () => {
        const el = $('zoc-clock');
        if (el) {
            const now = new Date();
            el.textContent = now.toTimeString().slice(0, 8);
        }
    };
    tick();
    setInterval(tick, 1000);
}

function bindEventListeners() {
    document.querySelectorAll('.zoc-tab').forEach((btn) => {
        btn.addEventListener('click', () => {
            switchZocTab(btn.dataset.tab);
        });
    });

    $('btn-poll-slower')?.addEventListener('click', () => adjustPollInterval(500));
    $('btn-poll-faster')?.addEventListener('click', () => adjustPollInterval(-500));
    $('btn-poll-refresh')?.addEventListener('click', async () => {
        setButtonBusy('btn-poll-refresh', true, 'Syncing...');
        setActionFeedback('running', 'Refreshing live telemetry from /dev/qnx_inspector and auditing kernel threads...');
        try {
            await fetchTelemetry();
            const res = await runDetective(false);
            const thr = state.latestTelemetry?.threads?.length || 0;
            const cyc = res?.cycles?.length || 0;
            setActionFeedback(
                state.latestTelemetry?.connected ? 'ok' : 'warn',
                state.latestTelemetry?.connected
                    ? `Telemetry synced: ${thr} active threads (${cyc} deadlock cycles detected).`
                    : 'Telemetry checked: QNX 8.0 QEMU VM is currently offline (127.0.0.1:2222 unreachable).'
            );
        } finally {
            setButtonBusy('btn-poll-refresh', false);
        }
    });

    $('btn-start-qemu')?.addEventListener('click', handleStartQemu);
    $('btn-start-qemu-overlay')?.addEventListener('click', handleStartQemu);

    $('btn-trigger-deadlock')?.addEventListener('click', () => triggerDemo('deadlock'));
    $('btn-trigger-healthy')?.addEventListener('click', () => triggerDemo('healthy'));
    $('btn-trigger-crash')?.addEventListener('click', () => triggerDemo('crash'));
    $('btn-trigger-benchmark')?.addEventListener('click', () => triggerBenchmark('btn-trigger-benchmark'));
    $('btn-bench-inline')?.addEventListener('click', () => triggerBenchmark('btn-bench-inline'));

    $('btn-run-detective')?.addEventListener('click', () => runDetective(true));
    $('btn-ai-autofix')?.addEventListener('click', triggerAiAutofix);
    $('btn-ai-autofix-inline')?.addEventListener('click', triggerAiAutofix);

    $('btn-reset-layout')?.addEventListener('click', () => {
        state.lastStructuralSig = null;
        if (state.latestTelemetry) {
            updateTopologyGraph(state.latestTelemetry, state.latestDetective, true);
        } else if (state.cy) {
            state.cy.fit(undefined, 28);
        }
        focusAndFlashPanel('panel-ipc-topology');
        setActionFeedback('ok', 'Cytoscape IPC topology layout reset and fitted to panel.', [
            { label: '🔍 Focus Graph', panelId: 'panel-ipc-topology' },
        ]);
    });

    $('proc-filter-input')?.addEventListener('input', (e) => {
        state.procFilterText = (e.target.value || '').trim().toLowerCase();
        if (state.latestTelemetry) {
            renderProcTable(state.latestTelemetry.threads || [], Boolean(state.latestTelemetry.connected));
        }
    });

    $('btn-clear-logs')?.addEventListener('click', () => {
        const consoleEl = $('slog2-console');
        if (consoleEl) consoleEl.innerHTML = '<div style="color:#81a1c1;">[Terminal buffer cleared — awaiting next telemetry frame]</div>';
        const cntEl = $('slog2-line-count');
        if (cntEl) cntEl.textContent = '0 lines';
    });

    // MCP JSON-RPC Studio bindings
    $('btn-reload-mcp-tools')?.addEventListener('click', loadMcpTools);
    $('btn-invoke-mcp-tool')?.addEventListener('click', invokeSelectedMcpTool);
    $('mcp-args-input')?.addEventListener('input', updateMcpRequestPreview);

    document.querySelectorAll('.mcp-quick-btn').forEach((btn) => {
        btn.addEventListener('click', () => {
            runQuickRestProbe(btn.dataset.endpoint, btn.dataset.method || 'GET');
        });
    });
}

function switchZocTab(tabName) {
    document.querySelectorAll('.zoc-tab').forEach((btn) => {
        btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    const btopWorkspace = $('btop-workspace');
    const mcpStudio = $('mcp-studio-view');
    if (!btopWorkspace || !mcpStudio) return;

    if (tabName === 'mcp') {
        btopWorkspace.classList.add('hidden');
        mcpStudio.classList.remove('hidden');
        if (!state.mcpTools.length) loadMcpTools();
        return;
    }

    mcpStudio.classList.add('hidden');
    btopWorkspace.classList.remove('hidden');
    btopWorkspace.classList.toggle('mode-btop', tabName === 'btop');
    btopWorkspace.classList.toggle('mode-topology', tabName === 'topology');

    requestAnimationFrame(() => {
        if (state.cy) {
            state.cy.resize();
            state.cy.fit(undefined, 28);
        }
    });
}

function adjustPollInterval(deltaMs) {
    state.pollIntervalMs = Math.max(500, Math.min(10000, state.pollIntervalMs + deltaMs));
    const label = $('poll-interval-label');
    if (label) {
        label.textContent = `${state.pollIntervalMs}ms`;
    }
    restartPollTimer();
}

function restartPollTimer() {
    if (state.pollTimer) clearInterval(state.pollTimer);
    state.pollTimer = setInterval(fetchTelemetry, state.pollIntervalMs);
}

/* ============================================================================
 * WebSocket & Telemetry Polling
 * ========================================================================= */
function connectWebSocket() {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${window.location.host}/ws/telemetry`;
    try {
        const ws = new WebSocket(url);
        state.ws = ws;
        ws.onmessage = (evt) => {
            try {
                const msg = JSON.parse(evt.data);
                if (msg && msg.type === 'telemetry_update' && msg.payload) {
                    applyTelemetry(msg.payload);
                }
            } catch (_) {
                // Ignore malformed ws packet
            }
        };
        ws.onclose = () => {
            state.ws = null;
            setTimeout(connectWebSocket, 3000);
        };
        ws.onerror = () => {
            try { ws.close(); } catch (_) {}
        };
    } catch (_) {
        // Fallback polling continues seamlessly
    }
}

async function fetchTelemetry() {
    try {
        const resp = await fetch('/api/telemetry');
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        applyTelemetry(data);
    } catch (err) {
        applyTelemetry({
            connected: false,
            simulated: false,
            error: `Backend API unreachable: ${err.message}`,
            system: {
                os: 'API DISCONNECTED',
                architecture: 'x86_64',
                hostname: 'disconnected',
                uptime_sec: 0,
                num_processes: 0,
                num_threads: 0,
                deadlocks_detected: 0,
            },
            threads: [],
            slog2_tail: `[OFFLINE] FastAPI bridge unreachable (${err.message}).`,
        });
    }
}

function applyTelemetry(data) {
    state.latestTelemetry = data;
    const connected = Boolean(data && data.connected);
    const sys = data.system || {};
    const threads = Array.isArray(data.threads) ? data.threads : [];

    updateConnectionChrome(connected, sys);
    renderKernelCpuPanel(connected, sys, threads);
    renderProcTable(threads, connected);
    renderSlog2Console(data.slog2_tail, connected);

    if (!connected) {
        state.latestDetective = null;
        state.lastDetectiveSig = null;
        updateTopologyGraph(data, null, false);
        renderDetectiveOffline();
        return;
    }

    if (data.deadlock_analysis && typeof data.deadlock_analysis === 'object') {
        state.latestDetective = data.deadlock_analysis;
        renderDetectiveResult(data.deadlock_analysis);
        updateTopologyGraph(data, state.latestDetective, false);
        return;
    }

    updateTopologyGraph(data, state.latestDetective, false);

    // Fallback background Detective audit when thread blocking pattern changes
    const sig = threads
        .filter((t) => t.target_pid || t.is_deadlocked)
        .map((t) => `${t.pid}:${t.tid}->${t.target_pid || 0}:${t.state}:${t.priority}:${t.is_deadlocked ? 1 : 0}`)
        .sort()
        .join('|');

    if (sig !== state.lastDetectiveSig && !state.detectiveInFlight) {
        state.lastDetectiveSig = sig;
        runDetective(false);
    }
}

function updateConnectionChrome(connected, sys) {
    const connBadge = $('connection-badge');
    const connText = $('connection-status-text');
    const targetBadge = $('target-badge');
    const targetBadgeText = $('target-badge-text');
    const targetDot = $('target-dot');
    const overlay = $('cy-offline-overlay');

    if (connBadge) {
        connBadge.className = `zoc-pill ${connected ? 'zoc-pill-green' : 'zoc-pill-red'}`;
    }
    if (connText) {
        connText.textContent = connected ? 'QEMU SSH: ONLINE' : 'QEMU SSH: OFFLINE';
    }
    const arch = sys.architecture || 'x86_64';
    const host = sys.hostname || 'disconnected';
    if (targetBadge) {
        targetBadge.className = `zoc-pill ${connected ? 'zoc-pill-green' : 'zoc-pill-muted'}`;
    }
    if (targetDot) {
        targetDot.className = `zoc-dot ${connected ? 'bg-emerald-400' : 'bg-slate-500'}`;
    }
    if (targetBadgeText) {
        targetBadgeText.textContent = connected ? `Target: QNX 8.0 ${arch} (${host})` : `Target: ${arch} OFFLINE`;
    }
    if (overlay) {
        overlay.classList.toggle('hidden', connected);
    }

    const qemuBtn = $('btn-start-qemu');
    const qemuIcon = $('btn-start-qemu-icon');
    const qemuText = $('btn-start-qemu-text');
    if (qemuBtn && !state.isBootingQemu) {
        qemuBtn.disabled = connected;
        if (qemuIcon) qemuIcon.textContent = connected ? '✅' : '🚀';
        if (qemuText) qemuText.textContent = connected ? 'QNX 8.0 VM Online' : 'Boot QNX 8.0 VM';
    }

    // Bottom ZOC session footer
    const sshEl = $('zoc-status-ssh');
    const devEl = $('zoc-status-dev');
    const upEl = $('zoc-status-uptime');
    if (sshEl) {
        sshEl.textContent = connected ? '🟩 Secure Shell (127.0.0.1:2222)' : '🟥 SSH Offline (127.0.0.1:2222)';
        sshEl.style.color = connected ? '#a3be8c' : '#bf616a';
    }
    if (devEl) {
        devEl.textContent = connected ? '🟩 /dev/qnx_inspector (MOUNTED)' : '⬜ /dev/qnx_inspector (CLOSED)';
        devEl.style.color = connected ? '#88c0d0' : '#81a1c1';
    }
    if (upEl) {
        upEl.textContent = connected ? `Uptime: ${formatUptime(sys.uptime_sec)}` : 'Uptime: --:--:--';
    }
}

/* ============================================================================
 * Panel 1: btop Kernel & Braille Sparklines
 * ========================================================================= */
function renderKernelCpuPanel(connected, sys, threads) {
    const metaEl = $('cpu-header-meta');
    const osEl = $('metric-os');
    const upEl = $('metric-uptime');

    if (metaEl) metaEl.textContent = `${sys.architecture || 'x86_64'} · ${sys.hostname || 'offline'}`;
    if (osEl) osEl.textContent = sys.os || (connected ? 'QNX Neutrino 8.0' : 'OFFLINE');
    if (upEl) upEl.textContent = connected ? formatUptime(sys.uptime_sec) : '--:--:--';

    let runCnt = 0;
    let rcvCnt = 0;
    let sndCnt = 0;
    let rplCnt = 0;

    for (const t of threads) {
        const g = classifyStateGroup(t.state);
        if (g === 'RUN') runCnt += 1;
        else if (g === 'RCV') rcvCnt += 1;
        else if (g === 'SND') sndCnt += 1;
        else if (g === 'RPL') rplCnt += 1;
    }

    const totalThreads = threads.length || (Number(sys.num_threads) || 0);
    const totalProcs = Number(sys.num_processes) || 0;
    const denom = Math.max(1, totalThreads);

    const activeRatio = connected ? Math.min(1, (runCnt + sndCnt + rplCnt) / Math.max(4, denom * 0.4)) : 0;
    const ipcRatio = connected ? Math.min(1, (rcvCnt + sndCnt + rplCnt) / Math.max(4, denom)) : 0;

    state.activeHistory.shift();
    state.activeHistory.push(activeRatio);
    state.ipcHistory.shift();
    state.ipcHistory.push(ipcRatio);

    const activeSpark = $('braille-sparkline-active');
    const ipcSpark = $('braille-sparkline-ipc');
    const statsEl = $('braille-stats');
    if (activeSpark) activeSpark.textContent = renderBrailleSparkline(state.activeHistory);
    if (ipcSpark) ipcSpark.textContent = renderBrailleSparkline(state.ipcHistory);
    if (statsEl) {
        const actPct = Math.round(activeRatio * 100);
        const ipcPct = Math.round(ipcRatio * 100);
        statsEl.textContent = connected ? `ACT ${actPct}% · IPC ${ipcPct}%` : 'OFFLINE';
    }

    const setMeter = (meterId, countId, value, ratio) => {
        const m = $(meterId);
        const c = $(countId);
        if (m) m.textContent = renderAsciiMeter(connected ? ratio : 0, 16);
        if (c) c.textContent = String(value);
    };

    setMeter('meter-threads', 'metric-threads', totalThreads, Math.min(1, totalThreads / 120));
    setMeter('meter-procs', 'metric-procs', totalProcs, Math.min(1, totalProcs / 60));
    setMeter('meter-run', 'cnt-run', runCnt, runCnt / denom);
    setMeter('meter-rcv', 'cnt-rcv', rcvCnt, rcvCnt / denom);
    setMeter('meter-snd', 'cnt-snd', sndCnt, sndCnt / denom);
    setMeter('meter-rpl', 'cnt-rpl', rplCnt, rplCnt / denom);
}

/* ============================================================================
 * Panel 4: pidin Process & Thread Inspector Table (2-way Sync)
 * ========================================================================= */
function renderProcTable(threads, connected) {
    const tbody = $('proc-table-body');
    const cntEl = $('proc-table-count');
    if (!tbody) return;

    if (!connected) {
        if (cntEl) cntEl.textContent = '0/0';
        tbody.innerHTML = '<tr><td colspan="5" style="color:#bf616a;text-align:center;padding:14px;">[OFFLINE] QNX 8.0 target disconnected — zero-simulation policy active.</td></tr>';
        return;
    }

    const filter = state.procFilterText;
    const filtered = !filter
        ? threads
        : threads.filter((t) => {
              const hay = `${t.pid}:${t.tid} ${t.name || ''} ${t.state || ''} ${t.priority ?? ''} ${t.chid || 0} ${t.target_pid || 0}`.toLowerCase();
              return hay.includes(filter);
          });

    if (cntEl) cntEl.textContent = `${filtered.length}/${threads.length}`;

    if (!filtered.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="color:#81a1c1;text-align:center;padding:14px;">No QNX threads match filter.</td></tr>';
        return;
    }

    // Prioritize deadlocked & IPC-active threads at the top for fast visibility
    const sorted = [...filtered].sort((a, b) => {
        if (Boolean(a.is_deadlocked) !== Boolean(b.is_deadlocked)) return a.is_deadlocked ? -1 : 1;
        const aTarget = Number(a.target_pid) > 0 ? 1 : 0;
        const bTarget = Number(b.target_pid) > 0 ? 1 : 0;
        if (aTarget !== bTarget) return bTarget - aTarget;
        return (Number(a.pid) - Number(b.pid)) || (Number(a.tid) - Number(b.tid));
    });

    tbody.innerHTML = sorted
        .map((t) => {
            const key = threadKey(t);
            const selectedClass = state.selectedNodeKey === key ? ' class="proc-selected"' : '';
            const stateColor = stateBadgeColor(t.state, t.is_deadlocked);
            const stateLabel = t.is_deadlocked ? `${t.state} [LOCK]` : (t.state || '--');
            const chidCoid = `${t.chid || 0}/${t.coid || 0}` + (t.target_pid ? ` → ${t.target_pid}` : '');
            return `<tr data-thread-key="${escapeHtml(key)}"${selectedClass}>
                <td>${escapeHtml(key)}</td>
                <td title="${escapeHtml(t.name)}">${escapeHtml(t.name || 'proc')}</td>
                <td style="color:${stateColor};font-weight:600;">${escapeHtml(stateLabel)}</td>
                <td>${escapeHtml(t.priority ?? '--')}</td>
                <td>${escapeHtml(chidCoid)}</td>
            </tr>`;
        })
        .join('');

    tbody.querySelectorAll('tr[data-thread-key]').forEach((row) => {
        row.addEventListener('click', () => {
            selectThreadByKey(row.dataset.threadKey, true);
        });
    });
}

function selectThreadByKey(key, panGraph) {
    state.selectedNodeKey = key;

    // Highlight table row
    const tbody = $('proc-table-body');
    if (tbody) {
        tbody.querySelectorAll('tr[data-thread-key]').forEach((tr) => {
            const active = tr.dataset.threadKey === key;
            tr.classList.toggle('proc-selected', active);
            if (active && !panGraph) {
                tr.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            }
        });
    }

    const threads = state.latestTelemetry?.threads || [];
    const threadObj = threads.find((t) => threadKey(t) === key);
    updateNodeInspector(threadObj, key);

    if (panGraph && state.cy && key) {
        state.cy.$(':selected').unselect();
        const node = state.cy.getElementById(key);
        if (node && node.length) {
            node.select();
            state.cy.animate({ center: { eles: node }, duration: 250 });
        }
    }
}

function updateNodeInspector(t, fallbackKey) {
    const titleEl = $('inspect-title');
    const detailsEl = $('inspect-details');
    if (!titleEl || !detailsEl) return;

    if (!t) {
        titleEl.textContent = fallbackKey ? `Selected: ${fallbackKey}` : 'Click a node or thread row to inspect';
        detailsEl.textContent = '';
        return;
    }

    titleEl.textContent = `${t.name || 'thread'} (${t.pid}:${t.tid})`;
    const targetInfo = t.target_pid ? `Blocked on PID ${t.target_pid}:${t.target_tid || 1}` : 'No active IPC block target';
    detailsEl.textContent = `State: ${t.state} | Prio: ${t.priority} | ChID/Coid: ${t.chid || 0}/${t.coid || 0} | ${targetInfo}`;
}

/* ============================================================================
 * Panel 2: Cytoscape IPC Topology Engine
 * ========================================================================= */
function initCytoscape() {
    const container = $('cy-container');
    if (!container || typeof cytoscape === 'undefined') return;

    state.cy = cytoscape({
        container,
        elements: [],
        wheelSensitivity: 0.2,
        style: [
            {
                selector: 'node',
                style: {
                    label: 'data(label)',
                    'background-color': '#181e28',
                    'border-color': '#88c0d0',
                    'border-width': 2,
                    color: '#e5e9f0',
                    'font-family': 'JetBrains Mono, Consolas, monospace',
                    'font-size': 10,
                    'text-valign': 'bottom',
                    'text-margin-y': 5,
                    width: 26,
                    height: 26,
                },
            },
            {
                selector: 'node[group="SND"]',
                style: { 'border-color': '#ebcb8b' },
            },
            {
                selector: 'node[group="RUN"]',
                style: { 'border-color': '#a3be8c' },
            },
            {
                selector: 'node[group="RPL"]',
                style: { 'border-color': '#b48ead' },
            },
            {
                selector: 'node[?deadlocked]',
                style: {
                    'background-color': '#3b151a',
                    'border-color': '#bf616a',
                    'border-width': 3,
                    width: 32,
                    height: 32,
                },
            },
            {
                selector: 'node[?priorityInverted]',
                style: {
                    'border-color': '#d08770',
                    'border-style': 'dashed',
                    'border-width': 3,
                },
            },
            {
                selector: 'node:selected',
                style: {
                    'border-color': '#eceff4',
                    'border-width': 3,
                    'background-color': '#2e3440',
                },
            },
            {
                selector: 'edge',
                style: {
                    width: 1.8,
                    'line-color': '#5e81ac',
                    'target-arrow-color': '#5e81ac',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'bezier',
                    label: 'data(label)',
                    'font-family': 'JetBrains Mono, Consolas, monospace',
                    'font-size': 8,
                    color: '#81a1c1',
                    'text-rotation': 'autorotate',
                },
            },
            {
                selector: 'edge[?deadlocked]',
                style: {
                    width: 3,
                    'line-color': '#bf616a',
                    'target-arrow-color': '#bf616a',
                },
            },
            {
                selector: 'edge[?priorityInverted]',
                style: {
                    width: 2.4,
                    'line-color': '#d08770',
                    'target-arrow-color': '#d08770',
                    'line-style': 'dashed',
                },
            },
        ],
    });

    state.cy.on('tap', 'node', (evt) => {
        const id = evt.target.id();
        selectThreadByKey(id, false);
    });
}

function updateTopologyGraph(data, detectiveRes, forceLayout) {
    const cy = state.cy;
    if (!cy) return;

    if (!data || !data.connected) {
        if (cy.elements().length) cy.elements().remove();
        state.lastStructuralSig = null;
        return;
    }

    const threads = Array.isArray(data.threads) ? data.threads : [];
    const deadlockedSet = new Set(
        Array.isArray(detectiveRes?.affected_threads) ? detectiveRes.affected_threads.map(String) : []
    );
    const piSet = new Set();
    const inversions = detectiveRes?.priority_inversions || [];
    for (const pi of inversions) {
        if (pi && typeof pi === 'object') {
            if (pi.client) piSet.add(String(pi.client));
            if (pi.server) piSet.add(String(pi.server));
            if (pi.blocked_thread) piSet.add(String(pi.blocked_thread));
            if (pi.blocker_thread) piSet.add(String(pi.blocker_thread));
        }
    }

    // Build focused IPC graph: include threads with active IPC targets, deadlocks, priority inversions, or unique named servers
    const threadByKey = new Map();
    const threadsByPid = new Map();
    for (const t of threads) {
        const k = threadKey(t);
        threadByKey.set(k, t);
        if (!threadsByPid.has(t.pid)) threadsByPid.set(t.pid, []);
        threadsByPid.get(t.pid).push(t);
    }

    const includedKeys = new Set();
    const edges = [];

    for (const t of threads) {
        const srcKey = threadKey(t);
        const srcLocked = Boolean(t.is_deadlocked || deadlockedSet.has(srcKey));
        if (srcLocked || piSet.has(srcKey)) includedKeys.add(srcKey);
        if (Number(t.target_pid) > 0) {
            includedKeys.add(srcKey);
            let dstKey = `${t.target_pid}:${t.target_tid || 1}`;
            if (!threadByKey.has(dstKey)) {
                const candidates = threadsByPid.get(Number(t.target_pid)) || [];
                if (candidates.length) dstKey = threadKey(candidates[0]);
            }
            if (threadByKey.has(dstKey)) {
                includedKeys.add(dstKey);
                const dstThread = threadByKey.get(dstKey);
                const dstLocked = Boolean(dstThread?.is_deadlocked || deadlockedSet.has(dstKey));
                const edgeDeadlocked = srcLocked && dstLocked;
                const edgePi = piSet.has(srcKey) && piSet.has(dstKey);
                edges.push({
                    id: `e:${srcKey}->${dstKey}`,
                    source: srcKey,
                    target: dstKey,
                    label: edgeDeadlocked ? 'DEADLOCK' : 'MsgSend',
                    deadlocked: edgeDeadlocked,
                    priorityInverted: edgePi,
                });
            }
        }
    }

    // Also include up to 12 representative active kernel/system processes so live VM always shows rich nodes even when healthy
    if (includedKeys.size < 12) {
        for (const t of threads) {
            if (includedKeys.size >= 16) break;
            if (t.tid === 1 || Number(t.chid) > 0) {
                includedKeys.add(threadKey(t));
            }
        }
    }

    const nodeList = Array.from(includedKeys)
        .map((k) => threadByKey.get(k))
        .filter(Boolean)
        .map((t) => {
            const k = threadKey(t);
            const shortName = (t.name || 'proc').split('/').pop();
            return {
                id: k,
                label: `${shortName} (${k})`,
                group: classifyStateGroup(t.state),
                deadlocked: Boolean(t.is_deadlocked || deadlockedSet.has(k)),
                priorityInverted: piSet.has(k),
            };
        });

    const structuralSig = [
        nodeList.map((n) => n.id).sort().join(','),
        edges.map((e) => e.id).sort().join(','),
    ].join('||');

    if (!forceLayout && structuralSig === state.lastStructuralSig) {
        // Update attributes cleanly in-place without disturbing layout or pan/zoom
        cy.batch(() => {
            for (const n of nodeList) {
                const ele = cy.getElementById(n.id);
                if (ele.length) ele.data(n);
            }
            for (const e of edges) {
                const ele = cy.getElementById(e.id);
                if (ele.length) ele.data(e);
            }
        });
        return;
    }

    state.lastStructuralSig = structuralSig;
    const elements = [
        ...nodeList.map((d) => ({ data: d })),
        ...edges.map((d) => ({ data: d })),
    ];

    cy.batch(() => {
        cy.elements().remove();
        cy.add(elements);
    });

    if (elements.length > 0) {
        cy.layout({
            name: 'cose',
            animate: false,
            nodeRepulsion: 6000,
            idealEdgeLength: 90,
            padding: 28,
        }).run();
    }

    if (state.selectedNodeKey) {
        const selected = cy.getElementById(state.selectedNodeKey);
        if (selected && selected.length) selected.select();
    }
}

/* ============================================================================
 * Panel 3: Deadlock Detective, Priority Inversion Auditor, AI Hot-Patch & Benchmark
 * ========================================================================= */
function renderDetectiveOffline() {
    const badge = $('deadlock-badge');
    const dot = $('deadlock-dot');
    const txt = $('deadlock-status-text');
    const panel = $('deadlock-panel');
    const icon = $('deadlock-panel-icon');
    const tag = $('deadlock-panel-tag');
    const details = $('deadlock-details');
    const piBadge = $('pi-count-badge');
    const piList = $('pi-auditor-list');
    const aiBox = $('ai-remediation-box');

    state.justResolvedByHotpatch = false;
    state.hotpatchResolutionSummary = '';

    if (badge) {
        badge.className = 'zoc-pill zoc-pill-muted';
    }
    if (dot) dot.className = 'zoc-dot bg-slate-500';
    if (txt) txt.textContent = 'IPC: Offline';
    if (panel) panel.classList.remove('panel-alert');
    if (icon) icon.textContent = '🛡';
    if (tag) tag.textContent = 'STATUS: OFFLINE';
    if (details) {
        details.innerHTML = '<p class="text-slate-400">Connect QNX 8.0 target to audit MsgSend cycles &amp; priority inversions.</p>';
    }
    if (piBadge) {
        piBadge.className = 'pi-badge pi-clean';
        piBadge.textContent = 'PI: 0';
    }
    if (piList) {
        piList.innerHTML = '';
        piList.classList.add('hidden');
    }
    if (aiBox) aiBox.classList.add('hidden');
}

function formatCycleChain(cycle) {
    const threads = state.latestTelemetry?.threads || [];
    const nameMap = new Map();
    for (const t of threads) {
        const shortName = String(t.name || 'proc').split('/').pop();
        nameMap.set(threadKey(t), shortName);
    }

    let nodes = Array.isArray(cycle.cycle_nodes) && cycle.cycle_nodes.length
        ? cycle.cycle_nodes
        : String(cycle.cycle_path || '').split(/\s*->\s*/).filter(Boolean);

    if (!nodes.length && cycle.cycle_path) return escapeHtml(cycle.cycle_path);

    return nodes
        .map((id) => {
            const key = String(id).trim();
            const procName = nameMap.get(key);
            const display = procName ? `${procName} (${key})` : key;
            return `<span class="text-rose-200">${escapeHtml(display)}</span>`;
        })
        .join(' <span class="text-amber-300 font-normal">──[MsgSend WAIT]──►</span> ');
}

async function runDetective(manual = false) {
    if (state.detectivePromise) {
        if (!manual) return await state.detectivePromise;
        try { await state.detectivePromise; } catch (_) {}
    }
    if (!state.latestTelemetry || !state.latestTelemetry.connected) {
        if (manual) {
            renderDetectiveOffline();
            setActionFeedback(
                'warn',
                'Cannot run Deadlock & Priority Inversion audit while QNX 8.0 QEMU VM is offline (127.0.0.1:2222 closed).'
            );
        }
        return null;
    }

    state.detectiveInFlight = true;
    const tag = $('deadlock-panel-tag');
    if (manual) {
        setButtonBusy('btn-run-detective', true, 'Auditing...');
        if (tag) tag.textContent = 'STATUS: SCANNING...';
        setActionFeedback(
            'running',
            'Auditing /dev/qnx_inspector threads via Tarjan cycle detection & Priority Inversion scanner...'
        );
    }

    const work = (async () => {
        try {
            const resp = await fetch('/api/detective', { method: 'POST' });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const res = await resp.json();
            state.latestDetective = res;
            renderDetectiveResult(res);
            updateTopologyGraph(state.latestTelemetry, res, false);

            if (manual) {
                const threadCnt = state.latestTelemetry?.threads?.length || 0;
                const procCnt = state.latestTelemetry?.system?.num_processes || 0;
                const cycCnt = Array.isArray(res.cycles) ? res.cycles.length : (res.deadlock_detected ? 1 : 0);
                const piCnt = Number(res.priority_inversion_count ?? (res.priority_inversions?.length || 0)) || 0;
                const level = cycCnt > 0 ? 'crit' : piCnt > 0 ? 'warn' : 'ok';

                const quickBtns = [{ label: '📊 View Cycles', panelId: 'deadlock-panel' }];
                if (cycCnt > 0) {
                    quickBtns.unshift({ label: '🎯 Focus Graph', onClick: zoomToDeadlockedNodes });
                    zoomToDeadlockedNodes();
                } else {
                    quickBtns.push({ label: '🔍 Focus Graph', panelId: 'panel-ipc-topology' });
                }
                focusAndFlashPanel('deadlock-panel');
                setActionFeedback(
                    level,
                    `Scanned ${threadCnt} kernel threads across ${procCnt} processes ➔ Found ${cycCnt} deadlock cycle(s), ${piCnt} priority inversion(s).`,
                    quickBtns
                );
            }

            return res;
        } catch (err) {
            if (tag) tag.textContent = 'STATUS: ERROR';
            const details = $('deadlock-details');
            if (details) details.textContent = `Detective audit error: ${err.message}`;
            if (manual) {
                setActionFeedback('crit', `Deadlock audit failed: ${err.message}`);
            }
            return null;
        } finally {
            state.detectiveInFlight = false;
            state.detectivePromise = null;
            if (manual) setButtonBusy('btn-run-detective', false);
        }
    })();

    state.detectivePromise = work;
    return await work;
}

function renderDetectiveResult(res) {
    if (!res) return;
    const badge = $('deadlock-badge');
    const dot = $('deadlock-dot');
    const txt = $('deadlock-status-text');
    const panel = $('deadlock-panel');
    const icon = $('deadlock-panel-icon');
    const tag = $('deadlock-panel-tag');
    const details = $('deadlock-details');
    const aiBox = $('ai-remediation-box');
    const aiText = $('ai-remediation-text');
    const piBadge = $('pi-count-badge');
    const piList = $('pi-auditor-list');

    const cycles = Array.isArray(res.cycles) ? res.cycles : [];
    const hasDeadlock = Boolean(res.deadlock_detected || cycles.length > 0);
    const inversions = Array.isArray(res.priority_inversions) ? res.priority_inversions : [];
    const piCount = Number(res.priority_inversion_count ?? inversions.length) || 0;

    if (hasDeadlock) {
        state.justResolvedByHotpatch = false;
    }

    if (badge) {
        badge.className = `zoc-pill ${hasDeadlock ? 'zoc-pill-red pulse-red' : piCount > 0 ? 'zoc-pill-amber' : 'zoc-pill-green'}`;
    }
    if (dot) {
        dot.className = `zoc-dot ${hasDeadlock ? 'bg-rose-400 animate-pulse' : piCount > 0 ? 'bg-amber-400' : 'bg-emerald-400'}`;
    }
    if (txt) {
        txt.textContent = hasDeadlock
            ? `IPC: DEADLOCK (${cycles.length} CYCLE)`
            : piCount > 0
            ? `IPC: PRIO INVERSION (${piCount})`
            : 'IPC: Nominal';
    }

    if (panel) panel.classList.toggle('panel-alert', hasDeadlock);
    if (icon) icon.textContent = hasDeadlock ? '🚨' : piCount > 0 ? '⚠️' : '🛡';
    if (tag) tag.textContent = hasDeadlock ? 'STATUS: CRITICAL' : piCount > 0 ? 'STATUS: WARN' : 'STATUS: CLEAN';

    if (details) {
        if (hasDeadlock && cycles.length > 0) {
            const summaryLine = res.summary || `Detected ${cycles.length} circular IPC deadlock(s) involving ${(res.affected_threads || []).length} threads.`;
            const cyclesHtml = cycles
                .map((c, idx) => {
                    const chain = formatCycleChain(c);
                    const desc = c.description ? `<div class="cycle-desc">${escapeHtml(c.description)}</div>` : '';
                    return `<div class="cycle-card">
                        <div class="text-[10px] font-bold text-rose-400 mb-0.5">CYCLE #${idx + 1}:</div>
                        <div class="cycle-chain">${chain}</div>
                        ${desc}
                    </div>`;
                })
                .join('');

            details.innerHTML = `
                <div class="font-bold text-rose-300">${escapeHtml(summaryLine)}</div>
                ${cyclesHtml}
                <div class="pt-1">
                    <button id="btn-zoom-deadlock-cycle" type="button" class="zoc-btn zoc-btn-red btn-xs">🎯 Zoom to Deadlocked Threads on Graph</button>
                </div>
            `;
            $('btn-zoom-deadlock-cycle')?.addEventListener('click', zoomToDeadlockedNodes);
        } else if (!hasDeadlock && state.justResolvedByHotpatch) {
            details.innerHTML = `
                <div class="resolved-banner">
                    ✔ RESOLVED BY AI HOT-PATCH — ${escapeHtml(state.hotpatchResolutionSummary || 'Circular deadlock cleared; healthy non-blocking IPC verified (0 cycles).')}
                </div>
            `;
        } else {
            const msg = res.diagnosis || res.summary || 'No circular MsgSend wait-for cycles detected in QNX message-passing pipeline.';
            details.innerHTML = `<p class="text-slate-300">${escapeHtml(msg)}</p>`;
        }
    }

    if (piBadge) {
        piBadge.className = `pi-badge ${piCount > 0 ? 'pi-warn' : 'pi-clean'}`;
        piBadge.textContent = `PI: ${piCount}`;
    }

    if (piList) {
        piList.classList.toggle('hidden', inversions.length === 0);
        if (!inversions.length) {
            piList.innerHTML = '';
        } else {
            piList.innerHTML = inversions
                .map((pi) => {
                    if (typeof pi === 'string') {
                        return `<div class="pi-item">▸ ${escapeHtml(pi)}</div>`;
                    }
                    const highName = pi.client_name || pi.blocked_name || pi.client || 'client';
                    const lowName = pi.server_name || pi.blocker_name || pi.server || 'server';
                    const highPrio = pi.client_priority ?? pi.blocked_priority ?? '?';
                    const lowPrio = pi.server_priority ?? pi.blocker_priority ?? '?';
                    const reason = pi.description || `${highName} (prio ${highPrio}) blocked on ${lowName} (prio ${lowPrio})`;
                    return `<div class="pi-item">▸ [PRIO INV] ${escapeHtml(reason)}</div>`;
                })
                .join('');
        }
    }

    if (aiBox && aiText) {
        const advice = res.remediation_advice || res.remediation;
        if (hasDeadlock || piCount > 0) {
            aiBox.classList.remove('hidden');
            aiText.textContent =
                advice ||
                'Circular blocking detected between synchronous MsgSend() threads. Click [AI Hot-Patch (QCC)] to slay deadlocked binary, compile asymmetric non-blocking ipc_healthy_demo via QCC, and deploy to target.';
        } else {
            aiBox.classList.add('hidden');
        }
    }
}

async function triggerAiAutofix() {
    if (!state.latestTelemetry || !state.latestTelemetry.connected) {
        setActionFeedback('warn', 'Cannot execute QCC AI Hot-Patch while QNX 8.0 target is offline.');
        return;
    }

    const outEl = $('hotpatch-output');
    const aiBox = $('ai-remediation-box');

    setButtonBusy('btn-ai-autofix', true, 'Hot-Patching...');
    setButtonBusy('btn-ai-autofix-inline', true, 'QCC Patching...');
    setActionFeedback(
        'running',
        'Slaying deadlocked process ➔ cross-compiling ipc_healthy_demo.c with qcc -Vgcc_ntox86_64 ➔ SCP deploying to target...'
    );

    if (outEl) {
        outEl.classList.remove('hidden');
        outEl.textContent = '[QCC HOT-PATCH] Terminating deadlock -> qcc -Vgcc_ntox86_64 compile -> SCP deploy -> executing ipc_healthy_demo...';
    }

    try {
        const resp = await fetch('/api/remediate/deadlock', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
        }
        const summary = [
            `[QCC] ${data.qcc_command || 'qcc -Vgcc_ntox86_64'} -> exit ${data.qcc_exit_code}`,
            `[SLAY] ${data.slayed ? 'ipc_deadlock_demo terminated' : 'no active deadlock binary slain'}`,
            `[SCP] ${data.upload_message || data.target_deployed || 'deployed'}`,
            data.execution_stdout ? `[EXEC]\n${data.execution_stdout.trim()}` : `[STATUS] ${data.message || 'Done'}`,
        ].join('\n');
        if (outEl) {
            outEl.classList.remove('hidden');
            outEl.textContent = summary;
        }

        await fetchTelemetry();
        const postRes = await runDetective(false);
        const postHasCycles = Boolean(
            postRes && (postRes.deadlock_detected || (Array.isArray(postRes.cycles) && postRes.cycles.length > 0))
        );

        if (!postHasCycles) {
            state.justResolvedByHotpatch = true;
            state.hotpatchResolutionSummary = `Slayed ipc_deadlock_demo, built ipc_healthy_demo (qcc exit ${data.qcc_exit_code}), deployed via SCP — 0 deadlock cycles remaining.`;
            if (aiBox) aiBox.classList.add('hidden');
            if (postRes) renderDetectiveResult(postRes);
            focusAndFlashPanel('deadlock-panel');
            setActionFeedback(
                'ok',
                `✔ RESOLVED BY AI HOT-PATCH — qcc built & deployed ipc_healthy_demo (0 circular waits remaining).`,
                [
                    { label: '📊 View Resolution', panelId: 'deadlock-panel' },
                    { label: '🔍 Focus Graph', panelId: 'panel-ipc-topology' },
                ]
            );
        } else {
            focusAndFlashPanel('deadlock-panel');
            setActionFeedback(
                'warn',
                `Hot-patch deployed, but ${(postRes.cycles || []).length} cycle(s) still remain on target.`,
                [{ label: '📊 View Cycles', panelId: 'deadlock-panel' }]
            );
        }
    } catch (err) {
        if (outEl) {
            outEl.classList.remove('hidden');
            outEl.textContent = `[HOT-PATCH ERROR] ${err.message}`;
        }
        setActionFeedback('crit', `AI Hot-Patch failed: ${err.message}`, [
            { label: '📊 View Panel', panelId: 'deadlock-panel' },
        ]);
    } finally {
        setButtonBusy('btn-ai-autofix', false);
        setButtonBusy('btn-ai-autofix-inline', false);
    }
}

async function triggerBenchmark(clickedBtnId = 'btn-trigger-benchmark') {
    if (!state.latestTelemetry || !state.latestTelemetry.connected) {
        setActionFeedback('warn', 'Cannot execute ClockCycles() IPC benchmark while QNX 8.0 target is offline.');
        return;
    }

    const statusEl = $('bench-status');
    setButtonBusy('btn-trigger-benchmark', true, 'Benchmarking...');
    setButtonBusy('btn-bench-inline', true, 'RUN...');
    setActionFeedback(
        'running',
        'Executing 1,000x 64B MsgSend/MsgReceive/MsgReply round-trips on QNX 8.0 using CPU ClockCycles()...'
    );
    if (statusEl) statusEl.textContent = 'Running 1,000x 64B MsgSend() ClockCycles() round-trips on QNX target...';

    try {
        const resp = await fetch('/api/trigger/benchmark', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
            throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
        }

        const m = data.metrics || data;
        const avgNs = Number(m.avg_latency_ns || 0).toFixed(1);
        const msgSec = Math.round(Number(m.throughput_msg_sec || 0)).toLocaleString();
        const hzTxt = formatFreq(m.cycles_per_sec);

        if ($('bench-latency-ns')) $('bench-latency-ns').textContent = `${avgNs} ns`;
        if ($('bench-throughput')) $('bench-throughput').textContent = `${msgSec} msg/s`;
        if ($('bench-payload')) $('bench-payload').textContent = `${m.payload_bytes || 64} B`;
        if ($('bench-cps')) $('bench-cps').textContent = hzTxt;
        if ($('bench-min-cyc')) $('bench-min-cyc').textContent = Number(m.min_cycles || 0).toLocaleString();
        if ($('bench-avg-cyc')) $('bench-avg-cyc').textContent = Math.round(Number(m.avg_cycles || 0)).toLocaleString();
        if ($('bench-max-cyc')) $('bench-max-cyc').textContent = Number(m.max_cycles || 0).toLocaleString();
        if (statusEl) statusEl.textContent = `[PASS] Completed ${m.iterations || 1000} iterations directly on QNX 8.0 hardware clock.`;

        await fetchTelemetry();
        focusAndFlashPanel('deadlock-panel');
        setActionFeedback(
            'ok',
            `ClockCycles() IPC Benchmark [PASS]: ${avgNs} ns avg RTT (${msgSec} msg/s @ ${hzTxt}, 1,000x 64B MsgSend).`,
            [
                { label: '⏱ View Bench', panelId: 'deadlock-panel' },
                { label: '📜 View Slog2', panelId: 'panel-slog2', scrollSlog: true },
            ]
        );
    } catch (err) {
        if (statusEl) statusEl.textContent = `[BENCH ERROR] ${err.message}`;
        setActionFeedback('crit', `Benchmark failed: ${err.message}`);
    } finally {
        setButtonBusy('btn-trigger-benchmark', false);
        setButtonBusy('btn-bench-inline', false);
    }
}

/* ============================================================================
 * QEMU Start & Target Demo Triggers
 * ========================================================================= */
async function handleStartQemu() {
    const overlayBtn = $('btn-start-qemu-overlay');

    state.isBootingQemu = true;
    setButtonBusy('btn-start-qemu', true, 'Booting VM...');
    if (overlayBtn) overlayBtn.disabled = true;
    setActionFeedback('running', 'Booting QNX Neutrino RTOS 8.0 in QEMU & waiting for SSH 127.0.0.1:2222...');

    try {
        const resp = await fetch('/api/qemu/start', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);
        await fetchTelemetry();
        setActionFeedback('ok', data.message || 'QNX 8.0 QEMU VM online and provisioned (/dev/qnx_inspector mounted).', [
            { label: '🔍 Focus Graph', panelId: 'panel-ipc-topology' },
        ]);
    } catch (err) {
        const consoleEl = $('slog2-console');
        if (consoleEl) {
            consoleEl.innerHTML += `<div style="color:#bf616a;">[QEMU START ERROR] ${escapeHtml(err.message)}</div>`;
        }
        setActionFeedback('crit', `Failed to boot QNX 8.0 QEMU VM: ${err.message}`);
    } finally {
        state.isBootingQemu = false;
        setButtonBusy('btn-start-qemu', false);
        if (overlayBtn) overlayBtn.disabled = false;
        if (state.latestTelemetry) {
            updateConnectionChrome(Boolean(state.latestTelemetry.connected), state.latestTelemetry.system || {});
        }
    }
}

async function triggerDemo(mode) {
    // Always clear stale hot-patch logs and prior resolution banner whenever starting a new demo
    const hotpatchEl = $('hotpatch-output');
    if (hotpatchEl) {
        hotpatchEl.classList.add('hidden');
        hotpatchEl.textContent = '';
    }
    state.justResolvedByHotpatch = false;
    state.hotpatchResolutionSummary = '';

    const configMap = {
        deadlock: {
            btnId: 'btn-trigger-deadlock',
            busy: 'Injecting Deadlock...',
            runMsg: 'Slaying prior demo processes & spawning mutual MsgSend() circular wait threads on QNX 8.0...',
        },
        crash: {
            btnId: 'btn-trigger-crash',
            busy: 'Injecting Fault...',
            runMsg: 'Executing slog2_crash_demo on QNX 8.0 & capturing critical slog2 buffer entries...',
        },
        healthy: {
            btnId: 'btn-trigger-healthy',
            busy: 'Starting Healthy...',
            runMsg: 'Stopping fault demos via sudo slay & launching non-blocking ipc_healthy_demo loop...',
        },
    };
    const cfg = configMap[mode] || { btnId: null, busy: 'Running...', runMsg: `Dispatching ${mode}...` };

    if (!state.latestTelemetry || !state.latestTelemetry.connected) {
        setActionFeedback('warn', `Cannot run ${mode} demo while QNX 8.0 QEMU target is offline.`);
        return;
    }

    if (cfg.btnId) setButtonBusy(cfg.btnId, true, cfg.busy);
    setActionFeedback('running', cfg.runMsg);

    try {
        const resp = await fetch(`/api/trigger/${mode}`, { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || data.error || `HTTP ${resp.status}`);

        let detRes = null;
        if (mode === 'deadlock' && data.telemetry) {
            applyTelemetry(data.telemetry);
            if (data.deadlock_analysis) {
                state.latestDetective = data.deadlock_analysis;
                renderDetectiveResult(data.deadlock_analysis);
                updateTopologyGraph(data.telemetry, data.deadlock_analysis, false);
                detRes = data.deadlock_analysis;
            } else {
                detRes = await runDetective(false);
            }
        } else if (mode === 'crash') {
            if (data.slog2_preview) {
                renderSlog2Console(data.slog2_preview, true);
            }
            await fetchTelemetry();
        } else {
            await fetchTelemetry();
            detRes = await runDetective(false);
        }

        if (mode === 'deadlock') {
            const cycCnt = detRes?.cycles?.length || 0;
            const thrCnt = detRes?.affected_threads?.length || 0;
            focusAndFlashPanel('deadlock-panel');
            if (cycCnt > 0) zoomToDeadlockedNodes();
            setActionFeedback(
                cycCnt > 0 ? 'crit' : 'warn',
                cycCnt > 0
                    ? `Injected IPC Deadlock: ${cycCnt} circular MsgSend cycle(s) detected across ${thrCnt} blocked thread(s).`
                    : `${data.message || 'Dispatched ipc_deadlock_demo'} — awaiting next telemetry frame.`,
                [
                    { label: '🎯 Focus Graph', onClick: zoomToDeadlockedNodes },
                    { label: '📊 View Cycles', panelId: 'deadlock-panel' },
                ]
            );
        } else if (mode === 'crash') {
            const previewLines = String(data.slog2_preview || '')
                .split(/\r?\n|\s*\|\s*/)
                .map((l) => l.trim())
                .filter(Boolean);
            const critLine =
                [...previewLines].reverse().find((l) => !/NO CRITICAL/i.test(l) && /CRIT|FAULT|SIGSEGV|ERROR|0XDEADBEEF|AEGIS/i.test(l)) ||
                previewLines[previewLines.length - 1] ||
                'slog2_crash_demo emitted buffer fault into /dev/slog2';

            focusAndFlashPanel('panel-slog2', true);
            setActionFeedback(
                'warn',
                `Slog2 Fault Injected ➔ <span class="text-rose-300 font-bold">${escapeHtml(critLine)}</span>`,
                [
                    { label: '📜 View Slog2', panelId: 'panel-slog2', scrollSlog: true },
                    { label: '📋 View Proc', panelId: 'panel-proc-table' },
                ],
                true
            );
        } else {
            focusAndFlashPanel('deadlock-panel');
            setActionFeedback(
                'ok',
                data.message || 'Healthy IPC running — ipc_healthy_demo active with 0 deadlock cycles.',
                [
                    { label: '🔍 Focus Graph', panelId: 'panel-ipc-topology' },
                    { label: '📋 View Proc', panelId: 'panel-proc-table' },
                ]
            );
        }
    } catch (err) {
        const consoleEl = $('slog2-console');
        if (consoleEl) {
            consoleEl.innerHTML += `<div style="color:#bf616a;">[TRIGGER ${escapeHtml(mode.toUpperCase())} ERROR] ${escapeHtml(err.message)}</div>`;
        }
        setActionFeedback('crit', `Failed triggering ${mode}: ${err.message}`);
    } finally {
        if (cfg.btnId) setButtonBusy(cfg.btnId, false);
    }
}

/* ============================================================================
 * Panel 5: slog2 Stream Console
 * ========================================================================= */
function renderSlog2Console(slogTail, connected) {
    const el = $('slog2-console');
    const cntEl = $('slog2-line-count');
    if (!el) return;

    const rawText = slogTail || (connected ? '(slog2info returned empty buffer)' : '[OFFLINE] Waiting for QNX 8.0 SSH telemetry...');
    const lines = String(rawText)
        .split(/\r?\n|\s*\|\s*/)
        .map((l) => l.trimEnd())
        .filter((l) => l.trim().length > 0);

    if (cntEl) cntEl.textContent = `${lines.length} lines`;

    const wasNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 48;

    el.innerHTML = lines
        .map((line) => {
            const upper = line.toUpperCase();
            const isNoCritical = upper.includes('NO CRITICAL') || upper.includes('NORMAL OPERATION');
            let color = '#d8dee9';
            if (!isNoCritical && (upper.includes('ERROR') || upper.includes('CRITICAL') || upper.includes('SIGSEGV') || upper.includes('DEADLOCK') || upper.includes('OFFLINE') || upper.includes('0XDEADBEEF'))) {
                color = '#bf616a';
            } else if (upper.includes('WARN') || upper.includes('INVERSION')) {
                color = '#ebcb8b';
            } else if (upper.includes('BENCHMARK') || upper.includes('HEARTBEAT') || upper.includes('PASS') || upper.includes('HEALTHY') || isNoCritical) {
                color = '#a3be8c';
            } else if (upper.includes('INFO') || upper.includes('[QNX-AEGIS]') || upper.includes('SLOG2')) {
                color = '#88c0d0';
            }
            return `<div style="color:${color};white-space:pre-wrap;word-break:break-word;">${escapeHtml(line)}</div>`;
        })
        .join('');

    if (wasNearBottom) {
        el.scrollTop = el.scrollHeight;
    }
}

/* ============================================================================
 * Tab 3: MCP JSON-RPC Studio
 * ========================================================================= */
async function loadMcpTools() {
    const listEl = $('mcp-tools-list');
    if (!listEl) return;
    listEl.innerHTML = '<div style="color:#81a1c1;">Loading FastMCP tool schemas from /api/mcp/tools...</div>';

    const t0 = performance.now();
    try {
        const resp = await fetch('/api/mcp/tools');
        const data = await resp.json();
        const elapsed = Math.round(performance.now() - t0);
        if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`);

        state.mcpTools = Array.isArray(data.tools) ? data.tools : [];
        if ($('mcp-studio-latency')) $('mcp-studio-latency').textContent = `TOOLS: ${state.mcpTools.length} · ${elapsed} ms`;

        if (!state.mcpTools.length) {
            listEl.innerHTML = '<div style="color:#ebcb8b;">No MCP tools returned.</div>';
            return;
        }

        listEl.innerHTML = state.mcpTools
            .map(
                (tool, idx) => `<button type="button" class="mcp-tool-item" data-idx="${idx}" style="display:block;width:100%;text-align:left;padding:8px;margin-bottom:6px;background:#11151d;border:1px solid #3b4252;color:#e5e9f0;cursor:pointer;font-family:inherit;">
                    <div style="color:#88c0d0;font-weight:700;font-size:12px;">${escapeHtml(tool.name)}</div>
                    <div style="color:#81a1c1;font-size:11px;margin-top:2px;line-height:1.35;">${escapeHtml((tool.description || '').slice(0, 120))}</div>
                </button>`
            )
            .join('');

        listEl.querySelectorAll('.mcp-tool-item').forEach((btn) => {
            btn.addEventListener('click', () => {
                selectMcpTool(Number(btn.dataset.idx));
            });
        });

        selectMcpTool(0);
    } catch (err) {
        listEl.innerHTML = `<div style="color:#bf616a;">Failed to load MCP tools: ${escapeHtml(err.message)}</div>`;
    }
}

function selectMcpTool(index) {
    const tool = state.mcpTools[index];
    if (!tool) return;
    state.selectedMcpTool = tool;

    document.querySelectorAll('#mcp-tools-list .mcp-tool-item').forEach((btn) => {
        const active = Number(btn.dataset.idx) === index;
        btn.style.borderColor = active ? '#88c0d0' : '#3b4252';
        btn.style.background = active ? '#1b222d' : '#11151d';
    });

    if ($('mcp-selected-tool')) $('mcp-selected-tool').textContent = tool.name;

    // Build default argument object from schema properties
    const defaultArgs = {};
    const props = tool.input_schema?.properties || {};
    for (const [k, spec] of Object.entries(props)) {
        if (spec && spec.default !== undefined) {
            defaultArgs[k] = spec.default;
        } else if (spec?.type === 'integer' || spec?.type === 'number') {
            defaultArgs[k] = 0;
        } else if (spec?.type === 'boolean') {
            defaultArgs[k] = false;
        } else if (spec?.type === 'array') {
            defaultArgs[k] = [];
        } else {
            defaultArgs[k] = '';
        }
    }

    const argsInput = $('mcp-args-input');
    if (argsInput) {
        argsInput.value = JSON.stringify(defaultArgs, null, 2);
    }
    updateMcpRequestPreview();
}

function updateMcpRequestPreview() {
    const reqEl = $('mcp-jsonrpc-request');
    const argsEl = $('mcp-args-input');
    if (!reqEl) return;

    let parsedArgs = {};
    try {
        parsedArgs = JSON.parse(argsEl?.value || '{}');
    } catch (_) {
        parsedArgs = { _invalid_json: true };
    }

    const payload = {
        jsonrpc: '2.0',
        id: state.rpcSeq,
        method: 'tools/call',
        params: {
            name: state.selectedMcpTool ? state.selectedMcpTool.name : 'select_a_tool',
            arguments: parsedArgs,
        },
    };
    reqEl.textContent = JSON.stringify(payload, null, 2);
}

async function invokeSelectedMcpTool() {
    if (!state.selectedMcpTool) return;
    const argsEl = $('mcp-args-input');
    const respEl = $('mcp-jsonrpc-response');
    const metaEl = $('mcp-response-meta');
    const btn = $('btn-invoke-mcp-tool');

    let parsedArgs = {};
    try {
        parsedArgs = JSON.parse(argsEl?.value || '{}');
    } catch (err) {
        if (respEl) respEl.textContent = `Argument JSON parse error: ${err.message}`;
        return;
    }

    const rpcId = state.rpcSeq++;
    updateMcpRequestPreview();

    if (btn) btn.disabled = true;
    if (metaEl) metaEl.textContent = 'Invoking...';

    const t0 = performance.now();
    try {
        const resp = await fetch('/api/mcp/call', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                id: rpcId,
                tool: state.selectedMcpTool.name,
                arguments: parsedArgs,
            }),
        });
        const data = await resp.json();
        const elapsed = Math.round(performance.now() - t0);
        if (metaEl) metaEl.textContent = `HTTP ${resp.status} · ${elapsed} ms`;
        if ($('mcp-studio-latency')) $('mcp-studio-latency').textContent = `LAST RPC: ${elapsed} ms`;
        if (respEl) respEl.textContent = JSON.stringify(data, null, 2);
    } catch (err) {
        if (metaEl) metaEl.textContent = 'ERR';
        if (respEl) respEl.textContent = `RPC failure: ${err.message}`;
    } finally {
        if (btn) btn.disabled = false;
    }
}

async function runQuickRestProbe(endpoint, method) {
    const reqEl = $('mcp-jsonrpc-request');
    const respEl = $('mcp-jsonrpc-response');
    const metaEl = $('mcp-response-meta');

    if (reqEl) reqEl.textContent = `${method} ${endpoint}\nAccept: application/json`;
    if (metaEl) metaEl.textContent = 'Requesting...';

    const t0 = performance.now();
    try {
        const resp = await fetch(endpoint, { method });
        const text = await resp.text();
        const elapsed = Math.round(performance.now() - t0);
        if (metaEl) metaEl.textContent = `HTTP ${resp.status} · ${elapsed} ms`;
        if ($('mcp-studio-latency')) $('mcp-studio-latency').textContent = `REST PROBE: ${elapsed} ms`;
        if (respEl) {
            try {
                respEl.textContent = JSON.stringify(JSON.parse(text), null, 2);
            } catch (_) {
                respEl.textContent = text;
            }
        }
    } catch (err) {
        if (metaEl) metaEl.textContent = 'ERR';
        if (respEl) respEl.textContent = `HTTP request error: ${err.message}`;
    }
}
