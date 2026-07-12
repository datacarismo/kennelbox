# kennelbox — Security Prioritization

A ranked list of work needed to make kennelbox actually contain an untrusted agent
(OpenClaw / Hermes) on a daily-driver machine. Ordered by risk-to-effort: fix the top
items before this is safe to point at a real home directory.

Legend — **Severity**: how bad if exploited. **Effort**: rough implementation cost.

> **STATUS (2026-07-11): all items #1–#11 are RESOLVED.**
> - #1: trust model decided — firejail is the only real boundary; allowlist is policy/UX. Additionally, inline code-execution flags (`-c`, `-e`, `--eval`, `--exec`, `-x`, `--command`) are blocked via `blocked_args`.
> - #2: firejail mandatory — kennelbox refuses to start without it.
> - #3: all file tools route through `run_sandboxed_file_op` inside firejail.
> - #4: `_safe_path` uses `Path.relative_to` after `resolve()`.
> - #5: writes to `.kennelbox/` denied unconditionally.
> - #6: blocklist renamed `warn_patterns`, advisory-only.
> - #7: dotfiles + sensitive filenames denied by name; suffix check only for the allowlist.
> - #8: whitelist-first firejail profile, `--private-tmp`/`--private-dev`, credential-dir blacklists, default `--seccomp`.
> - #9: `[limits]` caps for read/write sizes and command output.
> - #10: notifications (no-`id` requests) get no response, per JSON-RPC 2.0.
> - #11: installer prefers pipx; README security model rewritten to match reality.

---

## P0 — Containment is currently bypassable (do these first)

### 1. Bare interpreters make the command allowlist meaningless ✅ RESOLVED
- **Resolution (2026-07-11, commits `7d4661a`, `2aac07f`):** Both fix options adopted.
  Trust model: firejail is the only real boundary, allowlist is policy/UX. Plus
  `check_command()` now parses with `shlex.split()` and rejects `blocked_args` tokens
  (`-c`, `-e`, `--eval`, `--exec`, `-x`, `--command`) so `python3 -c` / `node -e` are
  denied even though the interpreters are allowlisted.
- **Severity:** Critical · **Effort:** Medium
- `python3`, `pip`, `node`, `npm` are all allowlisted. Each is a full arbitrary-code +
  arbitrary-filesystem primitive: `python3 -c "..."` can read `~/.ssh`, delete files, or
  open sockets without ever matching a blocked pattern. `pip install` / `npm install` run
  arbitrary `setup.py` / lifecycle scripts.
- **Fix (pick one):**
  - Treat the OS sandbox (firejail/bwrap/container) as the *only* real boundary and harden
    it accordingly — demote the command allowlist to UX guidance, not a security control; **or**
  - Drop bare interpreters from the default allowlist and expose them only through a
    constrained wrapper (no `-c`, no arbitrary scripts, pinned index for pip, `--ignore-scripts`
    for npm).

### 2. Isolation is optional and silently degrades to nothing ✅ RESOLVED
- **Resolution (commit `7d4661a`):** `_require_firejail()` raises `RuntimeError` when
  firejail is absent; `kennelbox run` exits with a clear install hint. No silent fallback.
- **Severity:** Critical · **Effort:** Medium
- When firejail is absent, the "fallback" is only `cwd=` + `HOME=cwd`. That does **not**
  restrict filesystem access — a `python3 -c` reads/writes anywhere the user can. README's
  "CWD-only restriction enforced in software" oversells this; there is no such enforcement.
- **Fix:** Make isolation mandatory. If no sandbox backend (firejail / bubblewrap / landlock /
  container) is available, **refuse to start** rather than degrade. Print a clear install hint.

### 3. read_file / write_file / list_directory run un-sandboxed in the host process ✅ RESOLVED
- **Resolution (commit `7d4661a`):** All three tools route through
  `run_sandboxed_file_op()` in `sandbox/jail.py` — the actual I/O executes as a
  Python one-liner inside firejail. `_safe_path` still runs first in the host as a
  fast policy check.
- **Severity:** Critical · **Effort:** Medium
- These MCP tools execute directly inside the kennelbox process, not inside firejail, so they
  rely solely on `_safe_path`. Combined with #4 below, they can read/write outside the project
  even when firejail is installed.
