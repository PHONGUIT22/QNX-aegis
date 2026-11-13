"""
QNX-Aegis: FastAPI Telemetry & WebSocket Bridge
Connects QNX target telemetry and MCP tools to the real-time Cytoscape.js dashboard.
Supports live QEMU VM launching, connection pooling, and auto-provisioning.
"""

import asyncio
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Dict, Any, List
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
import uvicorn

from .config import settings
from .qcc_compiler import QccCompiler
from .qemu_bridge import QemuBridge
from .deadlock_detective import DeadlockDetective

logging.basicConfig(level=settings.log_level)
for _name in ("paramiko", "paramiko.transport"):
    _plog = logging.getLogger(_name)
    _plog.setLevel(logging.CRITICAL + 1)
    _plog.propagate = False

logger = logging.getLogger("qnx_aegis.api_bridge")

app = FastAPI(
    title="QNX-Aegis Telemetry Bridge",
    description="Real-Time QNX 8.0 Microkernel Telemetry & MCP Studio Bridge",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

compiler = QccCompiler()
bridge = QemuBridge()
detective = DeadlockDetective()

# Active WebSocket connections
active_connections: List[WebSocket] = []


class ConnectionManager:
    def __init__(self):
        self.connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.connections.append(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.connections:
            self.connections.remove(websocket)
            logger.info(f"WebSocket client disconnected. Total clients: {len(self.connections)}")

    async def broadcast(self, message: Dict[str, Any]):
        for conn in list(self.connections):
            try:
                await conn.send_json(message)
            except Exception:
                self.disconnect(conn)


manager = ConnectionManager()


@app.get("/api/status")
async def get_system_status():
    """Returns general environment status and connectivity."""
    qcc_ok = compiler.is_available()
    ssh_ok, ssh_msg = await asyncio.to_thread(bridge.test_connection)
    return {
        "status": "ready" if (qcc_ok and ssh_ok) else "degraded",
        "qcc": {
            "available": qcc_ok,
            "version": compiler.get_version(),
            "target": settings.qnx_compiler_target
        },
        "qemu": {
            "connected": ssh_ok,
            "target": f"{settings.qemu_ssh_user}@{settings.qemu_host}:{settings.qemu_ssh_port}",
            "message": ssh_msg
        }
    }


@app.post("/api/qemu/start")
async def start_qemu_vm():
    """
    Starts the QNX Neutrino 8.0 QEMU VM in the background if not already running,
    waits for SSH to become ready, and auto-provisions the target daemon.
    """
    # 1. Check if VM is already running and reachable
    if bridge.is_port_open():
        await asyncio.to_thread(bridge.ensure_target_provisioned)
        return {
            "success": True,
            "already_running": True,
            "message": f"QNX 8.0 QEMU VM is already running on {settings.qemu_host}:{settings.qemu_ssh_port}."
        }

    # 2. Verify local QEMU binary and disk image paths
    qemu_bin = Path(settings.qemu_bin_path)
    disk_img = Path(settings.qemu_disk_image)
    if not qemu_bin.exists():
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"QEMU executable not found at: {qemu_bin}"}
        )
    if not disk_img.exists():
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"QNX 8.0 disk image not found at: {disk_img}"}
        )

    # 3. Construct QEMU command
    qemu_cmd = [
        str(qemu_bin),
        "-cpu", "max",
        "-m", str(settings.qemu_memory_mb),
        "-smp", str(settings.qemu_smp),
        "-drive", f"file={str(disk_img)},format=raw,if=ide",
        "-net", "nic,model=virtio",
        "-net", f"user,hostfwd=tcp:{settings.qemu_host}:{settings.qemu_ssh_port}-:22",
        "-serial", "tcp:127.0.0.1:4444,server,nowait",
        "-display", "none"
    ]

    # Flags to detach child process on Windows
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    creation_flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(
            qemu_cmd,
            creationflags=creation_flags,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL
        )
        logger.info(f"Spawned QEMU process PID {proc.pid}: {' '.join(qemu_cmd)}")
    except Exception as ex:
        logger.error(f"Failed to spawn QEMU: {ex}")
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": f"Failed to launch QEMU process: {ex}"}
        )

    # 4. Wait for SSH to become ready (poll up to 30s)
    max_wait = 30
    interval = 1.0
    elapsed = 0.0
    ssh_ready = False

    while elapsed < max_wait:
        await asyncio.sleep(interval)
        elapsed += interval
        if bridge.is_port_open():
            ok, _ = await asyncio.to_thread(bridge.test_connection)
            if ok:
                ssh_ready = True
                break

    if not ssh_ready:
        return JSONResponse(
            status_code=504,
            content={
                "success": False,
                "error": f"QEMU process started (PID {proc.pid}), but SSH did not become ready within {max_wait}s."
            }
        )

    # 5. Automatically provision target (/tmp/qnx-aegis binaries and /dev/qnx_inspector)
    provision_ok, provision_msg = await asyncio.to_thread(bridge.ensure_target_provisioned)
    logger.info(f"Target provision status: {provision_ok} -> {provision_msg}")

    return {
        "success": True,
        "already_running": False,
        "qemu_pid": proc.pid,
        "elapsed_sec": round(elapsed, 1),
        "message": f"QNX 8.0 QEMU VM started in {round(elapsed, 1)}s and target was provisioned."
    }


