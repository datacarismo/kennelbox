#!/usr/bin/env python3
"""kennelbox — sandboxed AI agent workspace with MCP bridge."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

try:
    import tomllib
except ModuleNotFoundError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ModuleNotFoundError:
        tomllib = None  # type: ignore[assignment]

app = typer.Typer(
    name="kennelbox",
    help="Sandboxed AI agent workspace with MCP bridge.",
    add_completion=False,
    invoke_without_command=True,
)
console = Console(stderr=True)

BANNER = """\
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
╚══════════════════════════════╝"""


def print_banner() -> None:
    console.print(BANNER, style="bold cyan")


def _load_toml(path: Path) -> dict:
    if tomllib is None:
        console.print("[red]Error:[/red] tomllib/tomli not installed. Run: pip install tomli")
        raise typer.Exit(1)
    with open(path, "rb") as f:
        return tomllib.load(f)


def _kennelbox_dir(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / ".kennelbox"


def _require_init(cwd: Path | None = None) -> Path:
    kb_dir = _kennelbox_dir(cwd)
    if not kb_dir.exists():
        console.print(
            "[red]Error:[/red] No .kennelbox directory found. Run [bold]kennelbox init[/bold] first."
        )
        raise typer.Exit(1)
    return kb_dir


def _load_sandbox_cfg(kb_dir: Path) -> dict:
    cfg_path = kb_dir / "sandbox.toml"
    if not cfg_path.exists():
        return {}
    return _load_toml(cfg_path).get("firejail", {})


def _load_state(kb_dir: Path) -> dict:
    state_file = kb_dir / "state.json"
    if not state_file.exists():
        return {}
    return json.loads(state_file.read_text())


def _save_state(kb_dir: Path, state: dict) -> None:
    (kb_dir / "state.json").write_text(json.dumps(state, indent=2))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    print_banner()
    if ctx.invoked_subcommand is None:
        console.print("\nRun [bold cyan]kennelbox --help[/bold cyan] to see available commands.\n")


@app.command()
def init(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Target directory (default: current)"),
) -> None:
    """Initialize a kennelbox sandbox in the current directory."""
    target = (cwd or Path.cwd()).resolve()
    kb_dir = target / ".kennelbox"

    if kb_dir.exists():
        console.print(f"[yellow]Already initialized:[/yellow] {kb_dir}")
        raise typer.Exit(0)

    console.print(f"\n[bold]Initializing kennelbox in[/bold] {target}\n")
    kb_dir.mkdir(parents=True)

    # Copy default configs from package config/ dir
    pkg_config = Path(__file__).parent / "config"
    for cfg_file in ("allowlist.toml", "sandbox.toml"):
        src = pkg_config / cfg_file
        dst = kb_dir / cfg_file
        if src.exists():
            shutil.copy2(src, dst)
            console.print(f"  [green]created[/green] .kennelbox/{cfg_file}")
        else:
            console.print(f"  [yellow]warning:[/yellow] default config {cfg_file} not found at {src}")

    # Auto-detect project type and set up environment
    from sandbox.venv_mgr import init_environment
    env_info = init_environment(target)

    # Persist state
    _save_state(kb_dir, {
        "cwd": str(target),
        "project_type": env_info["type"],
        "env_path": env_info.get("env_path", ""),
        "active_agent": None,
    })

    console.print(f"\n[bold green]kennelbox initialized.[/bold green]")
    console.print(f"  Config:  .kennelbox/allowlist.toml & sandbox.toml")
    console.print(f"  Next:    [bold]kennelbox run --agent <name>[/bold]\n")


@app.command()
def run(
    agent: str = typer.Option(..., "--agent", "-a", help="Agent name (e.g. openclaw, hermes)"),
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory (default: current)"),
    stdio: bool = typer.Option(True, "--stdio/--no-stdio", help="Use stdio MCP transport"),
) -> None:
    """Start a sandboxed MCP agent session."""
    target = (cwd or Path.cwd()).resolve()
    kb_dir = _require_init(target)
    sandbox_cfg = _load_sandbox_cfg(kb_dir)
    state = _load_state(kb_dir)

    console.print(f"\n[bold]Starting sandboxed session[/bold]")
    console.print(f"  Agent:   [cyan]{agent}[/cyan]")
    console.print(f"  CWD:     {target}")
    console.print(f"  Network: {'[red]blocked[/red]' if not sandbox_cfg.get('network', False) else '[green]allowed[/green]'}")

    from sandbox.jail import firejail_available
    if firejail_available():
        console.print("  Sandbox: [green]firejail active[/green]")
    else:
        console.print("  Sandbox: [yellow]firejail not found — CWD-only restriction[/yellow]")

    state["active_agent"] = agent
    _save_state(kb_dir, state)

    from sandbox.jail import firejail_available
    if not firejail_available():
        console.print(
            "\n[bold red]Error:[/bold red] firejail is required but not installed.\n"
            "  Install it with:  [bold]sudo apt install firejail[/bold]\n"
            "  kennelbox refuses to start without a kernel-level sandbox.\n"
        )
        raise typer.Exit(1)

    console.print("\n[bold cyan]MCP bridge running on stdio. Connect your agent now.[/bold cyan]")
    console.print("[dim](Ctrl-C to stop)[/dim]\n")

    try:
        from agent_bridge.server import run_server
        run_server(target, sandbox_cfg)
    except KeyboardInterrupt:
        console.print("\n[yellow]Session terminated by user.[/yellow]")
    finally:
        state["active_agent"] = None
        _save_state(kb_dir, state)


@app.command()
def rules(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory (default: current)"),
) -> None:
    """Display current allow/blocklist rules."""
    target = (cwd or Path.cwd()).resolve()
    kb_dir = _require_init(target)
    cfg_path = kb_dir / "allowlist.toml"
    cfg = _load_toml(cfg_path)
    cmds = cfg.get("commands", {})
    files = cfg.get("files", {})

    console.print()

    t = Table(title="Command Rules", box=box.ROUNDED, show_header=True)
    t.add_column("Type", style="bold")
    t.add_column("Values")
    for cmd in cmds.get("allowed", []):
        t.add_row("[green]ALLOWED[/green]", cmd)
    for cmd in cmds.get("warn_patterns", cmds.get("blocked", [])):
        t.add_row("[yellow]WARN[/yellow]", cmd)
    console.print(t)

    console.print()

    b = Table(title="Blocked Argument Flags", box=box.ROUNDED, show_header=True)
    b.add_column("Type", style="bold")
    b.add_column("Flag")
    for flag in cmds.get("blocked_args", ["-c", "-e", "--eval", "--exec", "-x", "--command"]):
        b.add_row("[red]BLOCKED[/red]", flag)
    console.print(b)
    console.print("[dim]These flags enable inline code execution and are denied regardless of base command.[/dim]")

    console.print()

    f = Table(title="File Extension Rules", box=box.ROUNDED, show_header=True)
    f.add_column("Type", style="bold")
    f.add_column("Extension")
    for ext in files.get("allowed_extensions", []):
        f.add_row("[green]ALLOWED[/green]", ext)
    console.print(f)
    console.print("[dim]Dotfiles and sensitive filenames (.env, .pem, .key, etc.) are always denied.[/dim]")
    console.print()


@app.command()
def status(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory (default: current)"),
) -> None:
    """Show active sandbox state."""
    target = (cwd or Path.cwd()).resolve()
    kb_dir = _require_init(target)
    state = _load_state(kb_dir)
    sandbox_cfg = _load_sandbox_cfg(kb_dir)

    from sandbox.jail import firejail_available

    console.print()
    t = Table(title="Kennelbox Status", box=box.ROUNDED)
    t.add_column("Key", style="bold cyan")
    t.add_column("Value")

    t.add_row("Project root", str(state.get("cwd", target)))
    t.add_row("Project type", state.get("project_type", "unknown"))
    t.add_row("Env path", state.get("env_path", "—"))
    t.add_row(
        "Active agent",
        f"[green]{state['active_agent']}[/green]" if state.get("active_agent") else "[dim]none[/dim]",
    )
    t.add_row(
        "Command subprocess network",
        "[red]blocked[/red]" if not sandbox_cfg.get("network", False) else "[green]allowed[/green]",
    )
    t.add_row(
        "Firejail",
        "[green]available[/green]" if firejail_available() else "[yellow]not installed[/yellow]",
    )
    t.add_row(
        "Seccomp",
        "[green]enabled[/green]" if sandbox_cfg.get("seccomp", True) else "[dim]disabled[/dim]",
    )
    console.print(t)
    console.print()


@app.command()
def release(
    cwd: Optional[Path] = typer.Option(None, "--cwd", help="Project directory (default: current)"),
    confirm: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Tear down sandbox and clean up .kennelbox directory."""
    target = (cwd or Path.cwd()).resolve()
    kb_dir = _require_init(target)

    if not confirm:
        typer.confirm(
            f"This will delete {kb_dir} and all kennelbox state. Continue?",
            abort=True,
        )

    shutil.rmtree(kb_dir)
    console.print(f"\n[green]Sandbox released.[/green] {kb_dir} removed.\n")


if __name__ == "__main__":
    app()
