"""
QNX-Aegis: Model Context Protocol (MCP) Server
Exposes first-class QNX 8.0 microkernel inspection, cross-compilation, deployment,
and deadlock diagnosis tools to AI Coding Assistants (Claude, Cursor, Antigravity).
"""

from typing import List, Dict, Any, Optional

try:
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        class FastMCP:
            def __init__(self, name: str):
                self.name = name
            def tool(self):
                def decorator(fn):
                    return fn
                return decorator
            def run(self):
                pass

from .config import settings
from .qcc_compiler import QccCompiler
from .qemu_bridge import QemuBridge
from .deadlock_detective import DeadlockDetective

# Initialize FastMCP Server
mcp = FastMCP("qnx-aegis")

compiler = QccCompiler()
bridge = QemuBridge()
detective = DeadlockDetective()


@mcp.tool()
def qnx_verify_environment() -> Dict[str, Any]:
    """
    Verifies host development environment, qcc cross-compiler availability,
    and SSH connectivity to the QNX Neutrino RTOS 8.0 QEMU virtual machine.
    """
    qcc_ok = compiler.is_available()
    qcc_ver = compiler.get_version() if qcc_ok else "Not found in PATH"

    ssh_ok, ssh_msg = bridge.test_connection()

    return {
        "status": "ready" if (qcc_ok and ssh_ok) else "degraded",
        "qcc_compiler": {
            "available": qcc_ok,
            "version": qcc_ver,
            "target_architecture": settings.qnx_compiler_target,
        },
        "qemu_connection": {
            "connected": ssh_ok,
            "target": f"{settings.qemu_ssh_user}@{settings.qemu_host}:{settings.qemu_ssh_port}",
            "details": ssh_msg,
        },
        "connected": ssh_ok
    }


@mcp.tool()
def qnx_compile_source(
    source_path: str,
    output_binary: str,
    extra_flags: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Cross-compiles C source code for QNX 8.0 using `qcc -Vgcc_ntox86_64` (or LLVM).
    Returns compiler exit code, stdout, stderr, and structured diagnostics.

    Parameters:
    - source_path: Path to the .c source file on the host.
    - output_binary: Path where compiled binary should be saved.
    - extra_flags: Additional compiler/linker flags (e.g. ['-lslog2', '-I...']).
    """
    return compiler.compile(
        source_path=source_path,
        output_binary=output_binary,
        extra_flags=extra_flags or []
    )


@mcp.tool()
def qnx_deploy_and_execute(
    local_binary: str,
    remote_path: str,
    timeout_sec: int = 15
) -> Dict[str, Any]:
    """
    Uploads a binary via SCP to the QNX QEMU virtual machine, sets executable permissions
    (chmod +x), and executes the process on the target.

    Parameters:
    - local_binary: Host path to the compiled binary.
    - remote_path: Remote target destination path (e.g. /tmp/qnx-aegis/app).
    - timeout_sec: Execution timeout in seconds.
    """
    upload_ok, upload_msg = bridge.upload_file(local_binary, remote_path)
    if not upload_ok:
        return {
            "success": False,
            "step": "upload",
            "message": upload_msg
        }

    exit_code, stdout, stderr = bridge.execute_remote(remote_path, timeout=timeout_sec)
    return {
        "success": exit_code == 0,
        "step": "execution",
        "exit_code": exit_code,
        "remote_path": remote_path,
        "stdout": stdout,
        "stderr": stderr
    }


@mcp.tool()
def qnx_inspect_microkernel() -> Dict[str, Any]:
    """
    Queries the native QNX Resource Manager (/dev/qnx_inspector) and process manager (pidin)
    on the QNX 8.0 target to retrieve live thread states (SEND, RECEIVE, REPLY, READY, RUNNING),
    channel associations, and uptime.
    """
    telemetry = bridge.read_inspector()
    # Augment with deadlock analysis
    analysis = detective.analyze(telemetry)
    telemetry["deadlock_analysis"] = analysis
    return telemetry


@mcp.tool()
def qnx_detect_ipc_deadlock() -> Dict[str, Any]:
    """
    Performs graph cycle detection on active QNX threads blocked in STATE_SEND or STATE_REPLY.
    Identifies the exact circular wait chain (PID:TID -> Target PID:TID) and provides remediation advice.
    """
    telemetry = bridge.read_inspector()
    return detective.analyze(telemetry)


@mcp.tool()
def qnx_read_slog2(lines: int = 50) -> Dict[str, Any]:
    """
    Retrieves the most recent system log records from the QNX System Logger 2 (slog2info) buffer.

    Parameters:
    - lines: Number of log lines to retrieve (default: 50).
    """
    log_content = bridge.read_slog2(lines=lines)
    return {
        "lines_requested": lines,
        "log_output": log_content
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