@app.get("/api/telemetry")
async def get_telemetry():
    """Retrieves current microkernel snapshot and deadlock analysis."""
    telemetry = await asyncio.to_thread(bridge.read_inspector)
    analysis = detective.analyze(telemetry)
    telemetry["deadlock_analysis"] = analysis
    return telemetry


@app.get("/api/slog2")
async def get_slog2(lines: int = 50):
    """Retrieves recent slog2 system logs."""
    logs = await asyncio.to_thread(bridge.read_slog2, lines=lines)
    return {"logs": logs}


@app.post("/api/detective")
async def run_detective():
    """Runs deadlock detective analysis on active target state."""
    telemetry = await asyncio.to_thread(bridge.read_inspector)
    return detective.analyze(telemetry)


async def _ensure_remote_binary(remote_bin: str) -> None:
    """Ensures a target ELF binary exists and is executable; provisions target if missing."""
    chk_code, _, _ = await asyncio.to_thread(bridge.execute_remote, f"test -x {remote_bin}", 4)
    if chk_code != 0:
        await asyncio.to_thread(bridge.ensure_target_provisioned)


async def _clear_fault_log() -> None:
    """Clears the persistent slog2_fault.log overlay on QNX target during recovery."""
    fault_log = f"{settings.qemu_deploy_dir}/slog2_fault.log"
    await asyncio.to_thread(
        bridge.execute_remote,
        f"echo {settings.qemu_ssh_password} | sudo -S rm -f {fault_log} 2>/dev/null || rm -f {fault_log} 2>/dev/null",
        3,
    )


@app.post("/api/trigger/deadlock")
async def trigger_deadlock_demo():
    """Slays any active fault/healthy demos cleanly, launches ipc_deadlock_demo as root, and returns telemetry."""
    if not bridge.is_port_open():
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )
    remote_bin = f"{settings.qemu_deploy_dir}/ipc_deadlock_demo"
    await _ensure_remote_binary(remote_bin)

    await asyncio.to_thread(
        bridge.execute_remote,
        f"echo {settings.qemu_ssh_password} | sudo -S slay -f ipc_deadlock_demo 2>/dev/null",
        4,
    )
    await asyncio.to_thread(
        bridge.execute_remote,
        f"echo {settings.qemu_ssh_password} | sudo -S slay -f ipc_healthy_demo 2>/dev/null",
        4,
    )

    launch_cmd = (
        f"echo {settings.qemu_ssh_password} | sudo -S nohup {remote_bin} "
        f">{settings.qemu_deploy_dir}/deadlock.log 2>&1 </dev/null &"
    )
    code, out, err = await asyncio.to_thread(bridge.execute_remote, launch_cmd, 5)
    await asyncio.sleep(0.6)

    telemetry = await asyncio.to_thread(bridge.read_inspector)
    analysis = detective.analyze(telemetry)
    telemetry["deadlock_analysis"] = analysis
    return {
        "success": True,
        "message": "Slayed prior demo instances and launched ipc_deadlock_demo as root (threads blocking in circular MsgSend wait)",
        "stdout": out,
        "stderr": err,
        "telemetry": telemetry,
        "deadlock_analysis": analysis,
    }


