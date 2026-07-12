"""MCP-compatible stdio JSON-RPC server bridging agents to the sandboxed workspace."""

from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Allowlist / config loader
# ---------------------------------------------------------------------------

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]


def _load_toml(path: Path) -> dict:
    if tomllib is None:
        raise RuntimeError("tomllib/tomli not available — cannot load config")
    with open(path, "rb") as f:
        return tomllib.load(f)


_BLOCKED_FILENAMES = {".env", ".pem", ".key", ".cert", ".pfx", ".p12"}

# Default blocked argument flags — allow operator to extend via allowlist.toml but never shrink
# below this set (config replaces rather than merges, so these are the shipped defaults).
_DEFAULT_BLOCKED_ARGS: set[str] = {"-c", "-e", "--eval", "--exec", "-x", "--command"}


class AllowlistGuard:
    """Validates tool requests against the project's allowlist.toml."""

    def __init__(self, kennelbox_dir: Path):
        cfg_path = kennelbox_dir / "allowlist.toml"
        if not cfg_path.exists():
            raise FileNotFoundError(f"allowlist.toml not found at {cfg_path}")
        cfg = _load_toml(cfg_path)
        cmds = cfg.get("commands", {})
        files = cfg.get("files", {})
        self.allowed_commands: list[str] = cmds.get("allowed", [])
        # warn_patterns: advisory only — logged but never used to block
        self.warn_patterns: list[str] = cmds.get("warn_patterns", cmds.get("blocked", []))
        self.allowed_extensions: list[str] = files.get("allowed_extensions", [])
        # blocked_args: argument tokens that enable inline code execution — always blocked
        self.blocked_args: set[str] = set(
            cmds.get("blocked_args", list(_DEFAULT_BLOCKED_ARGS))
        )
        limits = cfg.get("limits", {})
        self.max_read_bytes: int = limits.get("max_read_bytes", 10 * 1024 * 1024)
        self.max_write_bytes: int = limits.get("max_write_bytes", 10 * 1024 * 1024)
        self.max_output_bytes: int = limits.get("max_output_bytes", 1 * 1024 * 1024)

    def check_command(self, command: str) -> tuple[bool, str]:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            return False, f"Invalid shell syntax: {exc}"

        for token in tokens[1:]:  # skip the command name itself
            if token in self.blocked_args:
                return False, f"Argument '{token}' is not permitted (inline code execution blocked)"

        for pattern in self.warn_patterns:
            if pattern in command:
                print(
                    f"kennelbox [WARN] command matched advisory pattern '{pattern}': {command!r}",
                    file=sys.stderr,
                )
        for allowed in self.allowed_commands:
            if command == allowed or command.startswith(allowed + " "):
                return True, "ok"
        return False, f"Command not in allowlist. Permitted: {self.allowed_commands}"

    def check_file(self, filepath: str) -> tuple[bool, str]:
        name = Path(filepath).name
        # Explicit filename deny-list (dotfiles with sensitive names)
        if name in _BLOCKED_FILENAMES:
            return False, f"File '{name}' is blocked"
        # Dotfiles not in the allowed set are blocked
        if name.startswith("."):
            return False, f"Dotfile '{name}' is not permitted"
        suffix = Path(filepath).suffix
        if self.allowed_extensions and suffix not in self.allowed_extensions:
            return False, f"File extension '{suffix}' not in allowlist"
        return True, "ok"


# ---------------------------------------------------------------------------
# Path safety
# ---------------------------------------------------------------------------

