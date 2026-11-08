"""
QNX-Aegis: QNX SDP 8.0 QCC Compiler Wrapper
Orchestrates cross-compilation with qcc and provides structured compiler diagnostics.
Automatically discovers QNX SDP 8.0 installation on Windows and sets up the environment.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from .config import settings


class QccCompiler:
    def __init__(self, target_arch: Optional[str] = None):
        self.target_arch = target_arch or settings.qnx_compiler_target
        self._env = os.environ.copy()
        self._sdp_bat_path: Optional[Path] = None
        self._qcc_path: Optional[str] = shutil.which("qcc")

        # Auto-detect QNX SDP 8.0 if qcc is not already discoverable in PATH
        if not self._qcc_path:
            self._discover_and_inject_sdp_env()

    def _discover_and_inject_sdp_env(self):
        """Discovers qnxsdp-env.bat and populates environment variables."""
        candidate_paths = [
            settings.qnx_sdp_path,
            os.environ.get("QNX_SDP_PATH"),
            str(Path.home() / "qnx800" / "qnxsdp-env.bat"),
            "C:/qnx800/qnxsdp-env.bat",
            "D:/qnx800/qnxsdp-env.bat",
            "D:/QNX/qnx800/qnxsdp-env.bat",
            "C:/QNX/qnx800/qnxsdp-env.bat",
            "C:/QNX800/qnxsdp-env.bat",
            "D:/QNX800/qnxsdp-env.bat",
        ]

        found_bat: Optional[Path] = None
        for cp in candidate_paths:
            if cp:
                p = Path(cp)
                if p.exists() and p.is_file():
                    found_bat = p
                    break

        if found_bat:
            self._sdp_bat_path = found_bat
            try:
                # Execute batch file and extract exported environment variables
                cmd = f'cmd.exe /c "call \"{found_bat}\" >nul 2>&1 & set"'
                res = subprocess.run(cmd, capture_output=True, text=True, shell=True, timeout=10)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if "=" in line:
                            k, v = line.split("=", 1)
                            self._env[k] = v
                            os.environ[k] = v  # inject into process

                    # Locate qcc inside the newly discovered PATH
                    self._qcc_path = shutil.which("qcc", path=self._env.get("PATH"))
            except Exception:
                pass

        # Fallback: check direct binary path if batch extraction failed
        if not self._qcc_path and found_bat:
            base_dir = found_bat.parent
            direct_qcc = base_dir / "host" / "win64" / "x86_64" / "usr" / "bin" / "qcc.exe"
            if direct_qcc.exists():
                self._qcc_path = str(direct_qcc)
                qnx_target = str(base_dir / "target" / "qnx").replace("\\", "/")
                qnx_host = str(base_dir / "host" / "win64" / "x86_64").replace("\\", "/")
                self._env["QNX_TARGET"] = qnx_target
                self._env["QNX_HOST"] = qnx_host
                self._env["PATH"] = f"{direct_qcc.parent};{self._env.get('PATH', '')}"
                os.environ["QNX_TARGET"] = qnx_target
                os.environ["QNX_HOST"] = qnx_host
                os.environ["PATH"] = self._env["PATH"]

    def is_available(self) -> bool:
        """Check if qcc compiler binary is discoverable."""
        return self._qcc_path is not None

    def get_version(self) -> str:
        """Retrieve qcc version information."""
        if not self.is_available():
            return "qcc not found in system PATH or QNX SDP directories"
        try:
            qcc_bin = self._qcc_path or "qcc"
            res = subprocess.run(
                [qcc_bin, "-V"],
                capture_output=True,
                text=True,
                timeout=5,
                env=self._env
            )
            out = (res.stdout + res.stderr).strip()
            return out if out else f"qcc present at {qcc_bin}"
        except Exception as e:
            return f"Error querying qcc: {e}"

    def compile(
        self,
        source_path: str,
        output_binary: str,
        extra_flags: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Cross-compile C source code using qcc -V<target_arch>.
        Returns structured dictionary with success status and diagnostics.
        """
        src = Path(source_path)
        if not src.is_absolute():
            src = settings.project_root / src

        out = Path(output_binary)
        if not out.is_absolute():
            out = settings.project_root / out

        out.parent.mkdir(parents=True, exist_ok=True)

        qcc_bin = self._qcc_path or "qcc"
        flags = extra_flags or []
        cmd = [qcc_bin, f"-V{self.target_arch}", "-O2", "-Wall", "-Wextra", "-D_QNX_SOURCE"]
        cmd.extend(flags)
        cmd.extend(["-o", str(out), str(src)])

        if not self.is_available():
            return {
                "success": False,
                "simulated": True,
                "exit_code": -1,
                "command": " ".join(cmd),
                "error": "qcc compiler not found in PATH or QNX SDP paths.",
                "diagnostics": [
                    {
                        "file": str(src),
                        "line": 0,
                        "column": 0,
                        "type": "warning",
                        "message": "Host environment missing qcc cross-compiler. Target binary generation skipped."
                    }
                ]
            }

        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60,
                env=self._env
            )
            diagnostics = self._parse_diagnostics(res.stderr + res.stdout)
            return {
                "success": res.returncode == 0,
                "simulated": False,
                "exit_code": res.returncode,
                "command": " ".join(cmd),
                "stdout": res.stdout,
                "stderr": res.stderr,
                "output_binary": str(out) if res.returncode == 0 else None,
                "diagnostics": diagnostics,
            }
        except subprocess.TimeoutExpired:
            return {
                "success": False,
                "simulated": False,
                "exit_code": -1,
                "command": " ".join(cmd),
                "error": "Compilation timed out after 60 seconds",
                "diagnostics": []
            }
        except Exception as ex:
            return {
                "success": False,
                "simulated": False,
                "exit_code": -1,
                "command": " ".join(cmd),
                "error": str(ex),
                "diagnostics": []
            }

    def _parse_diagnostics(self, compiler_output: str) -> List[Dict[str, Any]]:
        """Parses gcc/clang standard error format into structured objects."""
        diagnostics = []
        pattern = re.compile(r"^(.*?):(\d+):(\d+):\s*(error|warning|note):\s*(.*)$")
        for line in compiler_output.splitlines():
            match = pattern.match(line.strip())
            if match:
                diagnostics.append({
                    "file": match.group(1),
                    "line": int(match.group(2)),
                    "column": int(match.group(3)),
                    "type": match.group(4),
                    "message": match.group(5)
                })
        return diagnostics