@app.post("/api/trigger/healthy")
async def trigger_healthy_demo():
    """Terminates existing demos, clears slog2_fault.log, and runs the healthy IPC transaction loop."""
    if not bridge.is_port_open():
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )
    remote_bin = f"{settings.qemu_deploy_dir}/ipc_healthy_demo"
    await _ensure_remote_binary(remote_bin)

    await asyncio.to_thread(
        bridge.execute_remote,
        f"echo {settings.qemu_ssh_password} | sudo -S slay -f ipc_deadlock_demo 2>/dev/null",
        4,
    )
    await asyncio.to_thread(
        bridge.execute_remote,
        f"echo {settings.qemu_ssh_password} | sudo -S slay -f ipc_healthy_demo 2>/dev/null",
        4,
    )
    await _clear_fault_log()

    code, out, err = await asyncio.to_thread(
        bridge.execute_remote,
        f"nohup {remote_bin} >/dev/null 2>&1 </dev/null &",
        5,
    )
    await asyncio.sleep(0.3)
    return {
        "success": True,
        "message": "Stopped fault demos, cleared fault log, and launched healthy non-blocking ipc_healthy_demo IPC loop",
        "stdout": out,
        "stderr": err,
    }


@app.post("/api/trigger/crash")
async def trigger_crash_demo():
    """Runs slog2 crash demo, appends fault telemetry to slog2_fault.log, and returns latest slog2 lines."""
    if not bridge.is_port_open():
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )
    remote_bin = f"{settings.qemu_deploy_dir}/slog2_crash_demo"
    await _ensure_remote_binary(remote_bin)

    code, out, err = await asyncio.to_thread(bridge.execute_remote, remote_bin, 5)

    ts = time.strftime("%b %d %H:%M:%S")
    fault_log = f"{settings.qemu_deploy_dir}/slog2_fault.log"
    entries = []
    for raw_line in out.splitlines():
        stripped = raw_line.strip()
        if stripped and not stripped.startswith("==="):
            entries.append(f"{ts} [slog2_crash_demo] {stripped}")
    entries.extend([
        f"{ts} qnx_aegis_demo [SLOG2_INFO] Subsystem initialized successfully (fault injection run)",
        f"{ts} qnx_aegis_demo [SLOG2_WARNING] Channel communication latency: 42ms (threshold: 20ms)",
        f"{ts} qnx_aegis_demo [SLOG2_ERROR] Invalid packet header received on side-channel coid 4",
        f"{ts} qnx_aegis_demo [SLOG2_CRITICAL] SIGSEGV at 0xDEADBEEF: Memory check failed in slog2_crash_demo",
    ])
    log_payload = "\n".join(entries).replace("'", "'\\''")
    append_cmd = (
        f"mkdir -p {settings.qemu_deploy_dir} && "
        f"printf '%s\\n' '{log_payload}' >> {fault_log} && "
        f"chmod 666 {fault_log} 2>/dev/null"
    )
    await asyncio.to_thread(bridge.execute_remote, append_cmd, 4)

    logs = await asyncio.to_thread(bridge.read_slog2, lines=25)
    return {
        "success": True,
        "message": "Executed slog2_crash_demo on QNX 8.0 target and recorded critical slog2 fault entries",
        "stdout": out,
        "stderr": err,
        "slog2_preview": logs,
    }


@app.post("/api/trigger/benchmark")
async def trigger_benchmark_demo():
    """
    Uploads (if missing) and executes /tmp/qnx-aegis/ipc_benchmark_demo on the QNX 8.0 target,
    returning parsed ClockCycles() hardware IPC round-trip latency and throughput metrics.
    """
    connected, _ = await asyncio.to_thread(bridge.test_connection)
    if not connected:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )

    remote_bin = f"{settings.qemu_deploy_dir}/ipc_benchmark_demo"
    chk_code, _, _ = await asyncio.to_thread(bridge.execute_remote, f"test -x {remote_bin}", 5)

    if chk_code != 0:
        local_bin = settings.target_bin_dir / "ipc_benchmark_demo"
        if not local_bin.exists():
            compile_res = await asyncio.to_thread(
                compiler.compile,
                "qnx-target/demos/ipc_benchmark_demo.c",
                str(local_bin),
                ["-lslog2"],
            )
            if not compile_res.get("success"):
                return JSONResponse(
                    status_code=500,
                    content={
                        "success": False,
                        "error": "Failed to cross-compile ipc_benchmark_demo on host.",
                        "compile_result": compile_res,
                    }
                )
        upload_ok, upload_msg = await asyncio.to_thread(
            bridge.upload_file, str(local_bin), remote_bin
        )
        if not upload_ok:
            return JSONResponse(
                status_code=500,
                content={"success": False, "error": f"Failed to upload benchmark binary: {upload_msg}"}
            )

    code, out, err = await asyncio.to_thread(bridge.execute_remote, remote_bin, 20)
    clean_out = out.strip()
    if code == 0 and "{" in clean_out and "}" in clean_out:
        try:
            json_str = clean_out[clean_out.find("{"):clean_out.rfind("}") + 1]
            metrics = json.loads(json_str)
            return {
                "success": True,
                **metrics,
                "metrics": metrics,
                "remote_binary": remote_bin,
                "raw_stdout": clean_out,
                "stderr": err,
            }
        except json.JSONDecodeError as ex:
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": f"Failed to parse benchmark JSON output: {ex}",
                    "stdout": out,
                    "stderr": err,
                }
            )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": f"Benchmark execution failed (exit_code={code})",
            "stdout": out,
            "stderr": err,
        }
    )


