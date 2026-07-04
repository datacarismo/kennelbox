# kennelbox — Security Prioritization

A ranked list of work needed to make kennelbox actually contain an untrusted agent
(OpenClaw / Hermes) on a daily-driver machine. Ordered by risk-to-effort: fix the top
items before this is safe to point at a real home directory.

Legend — **Severity**: how bad if exploited. **Effort**: rough implementation cost.

---

## P0 — Containment is currently bypassable (do these first)

### 1. Bare interpreters make the command allowlist meaningless
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

### 2. Isolation is optional and silently degrades to nothing
- **Severity:** Critical · **Effort:** Medium
- When firejail is absent, the "fallback" is only `cwd=` + `HOME=cwd`. That does **not**
  restrict filesystem access — a `python3 -c` reads/writes anywhere the user can. README's
  "CWD-only restriction enforced in software" oversells this; there is no such enforcement.
- **Fix:** Make isolation mandatory. If no sandbox backend (firejail / bubblewrap / landlock /
  container) is available, **refuse to start** rather than degrade. Print a clear install hint.

### 3. read_file / write_file / list_directory run un-sandboxed in the host process
- **Severity:** Critical · **Effort:** Medium
- These MCP tools execute directly inside the kennelbox process, not inside firejail, so they
  rely solely on `_safe_path`. Combined with #4 below, they can read/write outside the project
  even when firejail is installed.
- **Fix:** Route file tools through the same sandbox boundary as `run_command`, or enforce a
  vetted, symlink-safe path check (see #4) — ideally both.

### 4. Path-escape via string-prefix check in `_safe_path`
- **Severity:** High · **Effort:** Low
- `str(target).startswith(str(cwd_resolved))` lets sibling paths through. Confirmed: with root
  `/home/user/myproj`, both `../myproj-secrets/creds` and `../myproj2/x` resolve outside the
  project yet pass, because they share the string prefix.
- **Fix:** Use `target.relative_to(cwd_resolved)` (catch `ValueError`) or
  `os.path.commonpath([target, cwd_resolved]) == str(cwd_resolved)`. Also resolve symlinks and
  re-check after resolution.

---

## P1 — Guardrails that can be disarmed or don't do what they claim

### 5. Agent can rewrite its own guardrails
- **Severity:** High · **Effort:** Low
- `.toml` is an allowed extension and `.kennelbox/` lives inside the project root, so
  `write_file(".kennelbox/allowlist.toml", …)` or `sandbox.toml` passes the guard. Not
  exploitable in the same session (configs load once) but silently disarms the next one
  (`seccomp=false`, `network=true`, allow-all).
- **Fix:** Deny all writes to `.kennelbox/` unconditionally (path-based, not extension-based).
  Consider loading config from outside the project root entirely.

### 6. Blocklist is substring-based and gives false confidence
- **Severity:** Medium · **Effort:** Low
- `if blocked in command` is trivially bypassed (`rm  -rf`, `shutil.rmtree`, base64), and
  commands run without a shell so shell-oriented patterns are half-irrelevant. With a
  default-deny allowlist already in place, the blocklist mostly adds misplaced trust.
- **Fix:** Drop the blocklist as a security mechanism, or keep it only as a "obviously wrong"
  advisory layer and document it as non-authoritative.

### 7. Extension matching uses `.suffix` and misbehaves
- **Severity:** Medium · **Effort:** Low
- Confirmed: `Path(".env.example").suffix == ".example"` (so the `.env.example` allow entry is
  dead), and `Path(".env").suffix == ""` (so `.env` is blocked only by accident, not by the
  blocklist). Any `secret.env.example` also slips to `.example`.
- **Fix:** Match on filename patterns/globs, not `.suffix`; explicitly deny dotfiles like `.env`,
  `.pem`, `.key` by name.

---

## P2 — Harden the sandbox profile

### 8. firejail profile is blacklist-first and leaves gaps
- **Severity:** Medium · **Effort:** Medium
- `--whitelist=$CWD` plus a handful of `--blacklist`s still leaves `/opt`, `/srv`, `/mnt`,
  `/boot`, other users' homes, etc. readable. `--blacklist=/tmp` conflicts with `--private-tmp`.
  A custom `--seccomp.drop` list is generally weaker than firejail's default `--seccomp`.
- **Fix:** Move to a whitelist-first profile (`--private`, explicit binds) or a dedicated
  `.profile`; prefer default `--seccomp`; resolve the `/tmp` conflict.

### 9. No resource bounds on file tools
- **Severity:** Low · **Effort:** Low
- `read_file` slurps whole files into memory; `write_file` has no size cap; 30s is the only
  command bound. A hostile or buggy agent can OOM or fill the disk.
- **Fix:** Cap read/write sizes, add a total-output limit for commands, keep the timeout.

---

## P3 — Correctness / polish (not security-blocking)

### 10. JSON-RPC notification handling
- Requests without an `id` (notifications) still get a response printed; strictly they shouldn't.
  Handle `notifications/initialized` and no-`id` messages per spec.

### 11. Installer & docs
- `pip install -e` runs outside a venv by default; consider `pipx` or a dedicated venv.
- Update README's security model to match reality once #1–#4 land (remove "software CWD
  restriction" claim; state plainly that firejail/sandbox is required).

---

## Suggested order of attack
1. **#4** (path check) and **#5** (protect `.kennelbox/`) — small, high-value, land today.
2. **#2 + #3** — make the sandbox mandatory and route file tools through it.
3. **#1** — decide the interpreter policy; this defines the whole trust model.
4. **#7**, **#6**, then P2/P3 cleanup.