def _safe_path(cwd: Path, rel: str) -> Path:
    """Resolve path and verify it stays within cwd."""
    cwd_resolved = cwd.resolve()
    target = (cwd_resolved / rel).resolve()
    try:
        target.relative_to(cwd_resolved)
    except ValueError:
        raise PermissionError(f"Path escape blocked: '{rel}' resolves outside project root")
    return target


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def tool_read_file(cwd: Path, guard: AllowlistGuard, sandbox_cfg: dict, params: dict) -> Any:
    path_str = params.get("path", "")
    ok, reason = guard.check_file(path_str)
    if not ok:
        raise PermissionError(reason)
    target = _safe_path(cwd, path_str)  # verify containment before handing to sandbox
    if target.is_file():
        size = target.stat().st_size
        if size > guard.max_read_bytes:
            raise PermissionError(
                f"File is {size} bytes, exceeds max_read_bytes ({guard.max_read_bytes})"
            )
    from sandbox.jail import run_sandboxed_file_op
    return run_sandboxed_file_op("read", str(target), cwd, sandbox_cfg)


def tool_write_file(cwd: Path, guard: AllowlistGuard, sandbox_cfg: dict, params: dict) -> Any:
    path_str = params.get("path", "")
    content = params.get("content", "")
    ok, reason = guard.check_file(path_str)
    if not ok:
        raise PermissionError(reason)
    content_bytes = len(content.encode())
    if content_bytes > guard.max_write_bytes:
        raise PermissionError(
            f"Content is {content_bytes} bytes, exceeds max_write_bytes ({guard.max_write_bytes})"
        )
    target = _safe_path(cwd, path_str)
    kennelbox_dir = cwd.resolve() / ".kennelbox"
    try:
        target.relative_to(kennelbox_dir)
        raise PermissionError("Writes to .kennelbox/ are not permitted")
    except ValueError:
        pass  # target is not inside .kennelbox/ — safe to proceed
    from sandbox.jail import run_sandboxed_file_op
    return run_sandboxed_file_op("write", str(target), cwd, sandbox_cfg, content=content)


def tool_edit_file(cwd: Path, guard: AllowlistGuard, sandbox_cfg: dict, params: dict) -> Any:
    path_str = params.get("path", "")
    old_string = params.get("old_string", "")
    new_string = params.get("new_string", "")
    if not old_string:
        raise ValueError("old_string must not be empty")
    if old_string == new_string:
        raise ValueError("old_string and new_string are identical")
    ok, reason = guard.check_file(path_str)
    if not ok:
        raise PermissionError(reason)
    target = _safe_path(cwd, path_str)
    kennelbox_dir = cwd.resolve() / ".kennelbox"
    try:
        target.relative_to(kennelbox_dir)
        raise PermissionError("Edits to .kennelbox/ are not permitted")
    except ValueError:
        pass  # target is not inside .kennelbox/ — safe to proceed
    if not target.is_file():
        raise FileNotFoundError(f"No such file: {path_str}")
    size = target.stat().st_size
    if size > guard.max_read_bytes:
        raise PermissionError(
            f"File is {size} bytes, exceeds max_read_bytes ({guard.max_read_bytes})"
        )
    import json as _json
    payload = _json.dumps({"old": old_string, "new": new_string})
    from sandbox.jail import run_sandboxed_file_op
    return run_sandboxed_file_op("edit", str(target), cwd, sandbox_cfg, content=payload)


def tool_list_directory(cwd: Path, guard: AllowlistGuard, sandbox_cfg: dict, params: dict) -> Any:
    path_str = params.get("path", ".")
    target = _safe_path(cwd, path_str)
    from sandbox.jail import run_sandboxed_file_op
    return run_sandboxed_file_op("list", str(target), cwd, sandbox_cfg)


def tool_run_command(cwd: Path, guard: AllowlistGuard, sandbox_cfg: dict, params: dict) -> Any:
    command = params.get("command", "")
    ok, reason = guard.check_command(command)
    if not ok:
        raise PermissionError(reason)

    from sandbox.jail import run_sandboxed, firejail_available

    args = shlex.split(command)
    try:
        result = run_sandboxed(args, cwd, sandbox_cfg, timeout=params.get("timeout", 30))
        stdout, stdout_truncated = _truncate(result.stdout, guard.max_output_bytes)
        stderr, stderr_truncated = _truncate(result.stderr, guard.max_output_bytes)
        response = {
            "stdout": stdout,
            "stderr": stderr,
            "returncode": result.returncode,
            "sandboxed": firejail_available(),
        }
        if stdout_truncated or stderr_truncated:
            response["truncated"] = True
        return response
    except Exception as exc:
        raise RuntimeError(str(exc)) from exc


