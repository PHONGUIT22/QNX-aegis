"""
QNX-Aegis: Full MCP Tools Verification Test
Tests all 6 FastMCP tools against the live QNX SDP 8.0 toolchain and running QEMU target.
"""

import sys
import json
from pathlib import Path

# Add mcp-server root
sys.path.insert(0, str(Path(__file__).parent))

from src.server import (
    qnx_verify_environment,
    qnx_compile_source,
    qnx_deploy_and_execute,
    qnx_inspect_microkernel,
    qnx_detect_ipc_deadlock,
    qnx_read_slog2
)

print("==================================================================")
print("       QNX-AEGIS: VERIFYING ALL 6 MODEL CONTEXT PROTOCOL TOOLS    ")
print("==================================================================")

# Tool 1: qnx_verify_environment
print("\n[Tool 1/6] Invoking qnx_verify_environment()...")
res_env = qnx_verify_environment()
print(json.dumps(res_env, indent=2))
assert res_env["status"] == "ready", "Environment should be ready!"
assert res_env["qcc_compiler"]["available"] is True, "qcc must be available!"
assert res_env["qemu_connection"]["connected"] is True, "QEMU must be connected!"

# Tool 2: qnx_compile_source
print("\n[Tool 2/6] Invoking qnx_compile_source()...")
res_compile = qnx_compile_source(
    source_path="qnx-target/demos/ipc_healthy_demo.c",
    output_binary="qnx-target/bin/ipc_healthy_demo_test"
)
print(f"Compilation success: {res_compile['success']}, exit_code: {res_compile['exit_code']}")
assert res_compile["success"] is True, "Compilation must succeed!"

# Tool 3: qnx_deploy_and_execute
print("\n[Tool 3/6] Invoking qnx_deploy_and_execute()...")
res_deploy = qnx_deploy_and_execute(
    local_binary="qnx-target/bin/ipc_healthy_demo",
    remote_path="/tmp/qnx-aegis/ipc_healthy_demo",
    timeout_sec=5
)
print(f"Deploy & Execute success: {res_deploy['success']}")
print(f"Execution output preview:\n{res_deploy.get('stdout', '')[:200]}")
assert res_deploy["success"] is True, "Deploy & Execute must succeed!"

# Tool 4: qnx_inspect_microkernel
print("\n[Tool 4/6] Invoking qnx_inspect_microkernel()...")
res_inspect = qnx_inspect_microkernel()
print(f"System OS: {res_inspect.get('system', {}).get('os')}")
print(f"Uptime: {res_inspect.get('system', {}).get('uptime_sec')}s")
print(f"Total Processes: {res_inspect.get('system', {}).get('num_processes')}")
print(f"Total Threads: {res_inspect.get('system', {}).get('num_threads')}")
print(f"Simulated Mode: {res_inspect.get('simulated')}")
assert res_inspect.get("system", {}).get("num_threads", 0) > 0, "Must inspect real kernel threads!"
assert res_inspect.get("simulated") is False, "Must not be simulated when QEMU is live!"

# Tool 5: qnx_detect_ipc_deadlock
print("\n[Tool 5/6] Invoking qnx_detect_ipc_deadlock()...")
res_deadlock = qnx_detect_ipc_deadlock()
print(f"Deadlock Detected: {res_deadlock['deadlock_detected']}")
print(f"Cycle Count: {res_deadlock['cycle_count']}")
print(f"Summary: {res_deadlock['summary']}")

# Tool 6: qnx_read_slog2
print("\n[Tool 6/6] Invoking qnx_read_slog2(lines=15)...")
res_slog = qnx_read_slog2(lines=15)
print(f"Lines requested: {res_slog['lines_requested']}")
print(f"Log Preview:\n{res_slog['log_output'][:250]}")
assert len(res_slog["log_output"]) > 0, "Slog2 output must not be empty!"

print("\n==================================================================")
print("     ALL 6 MCP TOOLS SUCCESSFULLY VERIFIED ON LIVE QNX 8.0!       ")
print("==================================================================")
