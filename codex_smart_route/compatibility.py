"""Read-only compatibility detection for optional external developer tools."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

_INTEGRATIONS = {
    "rtk": {
        "executables": ("rtk",),
        "skills": (),
        "markers": ("RTK_ACTIVE", "RTK_SESSION", "RTK_WRAPPER"),
        "boundary": "command-wrapper",
    },
    "caveman": {
        "executables": (),
        "skills": ("caveman",),
        "markers": ("CAVEMAN_ACTIVE", "CAVEMAN_LEVEL"),
        "boundary": "communication-style",
    },
    "cavecrew": {
        "executables": (),
        "skills": ("cavecrew",),
        "markers": ("CAVECREW_ACTIVE", "CAVECREW_MODEL_OWNER"),
        "boundary": "work-orchestration",
    },
}

_SMART_ROUTE_OWNERS = frozenset({"smart-route", "codex-smart-route"})


def _skill_candidates(name: str, user_home: Path, project_root: Path | None) -> list[Path]:
    candidates = [user_home / ".agents" / "skills" / name / "SKILL.md"]
    if project_root is not None:
        candidates.insert(0, project_root / ".agents" / "skills" / name / "SKILL.md")
    return candidates


def _contains_router(value: str) -> bool:
    normalized = value.casefold().replace("_", "-")
    return "smart-route" in normalized or "codex-smart-route" in normalized


def detect_integrations(
    *,
    config_enabled: bool,
    adapter: str,
    auto_slug: str,
    project_root: Path | None = None,
    user_home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Report presence and obvious ownership conflicts without reading secrets.

    Environment values are used only for boolean/conflict classification. They are
    never copied into the returned report.
    """

    environment = os.environ if environ is None else environ
    actual_user_home = user_home or Path.home()
    resolve_executable = which or shutil.which
    integrations: dict[str, dict[str, Any]] = {}
    for name, definition in _INTEGRATIONS.items():
        executable_paths = [
            resolved
            for executable in definition["executables"]
            if (resolved := resolve_executable(executable)) is not None
        ]
        skill_paths = [
            str(candidate)
            for skill in definition["skills"]
            for candidate in _skill_candidates(skill, actual_user_home, project_root)
            if candidate.is_file()
        ]
        environment_markers = [marker for marker in definition["markers"] if marker in environment]
        integrations[name] = {
            "detected": bool(executable_paths or skill_paths or environment_markers),
            "boundary": definition["boundary"],
            "executables": executable_paths,
            "skill_manifests": skill_paths,
            "environment_markers": environment_markers,
        }

    warnings: list[str] = []
    adapter_normalized = adapter.casefold().replace("_", "-")
    if _contains_router(adapter_normalized):
        warnings.append(
            "adapter appears to invoke Smart Route recursively; configure a Codex adapter"
        )
    wrapper_chain = environment.get("CODEX_SMART_ROUTE_WRAPPER_CHAIN", "")
    if wrapper_chain and _contains_router(wrapper_chain):
        warnings.append(
            "CODEX_SMART_ROUTE_WRAPPER_CHAIN contains Smart Route; remove recursive routing"
        )
    owner = environment.get("CODEX_SMART_ROUTE_MODEL_OWNER", "").strip().casefold()
    cavecrew_owner = environment.get("CAVECREW_MODEL_OWNER", "").strip().casefold()
    if config_enabled and owner and owner not in _SMART_ROUTE_OWNERS:
        warnings.append("CODEX_SMART_ROUTE_MODEL_OWNER conflicts with enabled Smart Route routing")
    if config_enabled and cavecrew_owner and cavecrew_owner not in _SMART_ROUTE_OWNERS:
        warnings.append("CAVECREW_MODEL_OWNER conflicts with Smart Route main-model ownership")
    if (
        config_enabled
        and auto_slug.casefold()
        in {
            owner,
            cavecrew_owner,
        }
        and auto_slug.casefold() not in _SMART_ROUTE_OWNERS
    ):
        warnings.append("Auto model slug cannot be used as a model-selection owner")

    return {
        "integrations": integrations,
        "main_model_owner": "smart-route" if config_enabled else "external-or-manual",
        "routing_authorities": 1 if config_enabled else 0,
        "warnings": warnings,
        "inspection": "paths-and-marker-names-only",
    }


def compatibility_matrix() -> list[dict[str, str]]:
    """Machine-readable matrix mirrored by docs and deterministic tests."""

    return [
        {
            "scenario": "smart-route",
            "owner": "smart-route",
            "contract": "read-only and file-modifying tasks preserve process transport",
            "verification": "automated-offline",
        },
        {
            "scenario": "smart-route+rtk",
            "owner": "smart-route",
            "contract": "RTK may wrap commands but cannot change routing controls",
            "verification": "optional-external",
        },
        {
            "scenario": "smart-route+caveman",
            "owner": "smart-route",
            "contract": "style may change prose but not JSON or routing controls",
            "verification": "automated-offline",
        },
        {
            "scenario": "smart-route+cavecrew",
            "owner": "smart-route",
            "contract": "orchestration delegates main-model selection to Smart Route",
            "verification": "automated-offline",
        },
        {
            "scenario": "smart-route+rtk+caveman+cavecrew",
            "owner": "smart-route",
            "contract": "one routing owner; wrapper, style, and orchestration stay orthogonal",
            "verification": "automated-offline+optional-external",
        },
    ]
