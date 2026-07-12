# Kennelbox — Codebase Analysis & Next Steps

Based on the extracted zip contents as of July 4, 2026.

---

## What Already Exists

| File | Lines | Purpose |
|---|---|---|
| `kennelbox.py` | 292 | Typer CLI: `init`, `run`, `rules`, `status`, `release` |
| `agent_bridge/server.py` | 284 | MCP stdio JSON-RPC 2.0 server |
| `sandbox/jail.py` | 147 | Firejail wrapper (seccomp, whitelist, network block) |
| `sandbox/venv_mgr.py` | 89 | Auto-detect Python/Node/generic, create venv |
| `config/allowlist.toml` | 41 | Command allowlist + advisory warn_patterns |
| `config/sandbox.toml` | 5 | Firejail profile options |
| `install.sh` | ~180 | One-line installer |
| `PRIORITIES.md` | 117 | Security gap analysis (already accurate) |

## MCP Tools Exposed

| Tool | Description | Sandboxed? |
|---|---|---|
| `read_file` | Read a file within the project | Yes (firejail) |
| `write_file` | Write content to a file (blocks `.kennelbox/`) | Yes (firejail) |
| `list_directory` | List directory contents | Yes (firejail) |
| `run_command` | Run an allowlisted shell command | Yes (firejail) |

Things already done right: path escape uses `relative_to` not `startswith`,
`.kennelbox/` writes are blocked, firejail is mandatory (will refuse to start
without it), `warn_patterns` are advisory only, extension matching denies
dotfiles unconditionally.

---

## The Big Gap: Remote Transport

The codebase is a **local-only** sandbox. stdio MCP means the agent and
kennelbox must share a filesystem. For your VPS Hermes to control a daily
driver, you need network transport.

### Option A: HTTP/SSE MCP Transport (Recommended)

Add a `--listen` flag so kennelbox can serve over HTTP/Server-Sent-Events
on a Tailscale IP. This is the standard MCP HTTP transport — compatible with
Hermes, Claude Code, Codex, and any MCP client.

**What changes:**
- Add a `--listen HOST:PORT` / `--host` flag to `kennelbox run`
- Add FastAPI or a lightweight async HTTP handler to `agent_bridge/server.py`
- Dispatch incoming JSON-RPC to the same `dispatch()` function
- Standard MCP SSE protocol: POST `/mcp` for requests, SSE stream for responses
- Ship with `uvicorn` or `hypercorn` as an optional extra dep

**On the daily driver:**
```bash
kennelbox run --agent hermes --listen 100.x.x.x:9090
```

**On the VPS (Hermes):**
```bash
hermes mcp add kennelbox --url http://100.x.x.x:9090
```

**Good because:** Hermes already supports `--url` MCP servers. Zero SSH keys
needed — Tailscale handles auth. Tools appear as first-class `read_file`,
`write_file` etc. in the tool schema. Also works with Claude Code, Codex,
OpenCode, Cursor, any MCP client.

**Trade-off:** Requires FastAPI/uvicorn dep. Slightly more code than SSH tunnel.

---

### Option B: SSH Tunnel (Zero Code Change)

Wrap the existing stdio server in an SSH tunnel. No code changes to kennelbox.

**On the daily driver:**
```bash
# Nothing to start — just have SSH running
```

**On the VPS (Hermes):**
```bash
hermes mcp add kennelbox \
  --command "ssh 100.x.x.x 'cd /home/user/project && kennelbox run --agent hermes'"
```

**Good because:** Zero code changes. Uses existing SSH key infrastructure.
Works immediately with Hermes MCP's `--command` transport.

**Trade-off:** SSH key management. Each command starts a new kennelbox session
(auth overhead per tool call). No persistent connection. Slower than HTTP.

---

### Option C: Reverse Tunnel (No Inbound Ports on Daily Driver)

The daily driver opens an outbound connection to the VPS. The VPS doesn't need
to reach the daily driver at all.

**On the VPS:**
```bash
# TCP relay that kennelbox connects to
nc -lk 100.x.x.x 9090
```

**On the daily driver:**
```bash
# Pipe kennelbox stdio through the TCP relay
kennelbox run --agent hermes | nc 100.x.x.x 9090
```

Or use `ssh -R`:
```bash
# Daily driver exposes its kennelbox stdio to the VPS
ssh -R 9090:localhost:9090 user@100.x.x.x \
  'kennelbox run --agent hermes | nc localhost 9090'
```

**Good because:** No inbound ports needed on the daily driver at all.
Maximum security — the daily driver is fully behind Tailscale/NAT.

