import subprocess
import socket
import time
import sys
from pathlib import Path

# Force UTF-8 stdout
if sys.stdout.encoding.lower() != 'utf-8':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

QEMU_EXE = "D:/qemu/qemu-system-x86_64.exe"
DISK_IMG = "D:/QNX_VM/qemu/output/disk-qemu"

def recv_until(sock, pattern, timeout=40):
    sock.settimeout(1.0)
    buf = b""
    t0 = time.time()
    patterns = pattern if isinstance(pattern, list) else [pattern]
    while time.time() - t0 < timeout:
        try:
            chunk = sock.recv(1024)
            if chunk:
                buf += chunk
                text = chunk.decode("utf-8", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                for p in patterns:
                    if p.encode("utf-8") in buf:
                        return True, buf.decode("utf-8", errors="replace")
        except socket.timeout:
            pass
        time.sleep(0.1)
    return False, buf.decode("utf-8", errors="replace")

def send_cmd(sock, cmd_str, prompt=b"$", timeout=25):
    print(f"\n>>> EXEC: {cmd_str.strip()}")
    sock.sendall(cmd_str.encode("utf-8"))
    sock.settimeout(1.0)
    buf = b""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            chunk = sock.recv(1024)
            if chunk:
                buf += chunk
                text = chunk.decode("utf-8", errors="replace")
                sys.stdout.write(text)
                sys.stdout.flush()
                if prompt in buf or b"# " in buf:
                    return True, buf.decode("utf-8", errors="replace")
        except socket.timeout:
            pass
        time.sleep(0.1)
    return False, buf.decode("utf-8", errors="replace")

def main():
    print("[1] Terminating any stale QEMU instances...")
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

    print("[3] Connecting to TCP serial console on 127.0.0.1:4444...")
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

    print("[4] Waiting for boot and login prompt...")
    ok, _ = recv_until(ser, "login:", timeout=60)
    if not ok:
        print("[-] Did not see login: prompt in 60s, pulsing enter...")
        ser.sendall(b"\n")
        ok, _ = recv_until(ser, "login:", timeout=15)
        if not ok:
            print("[-] Still no login prompt.")
            return False

    print("\n[5] Sending login username (qnxuser)...")
    time.sleep(0.5)
    ser.sendall(b"qnxuser\n")
    ok, _ = recv_until(ser, ["Password:", "password:"], timeout=10)
    if not ok:
        print("[-] Did not see Password: prompt, sending anyway...")

    print("\n[6] Sending login password (qnxuser)...")
    time.sleep(0.5)
    ser.sendall(b"qnxuser\n")

    print("\n[7] Waiting for shell prompt ($ or #)...")
    ok, _ = recv_until(ser, ["$", "#"], timeout=25)
    if not ok:
        print("[-] Did not see shell prompt, sending enter...")
        ser.sendall(b"\n")
        recv_until(ser, ["$", "#"], timeout=10)

    print("\n[8] Executing setup commands...")
    time.sleep(1.0)
    send_cmd(ser, "export PATH=$PATH:/proc/boot:/sbin:/usr/sbin:/bin:/usr/bin\n")
    send_cmd(ser, "mount\n")
    send_cmd(ser, "use chkqnx6fs\n")
    send_cmd(ser, "echo qnxuser | sudo -S /usr/bin/chkqnx6fs -y -f /dev/hd0t179\n", timeout=30)
    send_cmd(ser, "echo qnxuser | sudo -S mount -u -o rw /data\n")
    send_cmd(ser, "mount\n")
    send_cmd(ser, "echo qnxuser | sudo -S ifconfig vtnet0 10.0.2.15/24 up\n")
    send_cmd(ser, "echo qnxuser | sudo -S route add default 10.0.2.2\n")
    send_cmd(ser, "ifconfig vtnet0\n")
    send_cmd(ser, "echo qnxuser | sudo -S rm -rf /data/var/run/dhcpcd /var/run/dhcpcd.pid /var/run/dhcpcd/* /data/var/run/dhcpcd/* /var/run/sshd.pid /data/var/run/sshd.pid\n")
    send_cmd(ser, "echo qnxuser | sudo -S /usr/bin/sshd -d\n", timeout=10)
    send_cmd(ser, "echo qnxuser | sudo -S /usr/bin/sshd\n")
    send_cmd(ser, "pidin -F '%a %N' | grep ssh\n")
    send_cmd(ser, "sync; sync\n")

    print("\n[9] Testing SSH via Paramiko while keeping serial open...")
    time.sleep(3)
    root_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root_dir / "mcp-server"))
    from src.qemu_bridge import QemuBridge
    bridge = QemuBridge()
    ssh_ok, ssh_msg = bridge.test_connection()
    print(f"    SSH Status: {ssh_ok} -> {ssh_msg}")

    if not ssh_ok:
        print("[-] First SSH attempt failed, retrying in 3 seconds...")
        time.sleep(3)
        ssh_ok, ssh_msg = bridge.test_connection()
        print(f"    Retry SSH Status: {ssh_ok} -> {ssh_msg}")

    if ssh_ok:
        print("\n[10] Provisioning target with root daemon...")
        p_ok, p_msg = bridge.ensure_target_provisioned()
        print(f"    Target Provisioning: {p_ok} -> {p_msg}")

        print("\n[11] Reading telemetry...")
        data = bridge.read_inspector()
        threads = data.get("threads", [])
        system = data.get("system", {})
        print(f"    Connected: {data.get('connected')}")
        print(f"    Simulated: {data.get('simulated')}")
        print(f"    OS: {system.get('os')}")
        print(f"    Processes: {system.get('num_processes')}")
        print(f"    Threads Count: {len(threads)}")

        if len(threads) >= 100:
            print(f"\n[SUCCESS] QNX 8.0 target fully verified with {len(threads)} live microkernel threads!")
            # Keep script alive for a few seconds so everything settles
            time.sleep(2)
            return True

    return False

if __name__ == "__main__":
    success = main()
    print(f"\nFINAL RESULT: {'SUCCESS' if success else 'FAILED'}")
    sys.exit(0 if success else 1)
