"""
QNX-Aegis: Serial Master
Spawns QEMU, maintains persistent serial connection,
waits for boot completion, configures network/sshd cleanly, and verifies telemetry.
"""

import subprocess
import socket
import time
import sys
from pathlib import Path

QEMU_EXE = "D:/qemu/qemu-system-x86_64.exe"
DISK_IMG = "D:/QNX_VM/qemu/output/disk-qemu"

def main():
    print("[1] Terminating old QEMU processes...")
    subprocess.run(["powershell", "-NoProfile", "-Command", "Stop-Process -Name qemu-system-x86_64 -Force -ErrorAction SilentlyContinue"], capture_output=True)
    time.sleep(2)

    print("[2] Spawning QEMU daemon...")
    cmd = [
        QEMU_EXE,
        "-cpu", "max",
        "-m", "2048",
        "-smp", "4",
        "-drive", f"file={DISK_IMG},format=raw,if=ide",
        "-net", "nic,model=virtio",
        "-net", "user,hostfwd=tcp::2222-:22",
        "-serial", "tcp:127.0.0.1:4444,server,nowait",
        "-display", "none"
    ]
    DETACHED = 0x00000008 | 0x00000200
    qemu_proc = subprocess.Popen(cmd, creationflags=DETACHED, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    print(f"    QEMU PID: {qemu_proc.pid}")

    print("[3] Connecting to serial console on 127.0.0.1:4444...")
    ser = None
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            ser = socket.create_connection(("127.0.0.1", 4444), timeout=1.0)
            print("    Connected to serial port 4444.")
            break
        except Exception:
            time.sleep(0.5)

    if not ser:
        print("[-] Could not connect to serial port!")
        return False

    ser.settimeout(1.0)

    # Phase 1: Wait for login prompt
    print("[4] Waiting for login prompt (up to 60s)...")
    buf = b""
    t_boot = time.time()
    while time.time() - t_boot < 60:
        try:
            chunk = ser.recv(1024)
            if chunk:
                buf += chunk
                sys.stdout.write(chunk.decode("ascii", errors="ignore"))
                sys.stdout.flush()
                if b"login:" in buf:
                    print("\n[+] Detected login prompt.")
                    break
        except socket.timeout:
            ser.sendall(b"\n")
        time.sleep(0.2)

    # Send credentials
    print("[*] Sending login credentials...")
    time.sleep(0.5)
    ser.sendall(b"qnxuser\n")
    time.sleep(1.0)
    ser.sendall(b"qnxuser\n")
    time.sleep(3.0)  # Wait for post_startup.sh / welcome banner to finish completely

    # Drain welcome banner
    for _ in range(5):
        try:
            ser.recv(2048)
        except socket.timeout:
            pass

    # Phase 2: Execute configuration commands
    print("[5] Executing network initialization commands...")
    commands = [
        "export PATH=$PATH:/proc/boot:/sbin:/usr/sbin:/bin:/usr/bin\n",
        "echo qnxuser | sudo -S mount -u -o rw /data 2>/dev/null\n",
        "echo qnxuser | sudo -S ifconfig vtnet0 10.0.2.15/24 up\n",
        "echo qnxuser | sudo -S route add default 10.0.2.2 2>/dev/null\n",
        "echo qnxuser | sudo -S /usr/bin/sshd 2>/dev/null\n",
        "echo qnxuser | sudo -S /usr/sbin/sshd 2>/dev/null\n",
        "echo qnxuser | sudo -S mv /data/var/run/dhcpcd /data/var/run/dhcpcd.bad 2>/dev/null\n",
        "echo qnxuser | sudo -S rm -rf /var/run/dhcpcd/* /data/var/run/dhcpcd/* /var/run/dhcpcd /var/run/sshd.pid /var/run/dhcpcd.pid /data/var/run/dhcpcd.pid 2>/dev/null\n",
        "echo qnxuser | sudo -S mkdir -p /data/var/run/dhcpcd && echo qnxuser | sudo -S chmod 755 /data/var/run/dhcpcd 2>/dev/null\n",
        "sync; sync\n",
        "ifconfig vtnet0\n"
    ]

    for c in commands:
        ser.sendall(c.encode("ascii"))
        time.sleep(0.8)

    # Read output
    resp = b""
    for _ in range(12):
        try:
            chunk = ser.recv(2048)
            if chunk:
                resp += chunk
        except socket.timeout:
            pass
        time.sleep(0.3)

    resp_str = resp.decode("ascii", errors="ignore")
    print("\n[+] Command Output:\n" + resp_str)

    # Phase 3: Test SSH Connectivity
    print("[6] Testing SSH connectivity via Paramiko...")
    time.sleep(2)

    root_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_dir / "mcp-server"))
    from src.qemu_bridge import QemuBridge

    bridge = QemuBridge()
    ok, msg = bridge.test_connection()
    print(f"    SSH Status: {ok} -> {msg}")
    if not ok:
        print("[-] SSH Connection failed!")
        return False

    # Phase 4: Provision Target with Root Daemon
    print("[7] Ensuring target is provisioned with root daemon...")
    p_ok, p_msg = bridge.ensure_target_provisioned()
    print(f"    Target Provisioning: {p_ok} -> {p_msg}")

    # Phase 5: Verify read_inspector() Telemetry
    print("[8] Querying telemetry via read_inspector()...")
    telemetry = bridge.read_inspector()
    threads = telemetry.get("threads", [])
    system = telemetry.get("system", {})
    print(f"    Connected: {telemetry.get('connected')}")
    print(f"    Simulated: {telemetry.get('simulated')}")
    print(f"    OS: {system.get('os')}")
    print(f"    Processes: {system.get('num_processes')}")
    print(f"    Threads Count: {len(threads)}")

    assert telemetry.get("connected") is True, "Target must be connected"
    assert telemetry.get("simulated") is False, "Must not be simulated"
    assert len(threads) >= 100, f"Expected >= 100 threads, got {len(threads)}"

    print(f"\n[SUCCESS] QNX 8.0 target fully verified with {len(threads)} live microkernel threads!")
    return True

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
