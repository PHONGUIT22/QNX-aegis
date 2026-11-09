"""
QNX-Aegis: Reliable QEMU Boot, Network Setup & Telemetry Verification
"""

import subprocess
import socket
import time
import sys
import os
from pathlib import Path

QEMU_EXE = "D:/qemu/qemu-system-x86_64.exe"
DISK_IMG = "D:/QNX_VM/qemu/output/disk-qemu"

def main():
    # 1. Kill any existing QEMU
    print("[1] Killing any existing QEMU processes...")
    subprocess.run(["powershell", "-NoProfile", "-Command", "Stop-Process -Name qemu-system-x86_64 -Force -ErrorAction SilentlyContinue"], capture_output=True)
    time.sleep(2)

    # 2. Launch QEMU via PowerShell Start-Process so it persists independently
    print("[2] Spawning independent QEMU daemon...")
    ps_cmd = (
        f"Start-Process -FilePath '{QEMU_EXE}' -ArgumentList "
        f"'-cpu max -m 2048 -smp 4 -drive file={DISK_IMG},format=raw,if=ide "
        f"-net nic,model=virtio -net user,hostfwd=tcp::2222-:22 "
        f"-serial tcp:127.0.0.1:4444,server,nowait -display none'"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], check=True)
    time.sleep(1)

    # 3. Connect to serial port immediately
    print("[3] Connecting to serial console on 127.0.0.1:4444...")
    s = None
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            s = socket.create_connection(("127.0.0.1", 4444), timeout=1.5)
            print("    Connected to serial port 4444.")
            break
        except Exception:
            time.sleep(0.5)

    if not s:
        print("[-] Could not connect to serial port!")
        return False

    s.settimeout(1.0)
    print("[4] Monitoring boot sequence...")
    buf = b""
    logged_in = False
    configured = False
    t_start = time.time()

    while time.time() - t_start < 60:
        try:
            chunk = s.recv(1024)
            if chunk:
                buf += chunk
                sys.stdout.write(chunk.decode("ascii", errors="ignore"))
                sys.stdout.flush()

                if not logged_in and b"login:" in buf:
                    print("\n    >>> Sending login credentials...")
                    time.sleep(0.5)
                    s.sendall(b"qnxuser\n")
                    time.sleep(1.0)
                    s.sendall(b"qnxuser\n")
                    time.sleep(1.5)
                    logged_in = True
                    buf = b""

                if logged_in and not configured and (b"qnxuser@qnxqemu" in buf or b"$" in buf or b"#" in buf):
                    print("\n    >>> Sending network configuration commands...")
                    time.sleep(0.5)
                    cmds = [
                        b"export PATH=$PATH:/proc/boot:/sbin:/usr/sbin:/bin:/usr/bin\n",
                        b"echo qnxuser | sudo -S mount -u -o rw /data 2>/dev/null\n",
                        b"echo qnxuser | sudo -S ifconfig vtnet0 10.0.2.15/24 up\n",
                        b"echo qnxuser | sudo -S route add default 10.0.2.2 2>/dev/null\n",
                        b"echo qnxuser | sudo -S /usr/bin/sshd 2>/dev/null\n",
                        b"echo qnxuser | sudo -S /usr/sbin/sshd 2>/dev/null\n",
                        b"echo qnxuser | sudo -S mv /data/var/run/dhcpcd /data/var/run/dhcpcd.bad 2>/dev/null\n",
                        b"echo qnxuser | sudo -S rm -rf /var/run/dhcpcd/* /data/var/run/dhcpcd/* /var/run/dhcpcd /var/run/sshd.pid /var/run/dhcpcd.pid /data/var/run/dhcpcd.pid 2>/dev/null\n",
                        b"echo qnxuser | sudo -S mkdir -p /data/var/run/dhcpcd && echo qnxuser | sudo -S chmod 755 /data/var/run/dhcpcd 2>/dev/null\n",
                        b"sync; sync\n",
                        b"ifconfig vtnet0\n"
                    ]
                    for c in cmds:
                        s.sendall(c)
                        time.sleep(0.4)
                    configured = True
                    time.sleep(2)
                    break

        except socket.timeout:
            if not logged_in:
                s.sendall(b"\n")
            elif not configured:
                s.sendall(b"\n")
        time.sleep(0.2)

    print("\n[5] Testing SSH connection to QNX target...")
    time.sleep(2)

    # Add mcp-server to sys.path
    root_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_dir / "mcp-server"))
    from src.qemu_bridge import QemuBridge

    bridge = QemuBridge()
    ok, msg = bridge.test_connection()
    print(f"    SSH Connection: {ok} -> {msg}")
    if not ok:
        print("[-] SSH connection failed!")
        return False

    print("[6] Ensuring target is provisioned with root daemon...")
    prov_ok, prov_msg = bridge.ensure_target_provisioned()
    print(f"    Target Provisioning: {prov_ok} -> {prov_msg}")

    print("[7] Verifying read_inspector() telemetry...")
    telemetry = bridge.read_inspector()
    threads = telemetry.get("threads", [])
    system = telemetry.get("system", {})
    print(f"    Connected: {telemetry.get('connected')}")
    print(f"    Simulated: {telemetry.get('simulated')}")
    print(f"    OS: {system.get('os')}")
    print(f"    Processes: {system.get('num_processes')}")
    print(f"    Threads Count: {len(threads)}")

    assert telemetry.get("connected") is True, "Must be connected"
    assert telemetry.get("simulated") is False, "Must not be simulated"
    assert len(threads) >= 100, f"Expected >= 100 threads, got {len(threads)}"

    print("\n[SUCCESS] QNX 8.0 target is fully verified with 100+ microkernel threads!")
    return True

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