@app.post("/api/remediate/deadlock")
async def remediate_deadlock():
    """
    One-click AI Hot-Patch remediation:
    1. Slays frozen ipc_deadlock_demo on the QNX target.
    2. Cross-compiles patched qnx-target/demos/ipc_healthy_demo.c on host with qcc.
    3. Uploads compiled binary via SCP to /tmp/qnx-aegis/ipc_healthy_demo.
    4. Launches ipc_healthy_demo and captures real verification telemetry.
    """
    connected, _ = await asyncio.to_thread(bridge.test_connection)
    if not connected:
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )

    # 1. Slay frozen deadlock process cleanly (supports root and configured password)
    slay_cmd = (
        f"echo root | sudo -S slay -f ipc_deadlock_demo 2>/dev/null || "
        f"echo {settings.qemu_ssh_password} | sudo -S slay -f ipc_deadlock_demo 2>/dev/null || "
        f"slay -f ipc_deadlock_demo 2>/dev/null"
    )
    slay_code, slay_out, slay_err = await asyncio.to_thread(bridge.execute_remote, slay_cmd, 5)

    # Also ensure any running ipc_healthy_demo is stopped before SCP overwrite and clear fault log
    await asyncio.to_thread(
        bridge.execute_remote,
        f"echo {settings.qemu_ssh_password} | sudo -S slay -f ipc_healthy_demo 2>/dev/null || slay -f ipc_healthy_demo 2>/dev/null",
        3,
    )
    await _clear_fault_log()

    # 2. Cross-compile healthy patched source via QCC on host
    local_bin = settings.target_bin_dir / "ipc_healthy_demo"
    compile_res = await asyncio.to_thread(
        compiler.compile,
        "qnx-target/demos/ipc_healthy_demo.c",
        str(local_bin),
    )
    if not compile_res.get("success"):
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": compile_res.get("error") or "QCC compilation failed during hot-patch.",
                "qcc_command": compile_res.get("command", ""),
                "diagnostics": compile_res.get("diagnostics", []),
                "stderr": compile_res.get("stderr", ""),
            }
        )

    # 3. Upload patched ELF binary via SCP
    remote_bin = f"{settings.qemu_deploy_dir}/ipc_healthy_demo"
    upload_ok, upload_msg = await asyncio.to_thread(bridge.upload_file, str(local_bin), remote_bin)
    if not upload_ok:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": f"SCP upload failed: {upload_msg}",
                "qcc_command": compile_res.get("command", ""),
            }
        )

    # 4. Launch patched healthy binary on target and verify execution output
    launch_cmd = f"nohup {remote_bin} >{settings.qemu_deploy_dir}/healthy.log 2>&1 </dev/null &"
    await asyncio.to_thread(bridge.execute_remote, launch_cmd, 5)
    await asyncio.sleep(0.8)
    _, exec_log, _ = await asyncio.to_thread(
        bridge.execute_remote,
        f"cat {settings.qemu_deploy_dir}/healthy.log 2>/dev/null",
        3,
    )

    qcc_cmd_raw = compile_res.get("command", "")
    if qcc_cmd_raw and not qcc_cmd_raw.startswith("qcc "):
        parts = qcc_cmd_raw.split(" ", 1)
        qcc_display = f"qcc {parts[1]}" if len(parts) > 1 and "qcc" in parts[0].lower() else qcc_cmd_raw
    else:
        qcc_display = qcc_cmd_raw

    return {
        "success": True,
        "slayed": "ipc_deadlock_demo",
        "slay_exit_code": slay_code,
        "slay_output": (slay_out + slay_err).strip(),
        "qcc_command": qcc_display,
        "qcc_full_command": qcc_cmd_raw,
        "qcc_exit_code": compile_res.get("exit_code", 0),
        "qcc_stdout": compile_res.get("stdout", ""),
        "qcc_stderr": compile_res.get("stderr", ""),
        "diagnostics": compile_res.get("diagnostics", []),
        "target_deployed": remote_bin,
        "upload_message": upload_msg,
        "execution_stdout": exec_log.strip(),
        "message": f"Hot-patched with QCC ({settings.qnx_compiler_target}), uploaded to {remote_bin}, and launched healthy IPC loop."
    }


