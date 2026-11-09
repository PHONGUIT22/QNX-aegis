# QNX-Aegis Codelab: Mastering QNX 8.0 Microkernel IPC & AI-Assisted Debugging
### A Hands-On Guide to QNX Resource Managers, Microkernel IPC Deadlocks, and Model Context Protocol (MCP)

> **Estimated Completion Time:** 15–20 minutes  
> **Target Audience:** Embedded Systems Engineers, RTOS Developers, and AI Tooling Architects  
> **Prerequisites:** Windows Host with QNX SDP 8.0, QEMU QNX 8.0 x86_64 image, and Python 3.10+

---

## Overview

In this codelab, you will:
1. Initialize the QNX SDP 8.0 cross-compilation environment on Windows.
2. Cross-compile a native QNX C **Resource Manager** (`/dev/qnx_inspector`) and IPC demo applications using `qcc`.
3. Deploy the binaries over SSH/SCP to a running QNX 8.0 QEMU virtual machine.
4. Execute an intentional mutual `MsgSend` circular deadlock between two QNX threads.
5. Use the **QNX-Aegis MCP Server** to empower an AI Assistant (Claude, Cursor, Antigravity) to autonomously detect the microkernel deadlock, trace the exact blocking states, and apply a fix.
6. Monitor the real-time microkernel state and `slog2` log stream on the **Cytoscape.js Live Dashboard**.

---

## Module 1: Environment Setup & Verification

### Step 1.1: Clone and Configure
Open a PowerShell terminal and navigate to the project root:
```powershell
cd d:\UIT\NamBonUIT\QNX-Aegis
Copy-Item .env.example .env
```

Open `.env` in your editor and verify the following default settings match your setup:
```ini
QNX_SDP_PATH=C:/QNX800/qnxsdp-env.bat
QNX_COMPILER_TARGET=gcc_ntox86_64
QEMU_HOST=127.0.0.1
QEMU_SSH_PORT=2222
QEMU_SSH_USER=qnxuser
QEMU_SSH_PASSWORD=qnxuser
QEMU_DEPLOY_DIR=/tmp/qnx-aegis
```

### Step 1.2: Run Environment Diagnostics
Execute the setup verification script:
```powershell
.\scripts\setup-env.ps1
```
You should see:
```text
[OK] QNX SDP 8.0 environment loaded successfully.
[OK] qcc cross-compiler detected: qcc -Vgcc_ntox86_64
[OK] Python 3.10+ detected.
[OK] Ready to compile for QNX Neutrino RTOS 8.0 (x86_64)!
```

---

## Module 2: Understanding the Native QNX Resource Manager