- **Fix:** Route file tools through the same sandbox boundary as `run_command`, or enforce a
  vetted, symlink-safe path check (see #4) — ideally both.

### 4. Path-escape via string-prefix check in `_safe_path` ✅ RESOLVED
- **Resolution (commit `7a8c281`):** `_safe_path` now resolves both paths and uses
  `target.relative_to(cwd_resolved)` (catching `ValueError`). Sibling-prefix escape
  (`../myproj-secrets`) verified blocked by repro script.
- **Severity:** High · **Effort:** Low
- `str(target).startswith(str(cwd_resolved))` lets sibling paths through. Confirmed: with root
  `/home/user/myproj`, both `../myproj-secrets/creds` and `../myproj2/x` resolve outside the
  project yet pass, because they share the string prefix.
- **Fix:** Use `target.relative_to(cwd_resolved)` (catch `ValueError`) or
  `os.path.commonpath([target, cwd_resolved]) == str(cwd_resolved)`. Also resolve symlinks and
  re-check after resolution.

---

## P1 — Guardrails that can be disarmed or don't do what they claim

### 5. Agent can rewrite its own guardrails ✅ RESOLVED
- **Resolution (commit `7a8c281`):** `tool_write_file` denies any target inside
  `.kennelbox/` unconditionally (path-based via `relative_to`, before the sandbox call).
- **Severity:** High · **Effort:** Low
- `.toml` is an allowed extension and `.kennelbox/` lives inside the project root, so
  `write_file(".kennelbox/allowlist.toml", …)` or `sandbox.toml` passes the guard. Not
  exploitable in the same session (configs load once) but silently disarms the next one
  (`seccomp=false`, `network=true`, allow-all).
- **Fix:** Deny all writes to `.kennelbox/` unconditionally (path-based, not extension-based).
  Consider loading config from outside the project root entirely.

### 6. Blocklist is substring-based and gives false confidence ✅ RESOLVED
- **Resolution (commit `7d4661a`):** Renamed `blocked` → `warn_patterns` in
  `allowlist.toml`. Matches are logged to stderr as advisory warnings, never used to
  block. Documented as non-authoritative; the allowlist is the only policy control.
- **Severity:** Medium · **Effort:** Low
- `if blocked in command` is trivially bypassed (`rm  -rf`, `shutil.rmtree`, base64), and
  commands run without a shell so shell-oriented patterns are half-irrelevant. With a
  default-deny allowlist already in place, the blocklist mostly adds misplaced trust.
- **Fix:** Drop the blocklist as a security mechanism, or keep it only as a "obviously wrong"
  advisory layer and document it as non-authoritative.

### 7. Extension matching uses `.suffix` and misbehaves ✅ RESOLVED
- **Resolution (commit `7d4661a`):** `check_file()` now denies exact sensitive
  filenames (`.env`, `.pem`, `.key`, `.cert`, `.pfx`, `.p12`) and all dotfiles by name,
  then applies the suffix allowlist to regular files. The dead `.env.example` allow
  entry was removed from config.
- **Severity:** Medium · **Effort:** Low
- Confirmed: `Path(".env.example").suffix == ".example"` (so the `.env.example` allow entry is
  dead), and `Path(".env").suffix == ""` (so `.env` is blocked only by accident, not by the
  blocklist). Any `secret.env.example` also slips to `.example`.
- **Fix:** Match on filename patterns/globs, not `.suffix`; explicitly deny dotfiles like `.env`,
  `.pem`, `.key` by name.

---

## P2 — Harden the sandbox profile

### 8. firejail profile is blacklist-first and leaves gaps ✅ RESOLVED
- **Resolution (commit `8473721`):** Dropped the conflicting `--blacklist=/tmp` and the
  redundant blanket blacklists; whitelist-first with `--private-cwd`, `--private-tmp`,
  `--private-dev`; targeted blacklists for credential dirs (`~/.ssh`, `~/.gnupg`,
  `~/.aws`, `~/.config/gcloud`, etc.); switched to firejail's default `--seccomp`.
  Also fixed invalid `--chdir` flag → `--private-cwd`. 6/6 integration tests pass.
- **Severity:** Medium · **Effort:** Medium
- `--whitelist=$CWD` plus a handful of `--blacklist`s still leaves `/opt`, `/srv`, `/mnt`,
  `/boot`, other users' homes, etc. readable. `--blacklist=/tmp` conflicts with `--private-tmp`.
  A custom `--seccomp.drop` list is generally weaker than firejail's default `--seccomp`.
- **Fix:** Move to a whitelist-first profile (`--private`, explicit binds) or a dedicated
  `.profile`; prefer default `--seccomp`; resolve the `/tmp` conflict.

### 9. No resource bounds on file tools ✅ RESOLVED
- **Resolution (commit `90fa638`):** New `[limits]` section in `allowlist.toml` —
  `max_read_bytes` / `max_write_bytes` (10 MB defaults) enforced before the sandbox
  call; `max_output_bytes` (1 MB) truncates command stdout/stderr with a
  `truncated: true` flag. Timeout retained.
- **Severity:** Low · **Effort:** Low
- `read_file` slurps whole files into memory; `write_file` has no size cap; 30s is the only
  command bound. A hostile or buggy agent can OOM or fill the disk.
- **Fix:** Cap read/write sizes, add a total-output limit for commands, keep the timeout.

---

## P3 — Correctness / polish (not security-blocking)

### 10. JSON-RPC notification handling ✅ RESOLVED
- **Resolution (commit `90fa638`):** `run_server()` detects requests without an `id`,
  logs them to stderr, and sends no response — per JSON-RPC 2.0. Verified with
  `notifications/initialized` followed by two real requests: exactly two responses.

### 11. Installer & docs ✅ RESOLVED
- **Resolution (commit `90fa638`):** `install.sh` prefers `pipx install --editable`
  (avoids PEP 668 externally-managed-environment errors) with pip fallback, and treats
  firejail as required. README security model rewritten: firejail is the mandatory
  boundary, all tool I/O is sandboxed, "software CWD restriction" claim removed,
  config examples updated to current format.

---

## Suggested order of attack (historical — all complete)
1. **#4** (path check) and **#5** (protect `.kennelbox/`) — small, high-value, land today. ✅
2. **#2 + #3** — make the sandbox mandatory and route file tools through it. ✅
3. **#1** — decide the interpreter policy; this defines the whole trust model. ✅
4. **#7**, **#6**, then P2/P3 cleanup. ✅

## What's next (beyond this list)
- **HTTP/SSE remote MCP transport** (`--listen HOST:PORT`) so a remote agent (e.g. VPS
  Hermes) can connect over Tailscale — see `kennelbox-recommendations.md`.
- Publish to PyPI (`pip install kennelbox`).
