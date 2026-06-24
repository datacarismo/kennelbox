"""Firejail wrapper: builds and executes sandboxed commands within CWD."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console(stderr=True)

# Syscalls too dangerous for agent use
_SECCOMP_DROP = [
    "mount", "umount2", "ptrace", "kexec_load", "open_by_handle_at",
    "init_module", "finit_module", "delete_module", "swapon", "swapoff",
    "reboot", "sethostname", "setdomainname", "iopl", "ioperm",
    "settimeofday", "clock_settime", "adjtimex",
]


def firejail_available() -> bool:
    return shutil.which("firejail") is not None


def build_firejail_args(cwd: Path, sandbox_cfg: dict, env_path: Optional[str] = None) -> list[str]:
    """Construct firejail CLI arguments from sandbox config."""
    args = ["firejail", "--quiet"]

    # Filesystem restrictions
    args += [
        f"--whitelist={cwd}",
        "--blacklist=/home",
        "--blacklist=/root",
        "--blacklist=/etc",
        "--blacklist=/var",
        "--blacklist=/tmp",
        "--blacklist=/proc",
        "--blacklist=/sys",
        f"--chdir={cwd}",
        "--noroot",
        "--private-tmp",
    ]

    # Include venv/node_modules if outside CWD (unlikely but guard anyway)
    if env_path and not env_path.startswith(str(cwd)):
        args.append(f"--whitelist={env_path}")

    # Network
    if sandbox_cfg.get("network", False) is False:
        args.append("--net=none")

    # Seccomp
    if sandbox_cfg.get("seccomp", True):
        drop_list = ",".join(_SECCOMP_DROP)
        args.append(f"--seccomp.drop={drop_list}")

    return args


def run_sandboxed(
    command: list[str],
    cwd: Path,
    sandbox_cfg: dict,
    env_path: Optional[str] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run a command inside firejail. Falls back to plain subprocess with CWD restriction if firejail is absent."""
    if firejail_available():
        jail_args = build_firejail_args(cwd, sandbox_cfg, env_path)
        full_cmd = jail_args + ["--"] + command
    else:
        console.print(
            "  [yellow]firejail not found — running with CWD restriction only (install firejail for full sandboxing)[/yellow]"
        )
        full_cmd = command

    env = os.environ.copy()
    env["HOME"] = str(cwd)  # prevent home escape even without firejail

    return subprocess.run(
        full_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
