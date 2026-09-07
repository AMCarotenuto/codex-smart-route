"""Read-only local diagnostics. Never reads credential contents."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import ConfigSource, RouterConfig, default_home
from .discovery import AppServerDiscovery, DiscoveryError
from .skill_install import (
    SKILL_LOCATION_DOCS,
    SkillScope,
    detected_skill_locations,
    resolve_skill_location,
)


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
    config: RouterConfig,
    codex_home: Path | None = None,
    discover: bool = True,
    *,
    skill_scope: SkillScope = "user",
    repo: Path | None = None,
    user_home: Path | None = None,
    config_sources: tuple[ConfigSource, ...] = (),
    project_root: Path | None = None,
    project_id: str | None = None,
    state_directory: Path | None = None,
) -> dict[str, Any]:
    home = default_home()
    actual_codex_home = codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    actual_user_home = user_home or Path.home()
    auth_path = actual_codex_home / "auth.json"
    locations = detected_skill_locations(
        user_home=actual_user_home, repo=repo, codex_home=actual_codex_home
    )
    selected_location = resolve_skill_location(
        skill_scope, user_home=actual_user_home, repo=repo, codex_home=actual_codex_home
    )
    legacy = next(item for item in locations if item["scope"] == "legacy")
    if legacy["installed"]:
        legacy_status = (
            "managed-installation-detected"
            if legacy["managed"]
            else "unmanaged-installation-detected"
        )
    else:
        legacy_status = "not-detected-unverified"
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
        "skill_installed": (selected_location.target / "SKILL.md").is_file(),
        "skill_scope": skill_scope,
        "selected_skill_root": str(selected_location.root),
        "skill_roots": locations,
        "skill_roots_documentation": SKILL_LOCATION_DOCS,
        "skill_roots_version_basis": "current-official-documentation",
        "legacy_compatibility": legacy_status,
        "global_changes_required": False,
        "state_directory": str(state_directory or home),
        "config_sources": [{"kind": source.kind, "path": source.path} for source in config_sources],
        "project_root": str(project_root) if project_root else None,
        "project_id": project_id,
        "models": [],
        "app_server_protocol": None,
        "warnings": [],
    }
    if discover and report["codex_executable"]:
        try:
            discovery = AppServerDiscovery()
            models = discovery.discover()
            report["app_server_protocol"] = discovery.last_diagnostics.to_dict()
            report["warnings"].extend(discovery.last_diagnostics.warnings)
            report["models"] = [
                {
                    "model": model.model,
                    "reasoning_efforts": model.reasoning_efforts,
                    "source": model.source,
                }
                for model in models
            ]
        except DiscoveryError as exc:
            report["app_server_protocol"] = discovery.last_diagnostics.to_dict()
            report["warnings"].append(str(exc))
    return report
