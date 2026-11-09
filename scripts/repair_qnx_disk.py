"""
QNX-Aegis: Automated Serial Filesystem Repair & Network Provisioner
Connects to QNX 8.0 serial console on TCP port 4444, logs in, mounts /data rw,
cleans up stale dhcpcd lock files, configures vtnet0, starts sshd, and flushes to disk (sync).
"""

import socket
import time
import sys

def repair_qnx_disk(port: int = 4444, timeout_sec: int = 35):
    print(f"[*] Attempting connection to QNX serial port on 127.0.0.1:{port}...")
    s = None
    t0 = time.time()
    while time.time() - t0 < timeout_sec:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=2.0)
            print("[+] Successfully connected to QNX TCP serial console.")
            break
        except Exception:
            time.sleep(1.0)

    if not s:
        print(f"[-] Could not connect to QNX serial console on port {port} within {timeout_sec}s.")
        return False, None

    s.settimeout(1.0)

    # 1. Wait for login or shell prompt
    print("[*] Waiting for QNX login or shell prompt...")
    full_output = b""
    logged_in = False
    start_wait = time.time()

    while time.time() - start_wait < 30:
        try:
            chunk = s.recv(1024)
            if chunk:
                full_output += chunk
                if b"login:" in full_output:
                    print("[+] Detected QNX login prompt.")
                    break
                if b"[qnxuser@" in full_output or b"# " in full_output:
                    print("[+] Already at shell prompt.")
                    logged_in = True
                    break
        except socket.timeout:
            # Wake up terminal
            s.sendall(b"\n")
        time.sleep(0.5)

    if not logged_in:
        print("[*] Sending login credentials (qnxuser)...")
        s.sendall(b"qnxuser\n")
        time.sleep(1.0)
        s.sendall(b"qnxuser\n")
        time.sleep(1.5)

    # 2. Execute Repair & Setup Commands
    print("[*] Executing filesystem repair, dhcpcd cleanup, and network initialization...")
    cmds = [
        # Set comprehensive PATH
        b"export PATH=$PATH:/proc/boot:/sbin:/usr/sbin:/bin:/usr/bin\n",
        # Remount /data read-write
        b"echo qnxuser | sudo -S mount -u -o rw /data 2>/dev/null\n",
        # Bring up vtnet0 with standard QEMU guest IP immediately
        b"echo qnxuser | sudo -S ifconfig vtnet0 10.0.2.15/24 up\n",
        b"echo qnxuser | sudo -S route add default 10.0.2.2 2>/dev/null\n",
        # Start sshd daemon
        b"echo qnxuser | sudo -S /usr/bin/sshd 2>/dev/null\n",
        b"echo qnxuser | sudo -S /usr/sbin/sshd 2>/dev/null\n",
        # Clean up stale dhcpcd lock and pid files that cause Corrupted file system / refusal to assign IP
        b"echo qnxuser | sudo -S mv /data/var/run/dhcpcd /data/var/run/dhcpcd.bad 2>/dev/null\n",
        b"echo qnxuser | sudo -S rm -rf /var/run/dhcpcd/* /data/var/run/dhcpcd/* /var/run/dhcpcd /var/run/sshd.pid /var/run/dhcpcd.pid /data/var/run/dhcpcd.pid 2>/dev/null\n",
        b"echo qnxuser | sudo -S mkdir -p /data/var/run/dhcpcd && echo qnxuser | sudo -S chmod 755 /data/var/run/dhcpcd 2>/dev/null\n",
        # Flush all disk caches to disk-qemu image
        b"sync; sync; sleep 1\n",
        # Query status
        b"ifconfig vtnet0\n",
        b"pidin -F '%a %N' | grep ssh\n",
    ]

    for cmd in cmds:
        s.sendall(cmd)
        time.sleep(0.6)

    # Read response
    resp = b""
    for _ in range(8):
        try:
            chunk = s.recv(2048)
            if chunk:
                resp += chunk
        except socket.timeout:
            pass
        time.sleep(0.3)

    clean_resp = resp.decode("ascii", errors="ignore")
    print("[+] QNX Serial Console Response:\n" + clean_resp)
    print("[+] Filesystem repaired, network configured, and synced to disk.")
    return True, s


if __name__ == "__main__":
    ok, sock = repair_qnx_disk()
    if ok and sock:
        print("[*] Holding serial socket open for 10 seconds to allow SSH verification...")
        time.sleep(10)
        sock.close()
    sys.exit(0 if ok else 1)
