# QNX-Aegis (QNX-MCP Studio)
### Autonomous AI Agent & Real-Time Microkernel Bridge for QNX Neutrino RTOS 8.0

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![QNX SDP](https://img.shields.io/badge/QNX%20SDP-8.0-red.svg)](https://blackberry.qnx.com/en/products/neutrino-rtos)
[![Target](https://img.shields.io/badge/Target-x86__64%20(QEMU)-blue.svg)]()
[![Model Context Protocol](https://img.shields.io/badge/Protocol-MCP%20Standard-purple.svg)](https://modelcontextprotocol.io/)
[![Track](https://img.shields.io/badge/Hackathon-Track%204%3A%20Developer%20Tooling-green.svg)]()

> **QNX Global Hackathon Submission**  
> **Track:** Track 4: Developer Tooling & Host Tools  
> **Bonus Categories:** Best Use of QNX-Specific Technologies & Best Codelab/Tutorial

---

## 1. Executive Summary & Problem Statement

Developing safety-critical real-time applications on the **QNX Neutrino RTOS 8.0 microkernel** requires deep understanding of QNX's unique synchronous message-passing primitives (`MsgSendv`, `MsgReceivev`, `MsgReplyv`). Unlike monolithic operating systems (Linux, Windows) where threads typically block on OS mutexes or file descriptors, QNX threads spend significant portions of their lifecycle in microkernel IPC blocking states:
- `STATE_SEND`: Thread has called `MsgSend()`, waiting for the server to call `MsgReceive()`.
- `STATE_RECEIVE`: Server has called `MsgReceive()`, waiting for an incoming message on its channel.
- `STATE_REPLY`: Server has received message, client is blocked waiting for `MsgReply()`.

When complex distributed actor systems or resource managers communicate across mutual channels, **circular IPC deadlocks** can occur (Thread A `REPLY`-blocked on Thread B, while Thread B is `SEND`-blocked or `REPLY`-blocked on Thread A). These deadlocks cannot be detected by standard POSIX gdb watchdogs or external Linux debugging tools.

**QNX-Aegis** bridges this gap by marrying modern **AI Developer Agents** (via Anthropic's **Model Context Protocol - MCP**) directly with the **QNX 8.0 Microkernel**:
1. **Target Side (`qnx-target/`):** A lightweight C Resource Manager daemon exposing `/dev/qnx_inspector`. It inspects kernel thread states via `/proc`, reconstructs the IPC channel dependency graph, and queries the `slog2` high-performance ring buffer.
2. **Host Side (`mcp-server/`):** An MCP server & FastAPI telemetry bridge running on Windows. It orchestrates `qcc` cross-compilation (`-Vgcc_ntox86_64`), manages QEMU deployment over SSH, executes graph cycle detection algorithms to pinpoint exact circular wait chains, and exposes structured diagnostic tools directly to AI models (Claude, Cursor, Antigravity).
3. **Live Dashboard (`dashboard/`):** A real-time Cytoscape.js dark-mode canvas visualizing channel topologies, live thread blocking transitions, and instant AI automated fixes.

---

## 2. System Architecture

```mermaid
flowchart TB
    subgraph Host["Host Machine (Windows 11)"]
        subgraph AI["AI Coding Assistant"]
            LLM["LLM Agent (Claude Desktop / Cursor / Antigravity)"]
        end

        subgraph MCP["QNX-Aegis MCP Server & Telemetry Bridge"]
            Server["MCP FastMCP Server<br/>(JSON-RPC 2.0 / stdio / SSE)"]
            Compiler["QCC Cross-Compiler Engine<br/>(qcc -Vgcc_ntox86_64)"]
            Detective["Deadlock Detective<br/>(Tarjan's Directed Cycle Detection)"]
            Bridge["FastAPI & WebSocket Server<br/>(:8000)"]
            Paramiko["QEMU SSH/SCP Transport<br/>(Paramiko Bridge)"]
        end

        subgraph UI["Developer Host Tooling"]
            Dashboard["Real-Time Web Dashboard<br/>(Tailwind + Cytoscape.js)"]
        end
    end

    subgraph Target["QNX Neutrino RTOS 8.0 (QEMU x86_64)"]
        subgraph Kernel["QNX Microkernel"]
            IPC["MsgSend / MsgReceive / MsgReply State Engine"]
            ProcFS["/proc Process Manager & devctl Interface"]
            Slog2Buffer["slog2 Ring Buffer System"]
        end

        subgraph Daemons["Userland & Target Tools"]
            ResMgr["/dev/qnx_inspector<br/>(Native QNX Resource Manager)"]
            DemoApp["Target Applications / Deadlock Demos"]
            Slog2Info["slog2info Utility"]
        end
    end

    %% Communications
    LLM <-->|Model Context Protocol (MCP)| Server
    Server --> Compiler
    Server --> Detective
    Server <--> Paramiko
    Bridge <-->|WebSocket State Stream| Dashboard

    Paramiko <-->|SSH / SCP (Port 2222)| Target
    ResMgr <-->|devctl / procfs| ProcFS
    ResMgr <-->|slog2 API| Slog2Buffer
    DemoApp <-->|Sync IPC Channels| IPC
    Paramiko -->|Read JSON| ResMgr
```

---

## 3. Evidence of QNX 8.0 Use

QNX-Aegis is purpose-built for **QNX Neutrino RTOS 8.0** and relies fundamentally on core QNX microkernel technologies that do not exist in general POSIX or Linux environments.

| QNX 8.0 Technology | Target Source File | Exact APIs & Mechanisms Used | Description |
|---|---|---|---|
| **QNX Resource Manager** | [`qnx-target/inspector-daemon/main.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/inspector-daemon/main.c) | `dispatch_create()`, `resmgr_attach()`, `iofunc_func_init()`, `resmgr_context_alloc()`, `resmgr_block()` | Registers `/dev/qnx_inspector` in the QNX namespace to provide zero-copy JSON telemetry streaming via standard `io_read` callbacks. |
| **QNX Native IPC Passing** | [`qnx-target/demos/ipc_deadlock_demo.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/demos/ipc_deadlock_demo.c)<br/>[`qnx-target/demos/ipc_healthy_demo.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/demos/ipc_healthy_demo.c) | `ChannelCreate()`, `ConnectAttach()`, `MsgSend()`, `MsgReceive()`, `MsgReply()` | Implements deterministic synchronous message transactions and demonstrates mutual `STATE_REPLY` / `STATE_SEND` circular wait states. |
| **QNX slog2 (System Logger 2)** | [`qnx-target/inspector-daemon/slog2_reader.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/inspector-daemon/slog2_reader.c)<br/>[`qnx-target/demos/slog2_crash_demo.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/demos/slog2_crash_demo.c) | `slog2_register()`, `slog2fa()`, `slog2c()`, `slog2_buffer_t` | Emits high-frequency zero-copy structured system logs into categorized QNX 8.0 slog2 ring buffers and reads them via `slog2info`. |
| **Microkernel Process Inspection** | [`qnx-target/inspector-daemon/ipc_analyzer.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/inspector-daemon/ipc_analyzer.c) | `devctl(fd, DCMD_PROC_SYSINFO)`, `DCMD_PROC_TIDSTATUS`, `STATE_SEND`, `STATE_REPLY`, `STATE_RECEIVE` | Queries microkernel thread control blocks (`procnto`) directly to extract thread blocking states, channel IDs, and target server PIDs. |
| **QNX Cross-Compilation** | [`scripts/build-target.ps1`](file:///d:/UIT/NamBonUIT/QNX-Aegis/scripts/build-target.ps1)<br/>[`mcp-server/src/qcc_compiler.py`](file:///d:/UIT/NamBonUIT/QNX-Aegis/mcp-server/src/qcc_compiler.py) | `qcc -Vgcc_ntox86_64`, `qcc -Vllvm_ntox86_64`, `-lslog2` | Uses native QNX SDP 8.0 cross-compiler drivers with target architecture specifications and QNX library linkers. |

---

## 4. Repository Structure

```
QNX-Aegis/
├── LICENSE                                  # MIT Open Source License
├── README.md                                # Hackathon Overview, Evidence & Instructions
├── .gitignore                               # Build artifacts & environment ignores
├── .env.example                             # Configuration blueprint
│
├── docs/
│   ├── ARCHITECTURE.md                      # Microkernel internals, IPC state machine & MCP architecture
│   └── CODELAB.md                           # 15-minute hands-on tutorial for Hackathon judges & developers
│
├── scripts/
│   ├── setup-env.ps1                        # Verifies QNX SDP 8.0, qcc in PATH, and Python environment
│   ├── build-target.ps1                     # Cross-compiles C binaries with qcc (-Vgcc_ntox86_64)
│   └── deploy-to-qemu.ps1                   # Deploys binaries to QNX 8.0 QEMU via SCP
│
├── qnx-target/                              # Native QNX C source code
│   ├── Makefile                             # Target Makefile calling qcc
│   ├── inspector-daemon/                    # QNX Resource Manager (/dev/qnx_inspector)
│   │   ├── Makefile
│   │   ├── qnx_inspector.h                  # Process, Thread, IPC & Slog2 data structures
│   │   ├── main.c                           # Resource Manager setup (resmgr_attach) & io_read
│   │   ├── ipc_analyzer.c                   # Thread state parser (/proc devctl)
│   │   └── slog2_reader.c                   # slog2 ring buffer extractor
│   └── demos/                               # Diagnostic & demo binaries
│       ├── Makefile
│       ├── ipc_deadlock_demo.c              # Intentional circular MsgSend deadlock
│       ├── ipc_healthy_demo.c               # Clean ChannelCreate / MsgSend / MsgReply loop
│       └── slog2_crash_demo.c               # Slog2 warning logger + deliberate fault trigger
│
├── mcp-server/                              # Host-side Python Model Context Protocol Service
│   ├── pyproject.toml                       # Python package configuration
│   ├── requirements.txt                     # Dependencies: mcp, fastapi, paramiko, etc.
│   └── src/
│       ├── config.py                        # Environment parser
│       ├── qcc_compiler.py                  # qcc wrapper with structured compiler diagnostics
│       ├── qemu_bridge.py                   # SSH/SCP client communicating with QNX VM
│       ├── deadlock_detective.py            # Directed graph cycle detection for QNX IPC
│       ├── server.py                        # FastMCP tool implementation
│       └── api_bridge.py                    # FastAPI & WebSocket server for live dashboard
│
└── dashboard/                               # Real-Time Visualizer
    ├── index.html                           # Single-page responsive dark-mode UI
    ├── app.js                               # WebSocket receiver & Cytoscape.js IPC graph layout
    └── styles.css                           # Microkernel status colors & glowing telemetry styles
```

---

## 5. Quickstart Guide

### Prerequisites
1. **Windows 10/11 Host** with:
   - **QNX SDP 8.0** installed (typically `C:\QNX800`).
   - **Python 3.10+** installed.
   - **QEMU** running QNX Neutrino RTOS 8.0 (`x86_64` target), with SSH port forwarding enabled (host port `2222` -> guest port `22` or `2222`). Default user: `qnxuser` / password: `qnxuser`.

### Step 1: Environment Initialization
Open PowerShell as Administrator or regular user:
```powershell
# 1. Clone or navigate to the repository
cd d:\UIT\NamBonUIT\QNX-Aegis

# 2. Copy and configure environment variables
Copy-Item .env.example .env

# 3. Run the setup validation script
.\scripts\setup-env.ps1
```

### Step 2: Cross-Compile Target Binaries
Cross-compile the native QNX Resource Manager and Demo applications using `qcc`:
```powershell
.\scripts\build-target.ps1
```
This produces `qnx_inspector`, `ipc_deadlock_demo`, `ipc_healthy_demo`, and `slog2_crash_demo` inside `qnx-target/bin/`.

### Step 3: Deploy to QNX 8.0 QEMU
Deploy the cross-compiled binaries to the target VM over SSH/SCP:
```powershell
.\scripts\deploy-to-qemu.ps1
```

### Step 4: Launch Host MCP Server & Live Dashboard
1. Set up the Python virtual environment:
```powershell
cd mcp-server
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

2. Run the MCP Server & Dashboard API Bridge:
```powershell
python -m src.api_bridge
```

3. Open your browser and navigate to:
```
http://localhost:8000
```
You will see the live **QNX-Aegis Dashboard** showing microkernel uptime, channel topologies, thread blocking states, and the streaming `slog2` log console.

---

## 6. Model Context Protocol (MCP) AI Tools

QNX-Aegis exposes 6 standard MCP tools to AI assistants:

| MCP Tool Name | Parameters | Purpose |
|---|---|---|
| `qnx_verify_environment` | *none* | Verifies host `qcc` compiler path and tests SSH connectivity to QNX QEMU. |
| `qnx_compile_source` | `source_path`, `output_binary`, `extra_flags` | Cross-compiles C source on host via `qcc -Vgcc_ntox86_64` and returns compiler warnings/errors. |
| `qnx_deploy_and_execute` | `local_binary`, `remote_path`, `timeout_sec` | Transmits binary to QNX via SCP, sets execute permissions (`chmod +x`), and executes in background or foreground. |
| `qnx_inspect_microkernel` | *none* | Reads `/dev/qnx_inspector` and `pidin` to extract live process and thread states (`REPLY`, `SEND`, `RECEIVE`). |
| `qnx_detect_ipc_deadlock` | *none* | Builds directed graph of thread IPC waits and pinpoints exact circular deadlocks (e.g., `TID 1 -> TID 2 -> TID 1`). |
| `qnx_read_slog2` | `lines` (default 50) | Queries the QNX system logger (`slog2info`) for latest error messages and diagnostic buffer records. |

### Connecting to Claude Desktop / Cursor
Add the following snippet to your `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "qnx-aegis": {
      "command": "python",
      "args": [
        "-m",
        "src.server"
      ],
      "cwd": "d:/UIT/NamBonUIT/QNX-Aegis/mcp-server",
      "env": {
        "PYTHONPATH": "d:/UIT/NamBonUIT/QNX-Aegis/mcp-server"
      }
    }
  }
}
```

---

## 7. Interactive Demonstration: Deadlock Detection & Fix

1. **Trigger Deadlock:** Click **"Simulate Deadlock"** on the dashboard or invoke `qnx_deploy_and_execute` with `ipc_deadlock_demo`.
2. **Observe Real-Time Visuals:** In Cytoscape.js, `Thread 1` and `Thread 2` shift from green (`RUNNING`) to red (`REPLY-blocked` / `SEND-blocked`) with glowing dashed edges indicating mutual wait.
3. **Ask Your AI Agent:**
   > *"Agent, inspect the QNX microkernel, diagnose why the application is frozen, and fix the source code."*
4. **Agent Action:**
   - Calls `qnx_detect_ipc_deadlock` and isolates the mutual `MsgSend` deadlock.
   - Proposes asynchronous client-server decoupling with a dedicated worker thread or proper `MsgReceive` state order.
   - Calls `qnx_compile_source` to build the patch.
   - Calls `qnx_deploy_and_execute` to restore real-time execution.

---

## 8. License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details. Built with ❤️ for the QNX Global Hackathon.
