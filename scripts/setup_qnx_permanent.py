"""
QNX-Aegis: Permanent Boot & Network Provisioner
Spawns QEMU, connects to serial console during boot, writes permanent startup config
to /boot/onboot so vtnet0 and sshd start on every boot automatically.
"""

import subprocess
import socket
import time
import sys
from pathlib import Path

QEMU_EXE = "D:/qemu/qemu-system-x86_64.exe"
DISK_IMG = "D:/QNX_VM/qemu/output/disk-qemu"

def main():
    print("[*] Launching QEMU daemon...")
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

    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(cmd, creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    print(f"[+] QEMU process launched with PID {proc.pid}")

    print("[*] Connecting to serial console on 127.0.0.1:4444...")
    s = None
    t0 = time.time()
    while time.time() - t0 < 30:
        try:
            s = socket.create_connection(("127.0.0.1", 4444), timeout=1.5)
            print("[+] Connected to serial console.")
            break
        except Exception:
            time.sleep(0.5)

    if not s:
        print("[-] Failed to connect to serial port.")
        sys.exit(1)

    s.settimeout(1.0)
    print("[*] Waiting for QNX boot...")
    output = b""
    logged_in = False
    start_wait = time.time()

    while time.time() - start_wait < 45:
        try:
            chunk = s.recv(1024)
            if chunk:
                output += chunk
                sys.stdout.write(chunk.decode("ascii", errors="ignore"))
                sys.stdout.flush()
                if b"login:" in output:
                    print("\n[+] Found login prompt.")
                    break
                if b"[qnxuser@" in output or b"# " in output:
                    print("\n[+] Already logged in.")
                    logged_in = True
                    break
        except socket.timeout:
            s.sendall(b"\n")
        time.sleep(0.3)

    if not logged_in:
        print("[*] Sending login credentials...")
        s.sendall(b"qnxuser\n")
        time.sleep(1.0)
        s.sendall(b"qnxuser\n")
        time.sleep(1.5)

    print("[*] Sending network and startup configuration commands...")
    commands = [
        b"export PATH=$PATH:/proc/boot:/sbin:/usr/sbin:/bin:/usr/bin\n",
        b"echo qnxuser | sudo -S mount -u -o rw /data 2>/dev/null\n",
        b"echo qnxuser | sudo -S mount -u -o rw /boot 2>/dev/null\n",
        b"echo qnxuser | sudo -S ifconfig vtnet0 10.0.2.15/24 up\n",
        b"echo qnxuser | sudo -S route add default 10.0.2.2 2>/dev/null\n",
        b"echo qnxuser | sudo -S /usr/bin/sshd 2>/dev/null\n",
        b"echo qnxuser | sudo -S /usr/sbin/sshd 2>/dev/null\n",
        # Write permanent /boot/onboot script if /boot is writable
        b"echo qnxuser | sudo -S sh -c 'echo \"#!/bin/sh\\nmount -u -o rw /data\\nifconfig vtnet0 10.0.2.15/24 up\\nroute add default 10.0.2.2 2>/dev/null\\n/usr/bin/sshd 2>/dev/null\\n/usr/sbin/sshd 2>/dev/null\\n\" > /boot/onboot && chmod +x /boot/onboot' 2>/dev/null\n",
        # Move corrupted dhcpcd lock
        b"echo qnxuser | sudo -S mv /data/var/run/dhcpcd /data/var/run/dhcpcd.bad 2>/dev/null\n",
        b"echo qnxuser | sudo -S rm -rf /var/run/dhcpcd/* /data/var/run/dhcpcd/* /var/run/dhcpcd /var/run/sshd.pid /var/run/dhcpcd.pid /data/var/run/dhcpcd.pid 2>/dev/null\n",
        b"echo qnxuser | sudo -S mkdir -p /data/var/run/dhcpcd && echo qnxuser | sudo -S chmod 755 /data/var/run/dhcpcd 2>/dev/null\n",
        b"sync; sync; sync; sleep 1\n",
        b"ifconfig vtnet0\n",
        b"pidin -F '%a %N' | grep ssh\n",
    ]

    for c in commands:
        s.sendall(c)
        time.sleep(0.5)

    time.sleep(2)
    resp = b""
    for _ in range(10):
        try:
            chunk = s.recv(1024)
            if chunk:
                resp += chunk
        except socket.timeout:
            pass
        time.sleep(0.2)

    print("\n[+] Command Execution Response:")
    print(resp.decode("ascii", errors="ignore"))

    print("[*] Closing serial connection...")
    s.close()

    print("[*] Testing SSH connectivity over port 2222...")
    time.sleep(2)
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect("127.0.0.1", port=2222, username="qnxuser", password="qnxuser", timeout=5)
        _, stdout, _ = client.exec_command("uname -a")
        out = stdout.read().decode().strip()
        print(f"[SUCCESS] SSH connected to QNX: {out}")
        client.close()
        return True
    except Exception as e:
        print(f"[FAIL] SSH connection failed: {e}")
        return False

if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)
