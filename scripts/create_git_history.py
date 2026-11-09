import subprocess
import os
import sys
import datetime
import random
from pathlib import Path

# Repo root
REPO_DIR = Path(__file__).resolve().parent.parent
os.chdir(str(REPO_DIR))

# Ensure clean git environment
os.environ["GIT_TERMINAL_PROMPT"] = "0"

def run_git(args, env=None, check=True):
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    res = subprocess.run(["git"] + args, env=merged_env, capture_output=True, text=True)
    if check and res.returncode != 0:
        print(f"[-] Git error: {' '.join(args)}\nSTDOUT: {res.stdout}\nSTDERR: {res.stderr}")
        raise RuntimeError(f"git {' '.join(args)} failed with code {res.returncode}")
    return res

def generate_timestamps(n_commits=30):
    start = datetime.datetime(2026, 11, 5, 8, 30, 0)
    end = datetime.datetime(2026, 11, 10, 11, 45, 0)
    total_seconds = (end - start).total_seconds()
    
    # 29 intervals between 30 commits
    n_intervals = n_commits - 1
    random.seed(1337)
    
    # Generate weights with average around 4.25h, min ~2.2h, max ~6.8h
    raw_weights = [random.uniform(2.2, 6.8) for _ in range(n_intervals)]
    scale = total_seconds / sum(raw_weights)
    intervals = [w * scale for w in raw_weights]
    
    timestamps = [start]
    for sec in intervals:
        timestamps.append(timestamps[-1] + datetime.timedelta(seconds=sec))
        
    for i in range(len(timestamps) - 1):
        diff_h = (timestamps[i+1] - timestamps[i]).total_seconds() / 3600.0
        assert 2.0 <= diff_h <= 24.0, f"Interval {i} invalid: {diff_h} hours"
        
    return timestamps

COMMITS = [
    {
        "files": [".gitignore", "LICENSE", ".env.example"],
        "message": "chore: initial repository configuration and licensing"
    },
    {
        "files": ["docs/ARCHITECTURE.md"],
        "message": "docs: document microkernel bridge architecture and MCP design"
    },
    {
        "files": ["docs/QNX_RESOURCES.md"],
        "message": "docs: add QNX Neutrino 8.0 SDP reference and IPC specifications"
    },
    {
        "files": ["qnx-target/inspector-daemon/qnx_inspector.h"],
        "message": "feat(qnx-target): define core inspector telemetry data structures"
    },
    {
        "files": ["qnx-target/inspector-daemon/slog2_reader.c"],
        "message": "feat(qnx-target): implement slog2 buffer parser for system log extraction"
    },
    {
        "files": ["qnx-target/inspector-daemon/ipc_analyzer.c"],
        "message": "feat(qnx-target): implement procfs traversal and IPC deadlock detection"
    },
    {
        "files": ["qnx-target/inspector-daemon/main.c"],
        "message": "feat(qnx-target): implement /dev/qnx_inspector resource manager and dump CLI"
    },
    {
        "files": ["qnx-target/inspector-daemon/Makefile"],
        "message": "build(qnx-target): add Makefile for inspector daemon cross-compilation"
    },
    {
        "files": ["qnx-target/demos/ipc_deadlock_demo.c"],
        "message": "feat(demos): implement circular MsgSend deadlock simulation demo"
    },
    {
        "files": ["qnx-target/demos/ipc_healthy_demo.c"],
        "message": "feat(demos): implement healthy producer-consumer IPC channel demo"
    },
    {
        "files": ["qnx-target/demos/slog2_crash_demo.c"],
        "message": "feat(demos): implement slog2 critical error and severity logging demo"
    },
    {
        "files": ["qnx-target/demos/Makefile", "qnx-target/Makefile"],
        "message": "build(qnx-target): add master Makefiles for QNX target demo binaries"
    },
    {
        "files": ["scripts/setup-env.ps1"],
        "message": "feat(scripts): add PowerShell environment setup for QNX SDP 8.0"
    },
    {
        "files": ["scripts/build-target.ps1"],
        "message": "feat(scripts): add automated cross-compilation script for qcc"
    },
    {
        "files": ["scripts/run-qemu.bat"],
        "message": "feat(scripts): add QEMU x86_64 launcher script for QNX target VM"
    },
    {
        "files": ["scripts/deploy-to-qemu.ps1"],
        "message": "feat(scripts): implement automated SSH/SCP binary deployment script"
    },
    {
        "files": ["mcp-server/pyproject.toml", "mcp-server/requirements.txt"],
        "message": "feat(mcp-server): configure Python package metadata and dependencies"
    },
    {
        "files": ["mcp-server/src/__init__.py", "mcp-server/src/config.py"],
        "message": "feat(mcp-server): implement configuration management and environment loading"
    },
    {
        "files": ["mcp-server/src/qcc_compiler.py"],
        "message": "feat(mcp-server): implement qcc cross-compiler wrapper with AI diagnostics"
    },
    {
        "files": ["mcp-server/src/deadlock_detective.py"],
        "message": "feat(mcp-server): implement graph cycle detection engine for QNX IPC channels"
    },
    {
        "files": ["mcp-server/src/qemu_bridge.py"],
        "message": "feat(mcp-server): implement QemuBridge persistent SSH communication layer"
    },
    {
        "files": ["mcp-server/src/server.py"],
        "message": "feat(mcp-server): implement Model Context Protocol (MCP) server endpoints"
    },
    {
        "files": ["mcp-server/src/api_bridge.py"],
        "message": "feat(api): implement FastAPI REST endpoints and telemetry stream bridge"
    },
    {
        "files": ["mcp-server/test_mcp_tools.py", "mcp-server/test_live.py"],
        "message": "test(mcp): add automated test suites for MCP tools and bridge operations"
    },
    {
        "files": ["dashboard/index.html"],
        "message": "feat(dashboard): create Web Studio UI structure with cyber-dark theme"
    },
    {
        "files": ["dashboard/styles.css"],
        "message": "feat(dashboard): add styling for telemetry cards and real-time pulse states"
    },
    {
        "files": ["dashboard/app.js"],
        "message": "feat(dashboard): implement Cytoscape.js real-time IPC graph visualization"
    },
    {
        "files": ["docs/CODELAB.md"],
        "message": "docs(codelab): add step-by-step hackathon tutorial and verification guide"
    },
    {
        "files": [
            "scripts/repair_qnx_disk.py",
            "scripts/serial_master.py",
            "scripts/setup_qnx_permanent.py",
            "scripts/interactive_serial_debug.py",
            "scripts/boot_and_configure.py",
            "scripts/create_git_history.py"
        ],
        "message": "feat(scripts): add self-healing target diagnostics and serial recovery utilities"
    },
    {
        "files": [
            "README.md",
            "run_live_test.bat",
            "run_mcp_tests.bat",
            "start_dashboard.bat",
            "-f", "qnx-target/bin/ipc_deadlock_demo",
            "-f", "qnx-target/bin/ipc_healthy_demo",
            "-f", "qnx-target/bin/ipc_healthy_demo_test",
            "-f", "qnx-target/bin/qnx_inspector",
            "-f", "qnx-target/bin/slog2_crash_demo"
        ],
        "message": "chore: complete project documentation, launch scripts, and target binaries"
    }
]