**Trade-off:** Requires a relay or reverse SSH setup. More complex to debug.
Not as clean as a proper HTTP server.

---

## Security Roadmap (from PRIORITIES.md)

Your PRIORITIES.md already has a good order. Re-stated here for reference:

### P0 — Ship-blocking (do before any remote transport goes live)

| # | Issue | Status |
|---|---|---|
| 1 | Bare interpreters (`python3 -c`, `pip install`, `npm`) bypass allowlist | Not yet addressed |
| 2 | File tools already route through firejail (`run_sandboxed_file_op`) | **Done** |
| 3 | Path escape via `relative_to` | **Done** |
| 4 | Make sandbox mandatory (firejail required) | **Done** in jail.py |

### P1 — Strong guardrails

| # | Issue | Status |
|---|---|---|
| 5 | `.kennelbox/` write protection | **Done** in server.py |
| 6 | Blocklist is substring-based, now advisory only | **Done** (renamed to `warn_patterns`) |
| 7 | Extension matching via `.suffix` is buggy | Still open |

### P2 — Harden sandbox profile

| # | Issue | Status |
|---|---|---|
| 8 | firejail profile is blacklist-first (whitelist would be better) | Still open |
| 9 | No resource bounds on file tools (no size caps) | Still open |

### P3 — Polish

| # | Issue | Status |
|---|---|---|
| 10 | JSON-RPC notification handling (no-`id` requests) | Still open |
| 11 | Installer should use pipx or dedicated venv | Still open |

---

## Suggested Implementation Order

### Phase 1: Remote Transport (HTTP/SSE)

```
Duration: ~1-2 hours
Files changed: agent_bridge/server.py, kennelbox.py, pyproject.toml
```

Add a `--listen HOST:PORT` flag. When set, start an HTTP server instead of
the stdio loop. Reuse the same `dispatch()` function and `AllowlistGuard`.
The HTTP server speaks standard MCP SSE transport:

- `GET /sse` — SSE stream endpoint (server sends events)
- `POST /messages` — client sends JSON-RPC requests

This makes kennelbox a first-class MCP remote server. Hermes, Claude Code,
Codex, and any MCP client can connect to it over Tailscale.

### Phase 2: Resolve P0 Security Issues

```
Duration: ~2-3 hours
```

1. **Interpreter policy** — decide: treat firejail as the real boundary and
   keep interpreters in the allowlist (simpler), or restrict them by only
   allowing `python3 script.py` (no `-c`) and `pip install --no-scripts`
   (tighter)
2. **Fix extension matching** — switch from `Path.suffix` to filename globs
   so `.env.example` matches correctly

### Phase 3: Remote Agent Discovery

```
Duration: ~1 hour
```

Add a `--discover` flag or a well-known endpoint (`GET /` or MCP `ping`) so
the agent can verify kennelbox is alive before starting a session.

### Phase 4: Polish & Publish

```
Duration: ~2 hours
```

- Add read/write size caps (e.g. 10MB read, 50MB write)
- Fix JSON-RPC notification handling per spec
- Update README security model to reflect the remote transport
- Publish to PyPI: `pip install kennelbox`

---

## Architecture Diagram (Target State)

```
                            Tailscale (100.x.x.x)
                                  │
┌─────────────────┐     HTTP/MCP      ┌──────────────────────────────┐
│ VPS Hermes       │◄────────────────►│ Daily Driver (kennelbox)     │
│                  │                  │                              │
│ read_file  ──────┼───── POST ──────►│  AllowlistGuard ──► firejail│
│ write_file ──────┼───── POST ──────►│  │                           │
│ run_command ─────┼───── POST ──────►│  ▼                           │
│                  │                  │  sandboxed subprocess        │
│                  │                  │  (seccomp, net=none, CWD)   │
└─────────────────┘                  └──────────────────────────────┘
```

The daily driver never opens a public port. The HTTP server binds to
`100.x.x.x` (Tailscale IP) — only devices on your Tailscale mesh can reach
it. The VPS Hermes connects as an MCP client over that secure tunnel.

---

## Running Total (files to change)

| File | What to add |
|---|---|
| `kennelbox.py` | `--listen` flag on `run` command |
| `agent_bridge/server.py` | HTTP server mode (async, SSE transport) |
| `pyproject.toml` | Optional dep: `fastapi`, `uvicorn` (or `sse-starlette`, `hypercorn`) |
| `config/allowlist.toml` | Maybe adjust interpreter policy |
| `README.md` | Document remote transport, update security model |
| `PRIORITIES.md` | Mark completed items, add remote transport items |