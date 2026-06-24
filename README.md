# kennelbox

A local sandboxed AI agent workspace CLI. Agents like OpenClaw and Hermes Agent connect to a project directory via MCP (Model Context Protocol) — with hard restrictions on filesystem scope, command execution, and network access.

```
╔══════════════════════════════╗
║  ██╗  ██╗██████╗             ║
║  ██║ ██╔╝██╔══██╗            ║
║  █████╔╝ ██████╔╝            ║
║  ██╔═██╗ ██╔══██╗            ║
║  ██║  ██╗██████╔╝            ║
║  ╚═╝  ╚═╝╚═════╝             ║
╠══════════════════════════════╣
║  KENNELBOX  //  v0.1.0       ║
║  agent sandbox + MCP bridge  ║
╠══════════════════════════════╣
║  $ kennelbox run --agent <x> ║
╚══════════════════════════════╝
```

## Features

- **CWD lock** — agents can only read/write inside the directory where `kennelbox init` was run
- **Auto-venv** — detects Python, Node, or generic projects and sets up an isolated environment
- **Firejail sandbox** — wraps agent execution with filesystem and network restrictions, seccomp filtering
- **Allow/blocklist** — explicit per-project command and file-extension rules; unknown = blocked
- **MCP bridge** — stdio JSON-RPC 2.0 server compatible with any MCP-capable agent

---

## Requirements

- Python 3.10+
- Linux (Ubuntu / Zorin OS or compatible)
- `firejail` (optional but strongly recommended for full sandboxing)

### Install firejail

```bash
sudo apt install firejail
```

> kennelbox works without firejail but falls back to CWD-only restriction enforced in software. Install firejail for full kernel-level isolation.

---

## Installation

### From source

```bash
git clone https://github.com/datacarismo/kennelbox.git
cd kennelbox
pip install -e .
```

### Via pip (once published)

```bash
pip install kennelbox
```

---

## Quick Start

```bash
cd /path/to/your/project

# 1. Initialize the sandbox
kennelbox init

# 2. Check the rules
kennelbox rules

# 3. Start a sandboxed agent session (stdio MCP)
kennelbox run --agent openclaw

# 4. Check status
kennelbox status

# 5. Tear down when done
kennelbox release
```

---

## CLI Reference

| Command | Description |
|---|---|
| `kennelbox init` | Initialize `.kennelbox/` in the current directory |
| `kennelbox run --agent <name>` | Start sandboxed MCP session for the named agent |
| `kennelbox rules` | Show allow/blocklist tables |
| `kennelbox status` | Show sandbox state (agent, network, firejail) |
| `kennelbox release` | Delete `.kennelbox/` and tear down the sandbox |

All commands accept `--cwd <path>` to target a different directory.

---

## Configuration

On `init`, kennelbox creates a `.kennelbox/` directory with two config files:

### `.kennelbox/allowlist.toml`

Controls which commands and file extensions agents may access.

```toml
[commands]
allowed = ["ls", "cat", "grep", "python3", "pip", "node", "npm", "git status", "git log", "git diff"]
blocked = ["rm -rf", "sudo", "curl", "wget", "nc", "chmod 777", "mkfs", "dd", "shutdown", "reboot"]

[files]
allowed_extensions = [".py", ".js", ".ts", ".json", ".toml", ".yaml", ".md", ".txt", ".env.example"]
blocked_extensions = [".env", ".pem", ".key", ".cert"]
```

Unknown commands default to **BLOCKED**.

### `.kennelbox/sandbox.toml`

Firejail profile options.

```toml
[firejail]
network = false          # block outbound network
read_only_home = true    # no writes outside CWD
restrict_above_cwd = true
seccomp = true           # drop dangerous syscalls
```

---

## MCP Bridge

kennelbox exposes a stdio JSON-RPC 2.0 server (MCP-compatible). When you run `kennelbox run`, the server starts on stdin/stdout and your agent connects to it.

### Available MCP Tools

| Tool | Description |
|---|---|
| `read_file` | Read a file relative to the project root |
| `write_file` | Write a file relative to the project root |
| `list_directory` | List directory contents |
| `run_command` | Run an allowlisted shell command |

All calls are validated against the allowlist before execution. Path traversal above the project root is always blocked.

---

## Connecting OpenClaw

Configure OpenClaw to use kennelbox as its MCP server:

```json
{
  "mcpServers": {
    "kennelbox": {
      "command": "kennelbox",
      "args": ["run", "--agent", "openclaw"],
      "transport": "stdio"
    }
  }
}
```

---

## Connecting Hermes Agent

Add to your Hermes Agent config:

```json
{
  "mcp": {
    "transport": "stdio",
    "command": "kennelbox run --agent hermes"
  }
}
```

---

## Project Structure

```
kennelbox/
├── kennelbox.py          # CLI entrypoint (typer)
├── config/
│   ├── allowlist.toml    # default permitted commands (copied to .kennelbox/ on init)
│   └── sandbox.toml      # default firejail profile options
├── sandbox/
│   ├── jail.py           # firejail wrapper logic
│   └── venv_mgr.py       # auto-venv setup (Python / Node / generic)
├── agent_bridge/
│   └── server.py         # MCP stdio JSON-RPC 2.0 server
├── pyproject.toml
└── README.md
```

---

## Security Model

1. **Filesystem** — firejail whitelists only `$CWD`. `/home`, `/etc`, `/var`, `/root`, `/tmp`, `/proc`, `/sys` are all blacklisted.
2. **Network** — disabled by default (`net=none` in firejail). Set `network = true` in `sandbox.toml` to enable.
3. **Commands** — only commands in `allowed` run; any command matching a `blocked` pattern is rejected before execution.
4. **Files** — only `allowed_extensions` may be read or written; `blocked_extensions` are always rejected.
5. **Path escape** — every file path is resolved and checked against the project root before any I/O.
6. **Syscalls** — seccomp drops `mount`, `ptrace`, `reboot`, `kexec_load`, and other dangerous syscalls.

kennelbox does **not** require root. firejail handles privilege separation at the kernel level via user namespaces.

---

## License

MIT
