"""
QNX-Aegis: FastAPI Telemetry & WebSocket Bridge
Connects QNX target telemetry and MCP tools to the real-time Cytoscape.js dashboard.
Supports live QEMU VM launching, connection pooling, and auto-provisioning.
"""

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
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


@app.post("/api/trigger/deadlock")
async def trigger_deadlock_demo():
    """Triggers the circular MsgSend deadlock demo on the target."""
    if not bridge.is_port_open():
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )
    remote_bin = f"{settings.qemu_deploy_dir}/ipc_deadlock_demo"
    code, out, err = await asyncio.to_thread(
        bridge.execute_remote,
        f"nohup {remote_bin} >/dev/null 2>&1 </dev/null &",
        5
    )
    return {
        "success": True,
        "message": "Dispatched ipc_deadlock_demo to QNX target (detached stdio)",
        "stdout": out,
        "stderr": err
    }


@app.post("/api/trigger/healthy")
async def trigger_healthy_demo():
    """Terminates existing demos and runs the healthy IPC transaction loop."""
    if not bridge.is_port_open():
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )
    await asyncio.to_thread(bridge.execute_remote, "slay -f ipc_deadlock_demo 2>/dev/null", 5)
    remote_bin = f"{settings.qemu_deploy_dir}/ipc_healthy_demo"
    code, out, err = await asyncio.to_thread(
        bridge.execute_remote,
        f"nohup {remote_bin} >/dev/null 2>&1 </dev/null &",
        5
    )
    return {
        "success": True,
        "message": "Dispatched ipc_healthy_demo to QNX target (detached stdio)",
        "stdout": out,
        "stderr": err
    }


@app.post("/api/trigger/crash")
async def trigger_crash_demo():
    """Runs slog2 crash demo to emit critical logs and trigger fault."""
    if not bridge.is_port_open():
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": "QNX target is offline. Please start QEMU VM first."}
        )
    remote_bin = f"{settings.qemu_deploy_dir}/slog2_crash_demo"
    code, out, err = await asyncio.to_thread(bridge.execute_remote, remote_bin, 5)
    return {
        "success": True,
        "message": "Executed slog2_crash_demo",
        "stdout": out,
        "stderr": err
    }


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
