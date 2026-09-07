"""Read-only local diagnostics. Never reads credential contents."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import RouterConfig, default_home
from .discovery import AppServerDiscovery, DiscoveryError


def _version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return None
    return (
        (result.stdout or result.stderr).strip().splitlines()[0]
        if (result.stdout or result.stderr).strip()
        else None
    )


def port_available(host: str = "127.0.0.1", port: int = 8765) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def run_doctor(
    config: RouterConfig, codex_home: Path | None = None, discover: bool = True
) -> dict[str, Any]:
    home = default_home()
    actual_codex_home = codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    auth_path = actual_codex_home / "auth.json"
    report: dict[str, Any] = {
        "python": sys.version.split()[0],
        "python_supported": sys.version_info >= (3, 11),
        "codex_executable": shutil.which("codex"),
        "codex_version": _version(["codex", "--version"]),
        "authentication": "present-uninspected" if auth_path.exists() else "not-detected",
        "config_valid": True,
        "router_enabled": config.enabled,
        "adapter": config.adapter,
        "app_server_experimental": config.experimental_app_server,
        "loopback": "127.0.0.1",
        "default_port_available": port_available(),
        "skill_installed": (
            actual_codex_home / "skills" / "codex-smart-route" / "SKILL.md"
        ).exists(),
        "global_changes_required": False,
        "state_directory": str(home),
        "models": [],
        "warnings": [],
    }
    if discover and report["codex_executable"]:
        try:
            models = AppServerDiscovery().discover()
            report["models"] = [
                {
                    "model": model.model,
                    "reasoning_efforts": model.reasoning_efforts,
                    "source": model.source,
                }
                for model in models
            ]
        except DiscoveryError as exc:
            report["warnings"].append(str(exc))
    return report
