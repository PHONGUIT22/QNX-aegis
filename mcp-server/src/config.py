"""
QNX-Aegis: Configuration Management
Loads environment variables and exposes unified configuration.
"""

import os
from pathlib import Path
from typing import Optional

# Search for .env at project root (2 levels up from this file)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
env_path = PROJECT_ROOT / ".env"

try:
    from dotenv import load_dotenv
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()
except ImportError:
    # Minimal fallback parser for .env when python-dotenv is not installed
    if env_path.exists():
        try:
            with open(env_path, "r", encoding="utf-8") as _f:
                for _line in _f:
                    _line = _line.strip()
                    if _line and not _line.startswith("#") and "=" in _line:
                        _k, _v = _line.split("=", 1)
                        _k, _v = _k.strip(), _v.strip()
                        if _k not in os.environ:
                            os.environ[_k] = _v
        except Exception:
            pass

try:
    from pydantic_settings import BaseSettings
except ImportError:
    try:
        from pydantic import BaseModel
        class BaseSettings(BaseModel):
            pass
    except ImportError:
        class BaseSettings:
            pass


def _find_default_sdp_path() -> str:
    custom = os.getenv("QNX_SDP_PATH")
    if custom and Path(custom).exists():
        return custom
    candidates = [
        Path.home() / "qnx800" / "qnxsdp-env.bat",
        Path("C:/qnx800/qnxsdp-env.bat"),
        Path("D:/qnx800/qnxsdp-env.bat"),
        Path("D:/QNX/qnx800/qnxsdp-env.bat"),
        Path("C:/QNX/qnx800/qnxsdp-env.bat"),
        Path("C:/QNX800/qnxsdp-env.bat"),
        Path("D:/QNX800/qnxsdp-env.bat"),
    ]
    for c in candidates:
        if c.exists():
            return str(c).replace("\\", "/")
    return custom or "C:/QNX800/qnxsdp-env.bat"


class Settings(BaseSettings):
    # QNX SDP 8.0 Paths
    qnx_sdp_path: str = _find_default_sdp_path()
    qnx_compiler_target: str = os.getenv("QNX_COMPILER_TARGET", "gcc_ntox86_64")

    # QNX QEMU Target SSH Configuration
    qemu_host: str = os.getenv("QEMU_HOST", "127.0.0.1")
    qemu_ssh_port: int = int(os.getenv("QEMU_SSH_PORT", "2222"))
    qemu_ssh_user: str = os.getenv("QEMU_SSH_USER", "qnxuser")
    qemu_ssh_password: str = os.getenv("QEMU_SSH_PASSWORD", "qnxuser")
    qemu_ssh_key_path: Optional[str] = os.getenv("QEMU_SSH_KEY_PATH", None)
    qemu_deploy_dir: str = os.getenv("QEMU_DEPLOY_DIR", "/tmp/qnx-aegis")

    # QNX QEMU Process Configuration
    qemu_bin_path: str = os.getenv("QEMU_BIN_PATH", "D:/qemu/qemu-system-x86_64.exe")
    qemu_disk_image: str = os.getenv("QEMU_DISK_IMAGE", "D:/QNX_VM/qemu/output/disk-qemu")
    qemu_memory_mb: int = int(os.getenv("QEMU_MEMORY_MB", "2048"))
    qemu_smp: int = int(os.getenv("QEMU_SMP", "4"))

    # Host MCP & API Bridge
    mcp_server_host: str = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
    mcp_server_port: int = int(os.getenv("MCP_SERVER_PORT", "8080"))
    api_bridge_host: str = os.getenv("API_BRIDGE_HOST", "0.0.0.0")
    api_bridge_port: int = int(os.getenv("API_BRIDGE_PORT", "8000"))
    telemetry_poll_interval: float = float(os.getenv("TELEMETRY_POLL_INTERVAL", "2.0"))
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def target_src_dir(self) -> Path:
        return PROJECT_ROOT / "qnx-target"

    @property
    def target_bin_dir(self) -> Path:
        return PROJECT_ROOT / "qnx-target" / "bin"


settings = Settings()