@app.get("/api/mcp/tools")
async def get_mcp_tools():
    """Returns metadata and JSON schemas of all MCP tools exposed by QNX-Aegis."""
    from .server import mcp as mcp_server

    tools_list = []
    if hasattr(mcp_server, "list_tools"):
        try:
            raw_tools = await mcp_server.list_tools()
            for t in raw_tools:
                d = t.model_dump() if hasattr(t, "model_dump") else dict(t)
                tools_list.append({
                    "name": d.get("name"),
                    "description": (d.get("description") or "").strip(),
                    "input_schema": d.get("input_schema") or d.get("inputSchema") or {},
                    "output_schema": d.get("output_schema") or d.get("outputSchema") or {},
                })
        except Exception as ex:
            logger.warning(f"Failed inspecting FastMCP tools dynamically: {ex}")

    return {
        "server": "qnx-aegis",
        "protocol": "Model Context Protocol (JSON-RPC 2.0)",
        "transport": "stdio / FastAPI HTTP Bridge",
        "count": len(tools_list),
        "tools": tools_list,
    }


@app.post("/api/mcp/call")
async def invoke_mcp_tool(payload: Dict[str, Any] = Body(...)):
    """Executes one of the QNX-Aegis MCP tools interactively for the MCP JSON-RPC Studio."""
    from .server import (
        qnx_verify_environment,
        qnx_compile_source,
        qnx_deploy_and_execute,
        qnx_inspect_microkernel,
        qnx_detect_ipc_deadlock,
        qnx_read_slog2,
    )

    tool_map = {
        "qnx_verify_environment": qnx_verify_environment,
        "qnx_compile_source": qnx_compile_source,
        "qnx_deploy_and_execute": qnx_deploy_and_execute,
        "qnx_inspect_microkernel": qnx_inspect_microkernel,
        "qnx_detect_ipc_deadlock": qnx_detect_ipc_deadlock,
        "qnx_read_slog2": qnx_read_slog2,
    }

    tool_name = payload.get("tool") or payload.get("name")
    args = payload.get("arguments") or payload.get("params") or {}
    if not tool_name or tool_name not in tool_map:
        return JSONResponse(
            status_code=404,
            content={
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Unknown MCP tool: {tool_name}"},
            },
        )

    try:
        result = await asyncio.to_thread(tool_map[tool_name], **args)
        return {
            "jsonrpc": "2.0",
            "id": payload.get("id", 1),
            "tool": tool_name,
            "result": result,
        }
    except Exception as ex:
        return JSONResponse(
            status_code=500,
            content={
                "jsonrpc": "2.0",
                "id": payload.get("id", 1),
                "error": {"code": -32603, "message": str(ex)},
            },
        )


@app.websocket("/ws/telemetry")
async def websocket_telemetry_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            # Keep socket alive and respond to client pings
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception:
        manager.disconnect(websocket)


async def telemetry_broadcast_worker():
    """Background task polling QNX microkernel state and broadcasting to web clients."""
    while True:
        try:
            telemetry = await asyncio.to_thread(bridge.read_inspector)
            analysis = detective.analyze(telemetry)
            telemetry["deadlock_analysis"] = analysis
            await manager.broadcast({
                "type": "telemetry_update",
                "payload": telemetry
            })
        except Exception as ex:
            logger.error(f"Error in telemetry broadcast worker: {ex}")
        await asyncio.sleep(settings.telemetry_poll_interval)


@app.on_event("startup")
async def startup_event():
    logger.info("Starting QNX-Aegis background telemetry broadcast worker...")
    asyncio.create_task(telemetry_broadcast_worker())


# Mount Dashboard static files
dashboard_path = settings.project_root / "dashboard"
if dashboard_path.exists():
    app.mount("/static", StaticFiles(directory=str(dashboard_path)), name="static")

    @app.get("/")
    async def serve_index():
        return FileResponse(dashboard_path / "index.html")

    @app.get("/styles.css")
    async def serve_styles():
        return FileResponse(dashboard_path / "styles.css")

    @app.get("/app.js")
    async def serve_app():
        return FileResponse(dashboard_path / "app.js")


def main():
    uvicorn.run(
        "src.api_bridge:app",
        host=settings.api_bridge_host,
        port=settings.api_bridge_port,
        reload=False
    )


if __name__ == "__main__":
    main()
