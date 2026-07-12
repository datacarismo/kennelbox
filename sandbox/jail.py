"""Firejail wrapper: builds and executes sandboxed commands within CWD."""

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from rich.console import Console

console = Console(stderr=True)

def firejail_available() -> bool:
    return shutil.which("firejail") is not None


# Credential directories that should never be readable by a sandboxed agent.
# System paths (/etc, /usr, etc.) remain accessible because executables need them;
# these targeted blacklists protect user-space secrets specifically.
_CREDENTIAL_DIRS = [
    ".ssh", ".gnupg", ".aws", ".azure", ".config/gcloud",
    ".config/gh", ".netrc", ".npmrc", ".pypirc",
]


def build_firejail_args(cwd: Path, sandbox_cfg: dict, env_path: Optional[str] = None) -> list[str]:
    """Construct firejail CLI arguments from sandbox config."""
    args = ["firejail", "--quiet"]

    # Whitelist-first filesystem isolation: only CWD (and optionally env_path) are visible
    # within the home directory tree. --private-tmp gives the process a clean tmpfs for /tmp;
    # do NOT also blacklist /tmp — that conflicts with --private-tmp.
    args += [
        f"--whitelist={cwd}",
        f"--private-cwd={cwd}",
        "--noroot",
        "--private-tmp",
        "--private-dev",
    ]

    # Block credential directories under $HOME regardless of whitelist mode.
    home = Path(os.environ.get("HOME", "/root"))
    for rel in _CREDENTIAL_DIRS:
        args.append(f"--blacklist={home / rel}")

    # Include venv/node_modules if outside CWD (unlikely but guard anyway)
    if env_path and not env_path.startswith(str(cwd)):
        args.append(f"--whitelist={env_path}")

    # Network
    if sandbox_cfg.get("network", False) is False:
        args.append("--net=none")

    # Seccomp: use firejail's maintained default profile rather than a hand-rolled drop list.
    if sandbox_cfg.get("seccomp", True):
        args.append("--seccomp")

    return args


def _require_firejail() -> None:
    if not firejail_available():
        raise RuntimeError(
            "firejail is required but not installed.\n"
            "Install it with:  sudo apt install firejail\n"
            "kennelbox refuses to start without a kernel-level sandbox."
        )


def run_sandboxed(
    command: list[str],
    cwd: Path,
    sandbox_cfg: dict,
    env_path: Optional[str] = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    """Run a command inside firejail. Raises RuntimeError if firejail is not installed."""
    _require_firejail()
    jail_args = build_firejail_args(cwd, sandbox_cfg, env_path)
    full_cmd = jail_args + ["--"] + command
    env = os.environ.copy()
    env["HOME"] = str(cwd)
    return subprocess.run(
        full_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def run_sandboxed_file_op(
    op: str,
    path: str,
    cwd: Path,
    sandbox_cfg: dict,
    content: str = "",
    timeout: int = 30,
) -> dict:
    """Run a file operation (read/write/list) inside firejail, returning parsed JSON."""
    import json

    _require_firejail()

    _scripts = {
        "read": (
            "import sys,json; f=open(sys.argv[1],errors='replace');"
            " print(json.dumps({'content':f.read(),'path':sys.argv[1]}))"
        ),
        "list": (
            "import sys,json,os; p=sys.argv[1]; entries=["
            "{'name':n,'type':'dir' if os.path.isdir(os.path.join(p,n)) else 'file',"
            "'size':os.path.getsize(os.path.join(p,n)) if os.path.isfile(os.path.join(p,n)) else None}"
            " for n in sorted(os.listdir(p))];"
            " print(json.dumps({'path':p,'entries':entries}))"
        ),
        "write": (
            "import sys,json,os; content=sys.stdin.read(); p=sys.argv[1];"
            " os.makedirs(os.path.dirname(p) or '.', exist_ok=True);"
            " open(p,'w').write(content);"
            " print(json.dumps({'written':p,'bytes':len(content.encode())}))"
        ),
    }

    script = _scripts[op]
    command = ["python3", "-c", script, path]
    jail_args = build_firejail_args(cwd, sandbox_cfg)
    full_cmd = jail_args + ["--"] + command

    env = os.environ.copy()
    env["HOME"] = str(cwd)

    result = subprocess.run(
        full_cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        input=content if op == "write" else None,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"sandboxed {op} failed")

    return json.loads(result.stdout)
