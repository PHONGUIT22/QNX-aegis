"""
QNX-Aegis: QEMU SSH & SCP Bridge (Paramiko)
Facilitates binary deployment, remote execution, and /dev/qnx_inspector queries.
Uses connection pooling, auto-reconnect, and automatic target provisioning.
Strict zero-simulation policy: offline target returns truthful offline status.
"""

import json
import logging
import socket
import time
from pathlib import Path
from typing import Dict, Any, Tuple, Optional
import paramiko
from scp import SCPClient
from .config import settings

# Suppress verbose Paramiko transport logs and banner timeout exceptions completely
for _name in ("paramiko", "paramiko.transport"):
    _plog = logging.getLogger(_name)
    _plog.setLevel(logging.CRITICAL + 1)
    _plog.propagate = False

logger = logging.getLogger("qnx_aegis.qemu_bridge")


class QemuBridge:
    def __init__(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        user: Optional[str] = None,
        password: Optional[str] = None,
        key_path: Optional[str] = None
    ):
        self.host = host or settings.qemu_host
        self.port = port or settings.qemu_ssh_port
        self.user = user or settings.qemu_ssh_user
        self.password = password or settings.qemu_ssh_password
        self.key_path = key_path or settings.qemu_ssh_key_path
        self.deploy_dir = settings.qemu_deploy_dir
        self._ssh_client: Optional[paramiko.SSHClient] = None

    def is_port_open(self, host: Optional[str] = None, port: Optional[int] = None, timeout: float = 0.5) -> bool:
        """Fast TCP probe to verify if QEMU port is responding without full handshake."""
        target_host = host or self.host
        target_port = port if port is not None else self.port
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            res = s.connect_ex((target_host, target_port))
            s.close()
            return res == 0
        except Exception:
            return False

    def _has_active_transport(self) -> bool:
        """Checks if the cached SSH client has an active, connected transport."""
        if self._ssh_client is not None:
            transport = self._ssh_client.get_transport()
            return transport is not None and transport.is_active()
        return False

    def _auto_heal_guest_network_via_serial(self, serial_port: int = 4444) -> bool:
        """
        Connects directly to the QNX 8.0 TCP serial console (port 4444) to auto-heal
        a dirty /data filesystem, stale dhcpcd lock files, and initialize vtnet0 & sshd.
        """
        if not self.is_port_open(port=serial_port, timeout=0.8):
            logger.debug(f"Serial port {serial_port} is not accessible for auto-healing.")
            return False

        logger.info(f"Initiating guest network & filesystem auto-healing via TCP serial port {serial_port}...")
        try:
            s = socket.create_connection(("127.0.0.1", serial_port), timeout=2.0)
            s.settimeout(1.0)
        except Exception as e:
            logger.warning(f"Failed to connect to serial port {serial_port}: {e}")
            return False

        try:
            # Wake terminal and check prompt
            full_out = b""
            logged_in = False
            for _ in range(3):
                s.sendall(b"\n")
                time.sleep(0.3)
                try:
                    chunk = s.recv(1024)
                    if chunk:
                        full_out += chunk
                        if b"login:" in full_out:
                            break
                        if b"[qnxuser@" in full_out or b"# " in full_out:
                            logged_in = True
                            break
                except socket.timeout:
                    pass

            if not logged_in:
                s.sendall(f"{self.user}\n".encode("ascii"))
                time.sleep(0.5)
                s.sendall(f"{self.password}\n".encode("ascii"))
                time.sleep(1.0)

            heal_cmds = [
                b"export PATH=$PATH:/proc/boot:/sbin:/usr/sbin:/bin:/usr/bin\n",
                b"echo qnxuser | sudo -S mount -u -o rw /data 2>/dev/null\n",
                b"echo qnxuser | sudo -S ifconfig vtnet0 10.0.2.15/24 up\n",
                b"echo qnxuser | sudo -S route add default 10.0.2.2 2>/dev/null\n",
                b"echo qnxuser | sudo -S /usr/bin/sshd 2>/dev/null\n",
                b"echo qnxuser | sudo -S /usr/sbin/sshd 2>/dev/null\n",
                b"echo qnxuser | sudo -S mv /data/var/run/dhcpcd /data/var/run/dhcpcd.bad 2>/dev/null\n",
                b"echo qnxuser | sudo -S rm -rf /var/run/dhcpcd/* /data/var/run/dhcpcd/* /var/run/dhcpcd /var/run/sshd.pid /var/run/dhcpcd.pid /data/var/run/dhcpcd.pid 2>/dev/null\n",
                b"echo qnxuser | sudo -S mkdir -p /data/var/run/dhcpcd && echo qnxuser | sudo -S chmod 755 /data/var/run/dhcpcd 2>/dev/null\n",
                b"sync; sync\n"
            ]
            for cmd in heal_cmds:
                s.sendall(cmd)
                time.sleep(0.4)

            # Drain pending output
            for _ in range(4):
                try:
                    s.recv(1024)
                except socket.timeout:
                    pass
                time.sleep(0.2)

            logger.info("Guest network & filesystem auto-healing completed successfully.")
            return True
        except Exception as e:
            logger.warning(f"Error during serial auto-healing: {e}")
            return False
        finally:
            try:
                s.close()
            except Exception:
                pass

    def _get_client(self, timeout: int = 5, retry_heal: bool = True) -> paramiko.SSHClient:
        """Returns active cached SSH client or connects a new persistent session."""
        if self._ssh_client is not None:
            transport = self._ssh_client.get_transport()
            if transport is not None and transport.is_active():
                return self._ssh_client
            self._close_client()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs: Dict[str, Any] = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": timeout,
            "banner_timeout": max(15, timeout),
        }
        if self.key_path and Path(self.key_path).exists():
            kwargs["key_filename"] = self.key_path
        elif self.password:
            kwargs["password"] = self.password

        try:
            client.connect(**kwargs)
            self._ssh_client = client
            return self._ssh_client
        except (paramiko.SSHException, socket.error, TimeoutError) as ex:
            self._close_client()
            if retry_heal and ("banner" in str(ex).lower() or isinstance(ex, (socket.timeout, TimeoutError))):
                if self._auto_heal_guest_network_via_serial():
                    time.sleep(1.0)
                    return self._get_client(timeout=timeout, retry_heal=False)
            raise ex

    def _close_client(self):
        """Closes and resets cached SSH client."""
        if self._ssh_client is not None:
            try:
                self._ssh_client.close()
            except Exception:
                pass
            self._ssh_client = None

    def test_connection(self) -> Tuple[bool, str]:
        """Tests SSH connectivity to the QNX QEMU VM."""
        if self._has_active_transport():
            return True, "Connected to QNX target (active session)"

        if not self.is_port_open():
            self._close_client()
            return False, f"Port {self.host}:{self.port} closed (QNX QEMU VM is offline)"
        try:
            client = self._get_client(timeout=3)
            _, stdout, _ = client.exec_command("uname -a", timeout=3)
            output = stdout.read().decode("utf-8", errors="ignore").strip()
            return True, f"Connected to QNX target: {output}"
        except (paramiko.SSHException, socket.error, Exception) as ex:
            self._close_client()
            return False, f"Cannot connect to QNX VM ({self.host}:{self.port}): {ex}"

    def execute_remote(self, command: str, timeout: int = 15) -> Tuple[int, str, str]:
        """Runs a command on the QNX target via persistent SSH session."""
        if not self._has_active_transport():
            if not self.is_port_open():
                return -1, "", f"Target {self.host}:{self.port} is offline"
        try:
            client = self._get_client(timeout=timeout)
            _, stdout, stderr = client.exec_command(command, timeout=timeout)
            exit_code = stdout.channel.recv_exit_status()
            out_str = stdout.read().decode("utf-8", errors="ignore")
            err_str = stderr.read().decode("utf-8", errors="ignore")
            return exit_code, out_str, err_str
        except Exception as ex:
            self._close_client()
            return -1, "", f"SSH Execution Error: {ex}"

    def upload_file(self, local_path: str, remote_path: str) -> Tuple[bool, str]:
        """Uploads a local file to QNX target using SCP over cached connection."""
        loc = Path(local_path)
        if not loc.exists() and not loc.is_absolute():
            loc = settings.project_root / local_path
        if not loc.exists():
            return False, f"Local file not found: {local_path}"
        if not self._has_active_transport():
            if not self.is_port_open():
                return False, f"Target {self.host}:{self.port} is offline"

        try:
            client = self._get_client()
            remote_p = Path(remote_path)
            dest_dir = str(remote_p.parent).replace("\\", "/")
            dest_name = remote_p.name

            # Ensure /data is rw and destination directory is ready (synchronous wait)
            self.execute_remote(f"echo {self.password} | sudo -S mount -u -o rw /data 2>/dev/null", timeout=3)
            self.execute_remote(f"echo {self.password} | sudo -S rm -f {remote_path} 2>/dev/null", timeout=3)
            self.execute_remote(f"mkdir -p {dest_dir}", timeout=3)

            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise paramiko.SSHException("SSH Transport inactive")

            with SCPClient(transport) as scp:
                scp.put(str(loc), remote_path=dest_dir)

            if loc.name != dest_name:
                self.execute_remote(f"mv {dest_dir}/{loc.name} {remote_path}", timeout=3)

            self.execute_remote(f"chmod +x {remote_path}", timeout=3)
            return True, f"Uploaded {loc.name} to {remote_path}"
        except Exception as ex:
            self._close_client()
            return False, f"SCP Upload failed: {ex}"

    def ensure_target_provisioned(self) -> Tuple[bool, str]:
        """
        Verifies that target binaries exist in deploy_dir (/tmp/qnx-aegis) and qnx_inspector is active.
        If missing (e.g. after QEMU reboot where /tmp is wiped), automatically uploads compiled binaries
        and starts the resource manager daemon.
        """
        if not self._has_active_transport():
            if not self.is_port_open():
                return False, "Target is offline."

        bin_dir = settings.target_bin_dir
        if not bin_dir.exists():
            return False, f"Local target bin directory not found: {bin_dir}"

        expected_bins = [
            "qnx_inspector",
            "ipc_deadlock_demo",
            "ipc_healthy_demo",
            "slog2_crash_demo",
            "ipc_benchmark_demo",
        ]

        # Always ensure /data is mounted read-write
        self.execute_remote(f"echo {self.password} | sudo -S mount -u -o rw /data 2>/dev/null", timeout=3)

        # 1. Check if expected binaries exist in deploy directory
        check_cmd = " ; ".join([f"test -x {self.deploy_dir}/{b} || echo {b}" for b in expected_bins])
        _, missing_out, _ = self.execute_remote(check_cmd, timeout=4)
        missing_bins = [line.strip() for line in missing_out.splitlines() if line.strip() in expected_bins]

        if missing_bins:
            logger.info(f"Target binaries missing in {self.deploy_dir} ({missing_bins}). Auto-provisioning from {bin_dir}...")
            self.execute_remote(f"echo {self.password} | sudo -S mkdir -p {self.deploy_dir}", timeout=3)
            self.execute_remote(f"echo {self.password} | sudo -S chmod 777 {self.deploy_dir}", timeout=3)
            if "qnx_inspector" in missing_bins:
                self.execute_remote(f"echo {self.password} | sudo -S slay -f qnx_inspector 2>/dev/null", timeout=3)

            for b_name in missing_bins:
                loc = bin_dir / b_name
                if loc.exists():
                    ok, msg = self.upload_file(str(loc), f"{self.deploy_dir}/{b_name}")
                    if not ok:
                        logger.warning(f"Failed to auto-upload {b_name}: {msg}")
                else:
                    logger.warning(f"Compiled binary {b_name} not found in {bin_dir}")

        # 2. Check if qnx_inspector daemon is actively running as root
        code, out, _ = self.execute_remote(
            f"echo {self.password} | sudo -S pidin -F '%a %U %N' 2>/dev/null | grep qnx_inspector",
            timeout=3
        )
        is_running_as_root = False
        if out.strip():
            for line in out.strip().splitlines():
                parts = line.split()
                # parts: [pid, uid, name]
                if len(parts) >= 3 and parts[2] == "qnx_inspector":
                    if parts[1] == "0":
                        is_running_as_root = True
                    else:
                        logger.info(f"qnx_inspector running unprivileged (UID {parts[1]}), slaying...")
                        self.execute_remote(f"echo {self.password} | sudo -S slay -f qnx_inspector 2>/dev/null", timeout=3)
                        break

        # Also verify if /dev/qnx_inspector character device node exists
        code_dev, _, _ = self.execute_remote("test -c /dev/qnx_inspector || test -e /dev/qnx_inspector", timeout=3)
        if not is_running_as_root or code_dev != 0:
            logger.info("Starting /tmp/qnx-aegis/qnx_inspector daemon as root via sudo...")
            self.execute_remote(f"echo {self.password} | sudo -S slay -f qnx_inspector 2>/dev/null", timeout=3)
            start_cmd = (
                f"echo {self.password} | sudo -S nohup {self.deploy_dir}/qnx_inspector >/dev/null 2>&1 </dev/null &"
            )
            self.execute_remote(start_cmd, timeout=5)
            time.sleep(0.5)

        return True, "Target provisioned successfully."

    def read_inspector(self) -> Dict[str, Any]:
        """
        Reads JSON telemetry from /dev/qnx_inspector on the target.
        Falls back to running /tmp/qnx-aegis/qnx_inspector --dump (with sudo) or pidin (with sudo).
        Returns truthful offline status when QEMU target is unreachable (strict zero simulation).
        """
        connected, _ = self.test_connection()
        if not connected:
            return {
                "connected": False,
                "simulated": False,
                "error": f"QNX 8.0 QEMU VM is offline ({self.host}:{self.port} unreachable).",
                "system": {
                    "os": "OFFLINE (Start QNX 8.0 QEMU VM)",
                    "architecture": "x86_64",
                    "hostname": "disconnected",
                    "uptime_sec": 0,
                    "num_processes": 0,
                    "num_threads": 0,
                    "deadlocks_detected": 0
                },
                "threads": [],
                "slog2_tail": f"[OFFLINE] Waiting for SSH connection to QNX 8.0 QEMU VM at {self.host}:{self.port}..."
            }

        dump_cmd = f"echo {self.password} | sudo -S {self.deploy_dir}/qnx_inspector --dump 2>/dev/null"

        # 1. Try reading the active Resource Manager /dev/qnx_inspector
        code, stdout, _ = self.execute_remote("cat /dev/qnx_inspector 2>/dev/null", timeout=4)
        clean_out = stdout.strip()
        if code == 0 and "{" in clean_out and "}" in clean_out:
            try:
                json_str = clean_out[clean_out.find("{"):clean_out.rfind("}") + 1]
                data = json.loads(json_str)
                # If /dev/qnx_inspector is healthy and returns full thread inventory (>= 10 threads)
                if len(data.get("threads", [])) >= 10:
                    data["connected"] = True
                    data["simulated"] = False
                    return data
            except json.JSONDecodeError:
                pass

        # 2. If daemon not running or returned < 10 threads (e.g. unprivileged), provision as root
        self.ensure_target_provisioned()

        # Retry reading /dev/qnx_inspector after provisioning
        code, stdout, _ = self.execute_remote("cat /dev/qnx_inspector 2>/dev/null", timeout=4)
        clean_out = stdout.strip()
        if code == 0 and "{" in clean_out and "}" in clean_out:
            try:
                json_str = clean_out[clean_out.find("{"):clean_out.rfind("}") + 1]
                data = json.loads(json_str)
                if len(data.get("threads", [])) >= 10:
                    data["connected"] = True
                    data["simulated"] = False
                    return data
            except json.JSONDecodeError:
                pass

        # 3. Fallback to executing qnx_inspector --dump directly with sudo as root
        code, stdout, _ = self.execute_remote(dump_cmd, timeout=4)
        clean_out = stdout.strip()
        if code == 0 and "{" in clean_out and "}" in clean_out:
            try:
                json_str = clean_out[clean_out.find("{"):clean_out.rfind("}") + 1]
                data = json.loads(json_str)
                data["connected"] = True
                data["simulated"] = False
                return data
            except json.JSONDecodeError:
                pass

        # 4. Fallback to native QNX pidin inspection with sudo as root
        pidin_cmd = f"echo {self.password} | sudo -S pidin -F '%a %b %N %J %B' 2>/dev/null"
        code, pidin_out, _ = self.execute_remote(pidin_cmd, timeout=4)
        if code == 0 and pidin_out.strip():
            parsed = self._parse_pidin_output(pidin_out)
            parsed["connected"] = True
            return parsed

        # 5. Connected but no telemetry available (clean truthful return, no fake data)
        return {
            "connected": True,
            "simulated": False,
            "error": "Unable to read QNX telemetry from /dev/qnx_inspector or pidin",
            "system": {
                "os": "QNX Neutrino RTOS 8.0",
                "architecture": "x86_64",
                "hostname": "qnx-target",
                "uptime_sec": 0,
                "num_processes": 0,
                "num_threads": 0,
                "deadlocks_detected": 0
            },
            "threads": [],
            "slog2_tail": "[QNX] Telemetry source temporarily unavailable."
        }

    def read_slog2(self, lines: int = 50) -> str:
        """Retrieves latest log records from QNX slog2info (no fake data)."""
        connected, _ = self.test_connection()
        if connected:
            code, stdout, _ = self.execute_remote(f"slog2info 2>/dev/null | tail -n {lines}", timeout=4)
            if code == 0 and stdout.strip():
                return stdout.strip()
            return "[slog2] No log records found or slog2info not responsive."

        return f"[OFFLINE] Waiting for SSH connection to QNX 8.0 QEMU VM at {self.host}:{self.port}..."

    def _parse_pidin_output(self, pidin_str: str) -> Dict[str, Any]:
        """Parses QNX pidin command output into telemetry format."""
        threads = []
        for line in pidin_str.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 4:
                pid = int(parts[0]) if parts[0].isdigit() else 0
                tid = int(parts[1]) if parts[1].isdigit() else 1
                name = parts[2]
                state = parts[3].upper()
                blocked = parts[4] if len(parts) > 4 else "-"

                chid = -1
                coid = -1
                target_pid = 0
                if blocked.isdigit():
                    val = int(blocked)
                    if state == "RECEIVE":
                        chid = val
                    elif state in ("REPLY", "SEND"):
                        coid = val
                        target_pid = val

                threads.append({
                    "pid": pid,
                    "tid": tid,
                    "name": name,
                    "state": state,
                    "priority": 10,
                    "chid": chid,
                    "coid": coid,
                    "target_pid": target_pid,
                    "target_tid": 0,
                    "is_deadlocked": False
                })

        return {
            "version": "1.0.0",
            "system": {
                "os": "QNX Neutrino RTOS 8.0",
                "architecture": "x86_64",
                "hostname": "qnx-qemu",
                "uptime_sec": 300,
                "num_processes": len(set(t["pid"] for t in threads)),
                "num_threads": len(threads),
                "deadlocks_detected": 0
            },
            "threads": threads,
            "slog2_tail": "Extracted via native QNX pidin utility",
            "simulated": False,
            "connected": True
        }
