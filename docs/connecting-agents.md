# Connecting Agents to kennelbox

Instructions for hooking an AI agent up to a kennelbox sandbox. Covers the two
supported setups:

- **OpenClaw** — runs on the same machine as kennelbox, connects over **stdio**
- **Hermes** — runs on a remote host (e.g. a VPS), connects over **HTTP** (ideally
  through Tailscale)

kennelbox is not an agent. It is an enforcement layer: an MCP server whose five
tools all execute inside a firejail sandbox scoped to one project directory.
The agent plans; kennelbox decides what actually runs.

---

## Prerequisites (kennelbox host)

```bash
sudo apt install firejail        # mandatory — kennelbox refuses to start without it
pipx install kennelbox           # or: pip install kennelbox

cd /path/to/project
kennelbox init                   # creates .kennelbox/ with allowlist.toml + sandbox.toml
```

Review `.kennelbox/allowlist.toml` before connecting an agent — it defines which
commands, flags, and file extensions the agent may use in this project.

---

## Option A — OpenClaw (local, stdio)

Register kennelbox as an MCP server in OpenClaw's configuration. The exact file
depends on your OpenClaw install, but the shape is the standard MCP stdio entry:

```json
{
  "mcpServers": {
    "kennelbox": {
      "command": "kennelbox",
      "args": ["run", "--agent", "openclaw", "--cwd", "/path/to/project"]
    }
  }
}
```

Notes:

- `--cwd` pins the sandbox to the project directory regardless of where the
  agent process launches from. The directory must already be `kennelbox init`-ed.
- The MCP protocol flows over stdin/stdout; kennelbox writes all human-readable
  status output to stderr, so it will not corrupt the JSON-RPC stream.
- To run it manually (e.g. to watch the logs while testing):

  ```bash
  kennelbox run --agent openclaw --cwd /path/to/project
  ```

---

## Option B — Hermes (remote, HTTP over Tailscale)

### 1. Start the server on the kennelbox host

```bash
kennelbox run --agent hermes --http \
  --host <tailscale-ip-of-this-machine> \
  --port 7333 \
  --cwd /path/to/project
```

- If you omit `--token`, kennelbox generates one and prints it at startup. Pass
  your own with `--token <secret>` if you want a stable value.
- `--host` defaults to `127.0.0.1` (local only). Binding to your Tailscale IP
  makes the server reachable from the tailnet and nothing else. Avoid `0.0.0.0`.
- Optionally restrict clients to the VPS's Tailscale IP:

  ```bash
  --allowed-ip <tailscale-ip-of-vps>
  ```

### 2. Smoke-test from the remote host (before involving the agent)

```bash
# Reachability — no auth required:
curl http://<tailscale-ip>:7333/health
# → {"status": "ok", "version": "0.1.1"}

# Auth + JSON-RPC end to end:
curl -X POST http://<tailscale-ip>:7333 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
# → JSON listing the five tools
```

