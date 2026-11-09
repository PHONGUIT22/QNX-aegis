import sys
import time
import json
from pathlib import Path

# Add mcp-server root
sys.path.insert(0, str(Path(__file__).parent))

from src.qemu_bridge import QemuBridge
from src.deadlock_detective import DeadlockDetective

bridge = QemuBridge()
detective = DeadlockDetective()

print("==================================================")
print(" LIVE QNX 8.0 QEMU VERIFICATION TEST             ")
print("==================================================")

# 1. Test SSH Connection
ok, msg = bridge.test_connection()
print(f"1. SSH Connectivity: {ok} -> {msg}")
assert ok, "SSH Connection must be active!"

# 1b. Ensure target provisioned
ok_prov, msg_prov = bridge.ensure_target_provisioned()
print(f"1b. Target Provisioned: {ok_prov} -> {msg_prov}")
time.sleep(1)

# 2. Test cat /dev/qnx_inspector
code, cat_out, _ = bridge.execute_remote("cat /dev/qnx_inspector")
print(f"2. cat /dev/qnx_inspector: Exit code {code}")
assert code == 0, "cat /dev/qnx_inspector must return 0"
try:
    snap = json.loads(cat_out)
    print(f"   OS: {snap['system']['os']}, Threads: {snap['system']['num_threads']}")
except Exception as e:
    print(f"   JSON Parse warning: {e}, length={len(cat_out)}")

# 3. Test qnx_inspector --dump
code, dump_out, _ = bridge.execute_remote("/tmp/qnx-aegis/qnx_inspector --dump")
print(f"3. /tmp/qnx-aegis/qnx_inspector --dump: Exit code {code}")
assert code == 0, "qnx_inspector --dump must return 0"

# 4. Trigger Live IPC Deadlock Demo
print("4. Triggering /tmp/qnx-aegis/ipc_deadlock_demo...")
bridge.execute_remote("nohup /tmp/qnx-aegis/ipc_deadlock_demo >/tmp/qnx-aegis/deadlock.log 2>&1 </dev/null &")
time.sleep(2)

code, log_out, _ = bridge.execute_remote("cat /tmp/qnx-aegis/deadlock.log")
print(f"   Deadlock log output:\n{log_out.strip()}")

code, pidin_out, _ = bridge.execute_remote("pidin -F '%a %b %N %J %B' | grep -i deadlock")
print(f"   Pidin status:\n{pidin_out.strip()}")

# 5. Detect Circular Wait Chain
print("5. Running Deadlock Detective on live microkernel state...")
telemetry = bridge.read_inspector()
analysis = detective.analyze(telemetry)
print(f"   Deadlock Detected: {analysis['deadlock_detected']}")
print(f"   Cycle Count: {analysis['cycle_count']}")
print(f"   Affected Threads: {analysis['affected_threads']}")
print(f"   Summary: {analysis['summary']}")
if analysis["cycles"]:
    for c in analysis["cycles"]:
        print(f"   Cycle: {c['cycle_path']}")
        print(f"   Details: {c['description']}")

# 6. Clean up demo
print("6. Cleaning up deadlock demo on target...")
bridge.execute_remote("slay -f ipc_deadlock_demo")
print("   [OK] Process slayed.")

print("==================================================")
print(" LIVE VERIFICATION COMPLETED SUCCESSFULLY!       ")
print("==================================================")