Take a look at [`qnx-target/inspector-daemon/main.c`](file:///d:/UIT/NamBonUIT/QNX-Aegis/qnx-target/inspector-daemon/main.c). 

In QNX, device drivers and services are called **Resource Managers**. They intercept POSIX file operations (`open`, `read`, `write`, `devctl`) via the microkernel's dispatch library.

Here is how `/dev/qnx_inspector` registers itself into the QNX filesystem namespace:

```c
// 1. Allocate dispatch structure
dpp = dispatch_create();

// 2. Initialize default POSIX I/O handlers
iofunc_func_init(_RESMGR_CONNECT_NFUNCS, &connect_funcs,
                 _RESMGR_IO_NFUNCS, &io_funcs);
io_funcs.read = inspector_io_read; // Overridden to output JSON

// 3. Register /dev/qnx_inspector
resmgr_id = resmgr_attach(dpp, &resmgr_attr, "/dev/qnx_inspector",
                          _FTYPE_ANY, 0, &connect_funcs, &io_funcs, &attr);

// 4. Enter non-terminating event dispatch loop
while (1) {
    ctp = dispatch_block(ctp);
    dispatch_handler(ctp);
}
```

When a user or tool runs `cat /dev/qnx_inspector`, the microkernel invokes `inspector_io_read()`, which queries the `/proc` filesystem and returns structured JSON describing all thread states!

---

## Module 3: Cross-Compiling the Target Binaries

To build all target binaries for the QNX x86_64 architecture using `qcc`, run:
```powershell
.\scripts\build-target.ps1
```

The script compiles:
1. `bin/qnx_inspector` — The native resource manager daemon.
2. `bin/ipc_deadlock_demo` — The circular deadlock demo.
3. `bin/ipc_healthy_demo` — A well-behaved producer-consumer IPC loop.
4. `bin/slog2_crash_demo` — A diagnostic tool that logs to `slog2` and exercises crash telemetry.

---

## Module 4: Deploying to QNX 8.0 in QEMU

Ensure your QNX 8.0 QEMU VM is booted and accepting SSH connections on port 2222:
```powershell
.\scripts\deploy-to-qemu.ps1
```
This transfers the compiled ELF binaries into `/tmp/qnx-aegis/` on the QNX target, applies executable permissions (`chmod +x`), and launches the `qnx_inspector` daemon in the background.

To verify manually from the target QNX shell:
```sh
# On the QNX VM shell:
cat /dev/qnx_inspector
```
Output:
```json
{
  "system": {
    "os": "QNX Neutrino RTOS 8.0",
    "target": "x86_64",
    "uptime_sec": 3142
  },
  "threads": [
    {"pid": 1, "tid": 1, "name": "procnto-smp-instr", "state": "READY"},
    {"pid": 4108, "tid": 1, "name": "qnx_inspector", "state": "RECEIVE", "chid": 1}
  ]
}
```

---

## Module 5: Triggering a Microkernel IPC Deadlock

Now run the intentional deadlock demo:
```powershell
# Run from host via PowerShell or let the dashboard trigger it:
python -c "from src.qemu_bridge import QemuBridge; QemuBridge().execute_remote('/tmp/qnx-aegis/ipc_deadlock_demo &')"
```

### What happens under the hood?
In `ipc_deadlock_demo.c`:
- **Thread 1** creates Channel 1 and attaches to Channel 2.
- **Thread 2** creates Channel 2 and attaches to Channel 1.
- Both threads simultaneously call `MsgSend()` to the other channel **before** calling `MsgReceive()`.
- Thread 1 transitions to `STATE_REPLY` (or `STATE_SEND`), waiting for Thread 2.
- Thread 2 transitions to `STATE_REPLY` (or `STATE_SEND`), waiting for Thread 1.
- **Mutual Circular Deadlock!** Neither thread can ever be scheduled again to execute `MsgReply()`.

---

## Module 6: Autonomous Diagnosis via AI MCP Agent

Launch the QNX-Aegis MCP Server:
```powershell
cd mcp-server
python -m src.server
```

In your AI Assistant (Claude Desktop, Cursor, or Antigravity):
Prompt the agent:
> *"Inspect the QNX target microkernel for active deadlocks and explain which threads are blocked."*

### Agent Tool Call: `qnx_detect_ipc_deadlock`
The agent invokes the tool. Under the hood:
1. Reads `/dev/qnx_inspector`.
2. `deadlock_detective.py` constructs the directed wait graph.
3. Finds the cycle: `TID 1 (PID 1054) -> TID 2 (PID 1054) -> TID 1 (PID 1054)`.
4. The AI outputs:
> *"A mutual synchronous IPC deadlock was detected on QNX 8.0! Thread 1 is in `STATE_REPLY` waiting for Connection 2, while Thread 2 is in `STATE_REPLY` waiting for Connection 1. Because QNX MsgSend is blocking, neither thread can reach MsgReceive or MsgReply."*

The AI Assistant then suggests and applies the fix:
- Decouple the request-reply sequence or introduce a non-blocking pulse (`MsgSendPulse()`), or ensure `MsgReceive()` is invoked by a dedicated server thread prior to sending.

---

## Module 7: Exploring the Real-Time Dashboard

Start the live dashboard and telemetry bridge:
```powershell
cd mcp-server
python -m src.api_bridge
```
Open your browser to:
```
http://localhost:8000
```

### Dashboard Highlights:
1. **Interactive Cytoscape.js IPC Graph:**
   - Watch nodes transition between states in real-time.
   - Click on any thread node to see PID, TID, channel associations, and priority.
   - When a deadlock occurs, edges pulse in vivid red.
2. **Live Slog2 Console:**
   - Displays real-time outputs from `slog2info` without cluttering the target terminal.
3. **One-Click Actions:**
   - Click **"Simulate Deadlock"** to launch `ipc_deadlock_demo`.
   - Click **"Run Detective"** to execute instant graph cycle analysis.
   - Click **"Recover & Run Healthy"** to launch `ipc_healthy_demo`.

---

## Summary & Next Steps

Congratulations! You have completed the QNX-Aegis Codelab. You now understand:
- How the QNX 8.0 Microkernel handles synchronous IPC (`MsgSend`/`MsgReceive`/`MsgReply`).
- How to author a native QNX Resource Manager (`/dev/qnx_inspector`).
- How to detect microkernel blocking states and cycle deadlocks.
- How to integrate Model Context Protocol (MCP) to make AI agents first-class QNX debugging partners.