If both succeed but the agent's MCP client fails to connect, the client likely
requires MCP Streamable HTTP / SSE, which kennelbox does not implement (yet) —
see [Protocol notes](#protocol-notes).

### 3. Point Hermes at the server

Configure Hermes's MCP/tool settings with:

| Setting | Value |
|---------|-------|
| URL | `http://<tailscale-ip>:7333` |
| Auth header | `Authorization: Bearer <token>` |
| Content type | `application/json` |

Every request is a JSON-RPC 2.0 message POSTed to `/`. Responses come back in
the POST body. Notifications (messages without an `"id"`) get `202 Accepted`
with an empty body.

---

## Protocol notes

The HTTP transport is **plain JSON-RPC 2.0 over POST** — one request per POST,
one response per body. It is *not* the MCP Streamable HTTP spec: there is no
SSE stream, no `Mcp-Session-Id`, and no server-initiated messages.

Supported methods:

| Method | Purpose |
|--------|---------|
| `initialize` | MCP handshake; returns `serverInfo` and capabilities |
| `tools/list` | Returns the five tool definitions with input schemas |
| `tools/call` | Executes a tool (`params.name` + `params.arguments`) |

Endpoints (HTTP mode):

| Endpoint | Auth | Purpose |
|----------|------|---------|
| `POST /` | Bearer token required | JSON-RPC requests |
| `GET /health` | none | Liveness check: `{"status": "ok", "version": ...}` |

---

## Tool catalog

All five tools operate on paths **relative to the project root** and execute
inside firejail. Absolute paths and `..` escapes are rejected.

| Tool | Arguments | Behavior |
|------|-----------|----------|
| `read_file` | `path` | Returns file contents. Refuses files over the `max_read_bytes` cap (default 10 MB). |
| `write_file` | `path`, `content` | Writes a file. Refuses content over `max_write_bytes` (default 10 MB). |
| `edit_file` | `path`, `old_string`, `new_string` | Replaces one exact occurrence. **Fails if `old_string` matches zero or multiple times** — include enough surrounding context to make it unique. Cheaper than rewriting the whole file with `write_file`. |
| `list_directory` | `path` (optional, default `.`) | Lists entries at the path. |
| `run_command` | `command`, `timeout` (optional, default 30 s) | Runs an allowlisted shell command in the sandbox. Output truncated at `max_output_bytes` (default 1 MB). |

Example `tools/call`:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": {
    "name": "edit_file",
    "arguments": {
      "path": "src/app.py",
      "old_string": "DEBUG = True",
      "new_string": "DEBUG = False"
    }
  }
}
```

---

## Rules the agent must work within

These are enforced server-side; the agent cannot opt out. Knowing them up front
avoids wasted tool calls.

**Commands** (`run_command`):
- Only allowlisted commands run. Defaults: `ls`, `cat`, `grep`, `python3`,
  `pip`, `node`, `npm`, `git status`, `git log`, `git diff`. Matching is by
  prefix (`python3 script.py` passes; `bash anything` does not).
- Inline code-execution flags are blocked for *every* command: `-c`, `-e`,
  `--eval`, `--exec`, `-x`, `--command`. Use `python3 script.py`, never
  `python3 -c '...'`. Write the code to a file first, then run the file.
- Commands run with **no network** by default (`--net=none`), in a private
  `/tmp` and `/dev`, with credential directories (`~/.ssh`, `~/.aws`,
  `~/.gnupg`, …) blacklisted and seccomp active.

**Files:**
- Only allowlisted extensions are writable. Defaults: `.py`, `.js`, `.ts`,
  `.json`, `.toml`, `.yaml`, `.md`, `.txt`.
- Dotfiles and sensitive filenames (`.env`, `.pem`, `.key`, `.cert`, `.pfx`,
  `.p12`) are always denied, read and write.
- The `.kennelbox/` directory is never writable — the agent cannot edit its
  own policy.

To change these limits, edit `.kennelbox/allowlist.toml` on the kennelbox host
and restart the session. Run `kennelbox rules` to see the active policy.

---

## Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `401 Unauthorized` | Missing or wrong bearer token. Header must be exactly `Authorization: Bearer <token>`. |
| `403 Forbidden: IP not allowed` | Server started with `--allowed-ip` and the client IP isn't listed. Check the VPS's Tailscale IP with `tailscale ip -4`. |
| `curl /health` times out | Server bound to `127.0.0.1` (default) instead of the Tailscale IP, or Tailscale is down on either end. |
| `Command not in allowlist` | Add the command to `allowed` in `.kennelbox/allowlist.toml` and restart. |
| `Argument '-c' is not permitted` | Inline code execution is blocked by design. Write a script file and run it. |
| `edit_file` "matches 0/multiple times" | `old_string` must appear exactly once. Add surrounding lines for uniqueness, or `read_file` first to confirm current contents. |
| kennelbox refuses to start | firejail is not installed. `sudo apt install firejail`. |
