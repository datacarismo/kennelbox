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
- **Firejail sandbox (required)** — every tool call (commands *and* file I/O) runs inside firejail with filesystem whitelisting, network blocking, and seccomp filtering
- **Command allowlist** — explicit per-project command rules; unknown = blocked. Inline code-execution flags (`-c`, `-e`, `--eval`, …) are blocked even for allowed interpreters
- **Resource caps** — configurable limits on file read/write sizes and command output
- **MCP bridge** — stdio JSON-RPC 2.0 server compatible with any MCP-capable agent

---

## Requirements

- Python 3.10+
- Linux (Ubuntu / Zorin OS or compatible)
- `firejail` — **required**; kennelbox refuses to start without it

### Install firejail

```bash
sudo apt install firejail
```

> firejail provides the kernel-level isolation boundary. There is no software-only fallback — without firejail, `kennelbox run` exits with an error.

---

## Installation

### One-line installer (recommended)

```bash
git clone https://github.com/datacarismo/kennelbox.git
cd kennelbox
bash install.sh
```

The installer checks your Python version, installs firejail via `apt` (required), installs the package via `pipx` (preferred) or `pip` (fallback), and verifies `kennelbox` is on your PATH.

```
Options:
  --yes           accept all prompts non-interactively
  --no-firejail   skip the firejail apt step (kennelbox will not run until it's installed)
```

### Manual install

```bash
git clone https://github.com/datacarismo/kennelbox.git
cd kennelbox
pipx install --editable .    # or: pip install -e .
```

> `pipx` is recommended — it installs into an isolated venv and avoids PEP 668 "externally managed environment" errors on modern Debian/Ubuntu.

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
# Flags enabling inline code execution — blocked regardless of base command
blocked_args = ["-c", "-e", "--eval", "--exec", "-x", "--command"]
# Advisory only: matches are logged as warnings but NOT blocked
warn_patterns = ["rm -rf", "sudo", "curl", "wget", "nc", "chmod 777", "mkfs", "dd", "shutdown", "reboot"]

[limits]
max_read_bytes = 10485760    # 10 MB
max_write_bytes = 10485760   # 10 MB
max_output_bytes = 1048576   # 1 MB — command output truncated beyond this

[files]
allowed_extensions = [".py", ".js", ".ts", ".json", ".toml", ".yaml", ".md", ".txt"]
```

Unknown commands default to **BLOCKED**. Dotfiles and sensitive filenames (`.env`, `.pem`, `.key`, etc.) are always denied regardless of the extension allowlist.

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

kennelbox exposes a JSON-RPC 2.0 server (MCP-compatible) over two transports:

**stdio (default)** — the server starts on stdin/stdout and a local agent connects to it.

**HTTP (`--http`)** — for remote agents (e.g. an agent on a VPS reaching your machine over Tailscale):

```bash
kennelbox run --agent hermes --http --host 100.x.x.x --port 7333
```

- A bearer token is **required** — auto-generated and printed if you don't pass `--token`
- Binds `127.0.0.1` by default; pass `--host` (e.g. your Tailscale IP) to accept remote connections
- Optional `--allowed-ip` (repeatable) restricts clients by source IP on top of the token
- `GET /health` for liveness; `POST /` for JSON-RPC
- Every HTTP request goes through the same allowlist + firejail pipeline as stdio

### Available MCP Tools

| Tool | Description |
|---|---|
| `read_file` | Read a file relative to the project root |
| `write_file` | Write a file relative to the project root |
| `edit_file` | Replace an exact, unique string in a file (fails on zero or multiple matches) |
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

**The firejail sandbox is the security boundary.** The allowlist is policy/UX guidance layered on top — it shapes what a well-behaved agent can request, but kernel-level isolation is what actually contains a misbehaving one. firejail is therefore required; kennelbox refuses to start without it.

1. **Filesystem** — firejail whitelists only `$CWD` within the home tree. Credential directories (`~/.ssh`, `~/.gnupg`, `~/.aws`, `~/.config/gcloud`, etc.) are explicitly blacklisted. `/tmp` and `/dev` are replaced with private views (`--private-tmp`, `--private-dev`).
2. **All tool I/O is sandboxed** — not just `run_command`: `read_file`, `write_file`, and `list_directory` also execute inside firejail.
3. **Network** — disabled by default (`net=none`). Set `network = true` in `sandbox.toml` to enable.
   > **Note:** this applies to the firejail subprocesses. The kennelbox process itself and the agent connecting over stdio are outside the sandbox — LLM API calls (to Anthropic, OpenAI, etc.) are unaffected.
4. **Commands** — only commands in `allowed` run. Argument flags enabling inline code execution (`-c`, `-e`, `--eval`, `--exec`, `-x`, `--command`) are rejected even for allowed interpreters. `warn_patterns` are advisory only — logged, never enforced.
5. **Files** — extension allowlist plus unconditional denial of dotfiles and sensitive filenames (`.env`, `.pem`, `.key`, `.cert`, `.pfx`, `.p12`). Writes to `.kennelbox/` (the config directory) are always blocked.
6. **Path escape** — every path is resolved and containment-checked against the project root (`Path.relative_to`, not string prefix) before any I/O.
7. **Resource caps** — file reads/writes and command output are size-limited (configurable in `[limits]`).
8. **Syscalls** — firejail's default `--seccomp` filter (maintained upstream, more complete than any hand-rolled list).

kennelbox does **not** require root. firejail handles privilege separation at the kernel level via user namespaces.

---

## License

MIT — see [LICENSE](LICENSE)