def _truncate(text: str, max_bytes: int) -> tuple[str, bool]:
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode(errors="replace") + "\n[output truncated]", True


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------------

def _ok(req_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def _err(req_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


# MCP tool manifest
_TOOLS_MANIFEST = [
    {
        "name": "read_file",
        "description": "Read the contents of a file within the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from project root"}
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file within the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from project root"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in a file. old_string must match exactly once; the edit fails if it matches zero or multiple times.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path from project root"},
                "old_string": {"type": "string", "description": "Exact text to find (must be unique in the file)"},
                "new_string": {"type": "string", "description": "Text to replace it with"},
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a path within the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path (default: '.')"}
            },
        },
    },
    {
        "name": "run_command",
        "description": "Run an allowed shell command within the sandboxed project directory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"},
                "timeout": {"type": "integer", "description": "Max seconds (default: 30)"},
            },
            "required": ["command"],
        },
    },
]


# ---------------------------------------------------------------------------
# Main server loop
# ---------------------------------------------------------------------------

def dispatch(request: dict, cwd: Path, guard: AllowlistGuard, sandbox_cfg: dict) -> dict:
    req_id = request.get("id")
    method = request.get("method", "")
    params = request.get("params", {})

    try:
        # MCP capability negotiation
        if method == "initialize":
            return _ok(req_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "kennelbox", "version": "0.1.0"},
            })

        if method == "tools/list":
            return _ok(req_id, {"tools": _TOOLS_MANIFEST})

        if method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            if tool_name == "read_file":
                result = tool_read_file(cwd, guard, sandbox_cfg, tool_args)
            elif tool_name == "write_file":
                result = tool_write_file(cwd, guard, sandbox_cfg, tool_args)
            elif tool_name == "edit_file":
                result = tool_edit_file(cwd, guard, sandbox_cfg, tool_args)
            elif tool_name == "list_directory":
                result = tool_list_directory(cwd, guard, sandbox_cfg, tool_args)
            elif tool_name == "run_command":
                result = tool_run_command(cwd, guard, sandbox_cfg, tool_args)
            else:
                return _err(req_id, -32601, f"Unknown tool: {tool_name}")

            return _ok(req_id, {
                "content": [{"type": "text", "text": json.dumps(result, indent=2)}]
            })

        if method == "ping":
            return _ok(req_id, {})

        return _err(req_id, -32601, f"Method not found: {method}")

    except PermissionError as exc:
        return _err(req_id, -32003, f"Permission denied: {exc}")
    except FileNotFoundError as exc:
        return _err(req_id, -32004, f"File not found: {exc}")
    except ValueError as exc:
        return _err(req_id, -32602, f"Invalid params: {exc}")
    except Exception as exc:
        return _err(req_id, -32000, f"Server error: {exc}")


def run_server(cwd: Path, sandbox_cfg: dict) -> None:
    """Run the MCP stdio server until stdin closes."""
    kennelbox_dir = cwd / ".kennelbox"
    guard = AllowlistGuard(kennelbox_dir)

    # Announce server ready on stderr (MCP convention: stdout is the JSON channel)
    print("kennelbox MCP server ready (stdio). Waiting for requests...", file=sys.stderr)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            response = _err(None, -32700, f"Parse error: {exc}")
            print(json.dumps(response), flush=True)
            continue

        # JSON-RPC 2.0: a request without an "id" is a notification — the server
        # MUST NOT reply, even with an error (e.g. MCP's notifications/initialized).
        if "id" not in request:
            method = request.get("method", "")
            print(f"kennelbox [INFO] notification received: {method}", file=sys.stderr)
            continue

        response = dispatch(request, cwd, guard, sandbox_cfg)
        print(json.dumps(response), flush=True)
