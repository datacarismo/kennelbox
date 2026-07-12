"""HTTP transport for the kennelbox MCP bridge.

Exposes a threaded HTTP server so remote agents can connect over the network
instead of stdio. Every request is authenticated and IP-filtered before being
dispatched through the same allowlist/sandbox pipeline as the stdio server.

Endpoints:
  POST /         JSON-RPC 2.0 request → JSON-RPC 2.0 response
  GET  /health   {"status": "ok", "version": "0.1.0"}

Security posture:
  - A bearer token is REQUIRED — run_http_server refuses to start without one.
  - Token comparison uses secrets.compare_digest (constant-time).
  - Binds 127.0.0.1 by default; pass host explicitly (e.g. a Tailscale IP)
    to accept remote connections. 0.0.0.0 is allowed but never the default.
"""

from __future__ import annotations

import json
import secrets
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

from rich.console import Console

console = Console(stderr=True)

VERSION = "0.1.0"


class _ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class _KennelboxHandler(BaseHTTPRequestHandler):
    # Injected by run_http_server before the server starts
    auth_token: str = ""
    allowed_ips: list[str] | None = None
    cwd: Path | None = None
    guard = None
    sandbox_cfg: dict = {}

    # ------------------------------------------------------------------
    # Request handlers
    # ------------------------------------------------------------------

    def do_POST(self) -> None:
        if not self._ip_ok():
            self._text(403, "Forbidden: IP not allowed")
            return
        if not self._auth_ok():
            self._text(401, "Unauthorized: invalid or missing Bearer token")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            request = json.loads(body)
        except json.JSONDecodeError as exc:
            self._send_json({
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": f"Parse error: {exc}"},
            })
            return

        # JSON-RPC 2.0: notifications (no "id") get 202 Accepted, no body
        if "id" not in request:
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        from agent_bridge.server import dispatch
        response = dispatch(request, self.cwd, self.guard, self.sandbox_cfg)
        self._send_json(response)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json({"status": "ok", "version": VERSION})
        else:
            self._text(404, "Not found")

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _ip_ok(self) -> bool:
        if not self.allowed_ips:
            return True
        return self.client_address[0] in self.allowed_ips

    def _auth_ok(self) -> bool:
        header = self.headers.get("Authorization", "")
        expected = f"Bearer {self.auth_token}"
        return secrets.compare_digest(header.encode(), expected.encode())

    # ------------------------------------------------------------------
    # Response helpers
    # ------------------------------------------------------------------

    def _send_json(self, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, code: int, message: str) -> None:
        body = message.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        method = fmt % args
        console.print(f"  [dim]{self.client_address[0]}[/dim]  {method}")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_http_server(
    cwd: Path,
    sandbox_cfg: dict,
    host: str = "127.0.0.1",
    port: int = 7333,
    token: str = "",
    allowed_ips: list[str] | None = None,
) -> None:
    """Start the threaded HTTP MCP server and block until Ctrl-C.

    A non-empty token is required: kennelbox never serves the MCP bridge
    over a network socket without authentication.
    """
    if not token:
        raise ValueError("run_http_server requires a bearer token — refusing to serve unauthenticated")

    from agent_bridge.server import AllowlistGuard

    guard = AllowlistGuard(cwd / ".kennelbox")

    # Inject shared config into the handler class (one instance per thread)
    _KennelboxHandler.auth_token = token
    _KennelboxHandler.allowed_ips = allowed_ips
    _KennelboxHandler.cwd = cwd
    _KennelboxHandler.guard = guard
    _KennelboxHandler.sandbox_cfg = sandbox_cfg

    server = _ThreadedHTTPServer((host, port), _KennelboxHandler)

    console.print(f"\n  [bold cyan]HTTP MCP server listening on {host}:{port}[/bold cyan]")
    if host == "0.0.0.0":
        console.print("  [yellow]Warning: bound to all interfaces — prefer a specific IP (e.g. your Tailscale address)[/yellow]")
    console.print(f"\n  [bold]Agent connection details:[/bold]")
    console.print(f"    URL:    http://{host}:{port}")
    console.print(f"    Token:  Bearer {token}")
    if allowed_ips:
        console.print(f"    IPs:    {', '.join(allowed_ips)}")
    else:
        console.print("    IPs:    unrestricted (any client with the token)")

    console.print("\n  [dim](Ctrl-C to stop)[/dim]\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