def main():
    print(f"[*] Total planned commits: {len(COMMITS)}")
    assert len(COMMITS) == 30, f"Expected exactly 30 commits, got {len(COMMITS)}"

    timestamps = generate_timestamps(30)
    print(f"[+] Generated 30 timestamps starting from {timestamps[0]} to {timestamps[-1]}")

    # Initialize git repo
    print("[*] Initializing git repository on branch 'main'...")
    run_git(["init", "-b", "main"])
    run_git(["config", "commit.gpgsign", "false"])
    run_git(["config", "user.email", "np79857@gmail.com"])
    run_git(["config", "user.name", "PHONGUIT22"])

    # Configure remote
    remote_url = "https://github.com/PHONGUIT22/QNX-aegis.git"
    existing_remotes = run_git(["remote"], check=False).stdout.split()
    if "origin" in existing_remotes:
        run_git(["remote", "set-url", "origin", remote_url])
    else:
        run_git(["remote", "add", "origin", remote_url])

    for idx, c in enumerate(COMMITS):
        commit_num = idx + 1
        ts = timestamps[idx]
        ts_str = ts.strftime("%Y-%m-%d %H:%M:%S +0700")
        diff_str = ""
        if idx > 0:
            diff_h = (ts - timestamps[idx-1]).total_seconds() / 3600.0
            diff_str = f" (+{diff_h:.2f}h)"

        print(f"\n[{commit_num:02d}/30] {ts_str}{diff_str} -> {c['message']}")

        # Stage files
        add_args = ["add"] + c["files"]
        run_git(add_args)

        # Commit with specific timestamp
        env = {
            "GIT_AUTHOR_NAME": "PHONGUIT22",
            "GIT_AUTHOR_EMAIL": "np79857@gmail.com",
            "GIT_COMMITTER_NAME": "PHONGUIT22",
            "GIT_COMMITTER_EMAIL": "np79857@gmail.com",
            "GIT_AUTHOR_DATE": ts_str,
            "GIT_COMMITTER_DATE": ts_str,
        }
        run_git(["commit", "--no-gpg-sign", "-m", c["message"]], env=env)

    print("\n[+] All 30 commits created successfully!")
    print("\n[*] Commit log summary:")
    res = run_git(["log", "--oneline", "--format=%h | %ad | %s", "--date=iso-local"])
    print(res.stdout)

    print("\n[*] Pushing branch 'main' to origin https://github.com/PHONGUIT22/QNX-aegis.git ...")
    push_res = run_git(["push", "-u", "origin", "main", "--force"])
    print(push_res.stdout)
    print(push_res.stderr)
    print("\n[SUCCESS] Successfully pushed all 30 commits to GitHub!")

if __name__ == "__main__":
    main()
