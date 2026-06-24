"""Auto-venv setup: detects project type and initializes an isolated environment."""

import os
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console(stderr=True)


def detect_project_type(cwd: Path) -> str:
    """Return 'python', 'node', or 'generic'."""
    if (cwd / "requirements.txt").exists() or (cwd / "setup.py").exists() or (cwd / "pyproject.toml").exists():
        return "python"
    if (cwd / "package.json").exists():
        return "node"
    return "generic"


def setup_python_venv(cwd: Path) -> Path:
    venv_dir = cwd / ".kennelbox" / "venv"
    if venv_dir.exists():
        console.print(f"  [dim]Python venv already exists at {venv_dir}[/dim]")
        return venv_dir

    console.print("  [cyan]Creating Python venv...[/cyan]")
    subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        check=True,
        capture_output=True,
    )

    req = cwd / "requirements.txt"
    if req.exists():
        pip = venv_dir / "bin" / "pip"
        console.print("  [cyan]Installing requirements.txt...[/cyan]")
        subprocess.run(
            [str(pip), "install", "-r", str(req)],
            check=True,
            capture_output=True,
        )

    console.print(f"  [green]Python venv ready:[/green] {venv_dir}")
    return venv_dir


def setup_node_env(cwd: Path) -> Path:
    node_modules = cwd / "node_modules"
    console.print("  [cyan]Running npm install...[/cyan]")
    try:
        subprocess.run(
            ["npm", "install", "--prefix", str(cwd)],
            check=True,
            capture_output=True,
        )
        console.print(f"  [green]Node env ready:[/green] {node_modules}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        console.print("  [yellow]npm not found or install failed; skipping Node setup.[/yellow]")
    return node_modules


def setup_generic_env(cwd: Path) -> Path:
    env_dir = cwd / ".kennelbox" / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    console.print(f"  [green]Generic sandbox env ready:[/green] {env_dir}")
    return env_dir


def init_environment(cwd: Path) -> dict:
    project_type = detect_project_type(cwd)
    console.print(f"  [bold]Project type detected:[/bold] {project_type}")

    if project_type == "python":
        env_path = setup_python_venv(cwd)
        python_bin = env_path / "bin" / "python3"
        return {
            "type": project_type,
            "env_path": str(env_path),
            "python": str(python_bin),
            "activate": str(env_path / "bin" / "activate"),
        }
    elif project_type == "node":
        env_path = setup_node_env(cwd)
        return {"type": project_type, "env_path": str(env_path)}
    else:
        env_path = setup_generic_env(cwd)
        return {"type": project_type, "env_path": str(env_path)}
