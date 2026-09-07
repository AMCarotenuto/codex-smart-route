"""Scoped, reversible installation for project-owned Codex skill files."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import Literal

SkillScope = Literal["user", "repo", "legacy"]
SKILL_NAME = "codex-smart-route"
MANIFEST_NAME = ".smart-route-manifest.json"
MANIFEST_OWNER = "codex-smart-route"
SKILL_LOCATION_DOCS = "https://developers.openai.com/codex/skills#where-codex-loads-local-skills"


class SkillInstallError(RuntimeError):
    pass


def skill_root_path(scope: SkillScope, base: PurePath) -> PurePath:
    """Return platform-neutral root shape for path tests and diagnostics."""
    if scope in {"user", "repo"}:
        return base / ".agents" / "skills"
    if scope == "legacy":
        return base / "skills"
    raise SkillInstallError(f"unknown skill scope: {scope}")


@dataclass(frozen=True)
class SkillLocation:
    scope: SkillScope
    root: Path
    target: Path
    documented: bool
    compatibility: str

    def to_dict(self) -> dict[str, str | bool]:
        return {
            "scope": self.scope,
            "root": str(self.root),
            "target": str(self.target),
            "documented": self.documented,
            "compatibility": self.compatibility,
        }


def resolve_skill_location(
    scope: SkillScope,
    *,
    user_home: Path | None = None,
    repo: Path | None = None,
    codex_home: Path | None = None,
) -> SkillLocation:
    """Resolve current documented roots plus explicitly requested legacy root."""
    if scope == "user":
        home = (user_home or Path.home()).expanduser()
        root = Path(skill_root_path(scope, home))
        return SkillLocation(scope, root, root / SKILL_NAME, True, "supported-documented")
    if scope == "repo":
        if repo is None:
            raise SkillInstallError("--scope repo requires --repo PATH")
        repository = repo.expanduser().resolve()
        if not repository.is_dir():
            raise SkillInstallError(f"repository path is not a directory: {repository}")
        root = Path(skill_root_path(scope, repository))
        target = root / SKILL_NAME
        if not target.resolve(strict=False).is_relative_to(repository):
            raise SkillInstallError(
                "repository skill root resolves outside the selected repository"
            )
        return SkillLocation(scope, root, target, True, "supported-documented")
    if scope == "legacy":
        home = (
            codex_home or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        ).expanduser()
        root = Path(skill_root_path(scope, home))
        return SkillLocation(scope, root, root / SKILL_NAME, False, "explicit-unverified")
    raise SkillInstallError(f"unknown skill scope: {scope}")


def detected_skill_locations(
    *,
    user_home: Path | None = None,
    repo: Path | None = None,
    codex_home: Path | None = None,
) -> list[dict[str, str | bool]]:
    scopes: list[SkillScope] = ["user"]
    if repo is not None:
        scopes.append("repo")
    scopes.append("legacy")
    result: list[dict[str, str | bool]] = []
    for scope in scopes:
        location = resolve_skill_location(
            scope, user_home=user_home, repo=repo, codex_home=codex_home
        )
        row = location.to_dict()
        row["installed"] = (location.target / "SKILL.md").is_file()
        row["managed"] = (location.target / MANIFEST_NAME).is_file()
        result.append(row)
    return result


def _hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(
        item for item in path.rglob("*") if item.is_file() and item.name != MANIFEST_NAME
    ):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def _validated_manifest(target: Path, scope: SkillScope) -> dict[str, object]:
    manifest_path = target / MANIFEST_NAME
    if not manifest_path.is_file():
        raise SkillInstallError("target skill exists and is not managed by Codex Smart Route")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SkillInstallError("installed skill has an invalid integrity manifest") from exc
    if not isinstance(manifest, dict):
        raise SkillInstallError("installed skill has an invalid integrity manifest")
    version = manifest.get("version")
    if version == 1 and scope == "legacy":
        return manifest
    if (
        version != 2
        or manifest.get("owner") != MANIFEST_OWNER
        or manifest.get("scope") != scope
        or manifest.get("target") != str(target.resolve())
    ):
        raise SkillInstallError("installed skill manifest does not own this scoped target")
    return manifest


def install_skill(
    source: Path,
    dry_run: bool = False,
    *,
    scope: SkillScope = "user",
    user_home: Path | None = None,
    repo: Path | None = None,
    codex_home: Path | None = None,
) -> dict[str, str | bool | None]:
    source = source.expanduser().resolve()
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise SkillInstallError(f"skill source is invalid: {source}")
    location = resolve_skill_location(scope, user_home=user_home, repo=repo, codex_home=codex_home)
    target = location.target
    if source == target.resolve(strict=False):
        raise SkillInstallError("skill source and install target must differ")
    if target.is_symlink():
        raise SkillInstallError("target skill is a symlink; refusing overwrite")
    action = "install"
    if target.exists():
        manifest = _validated_manifest(target, scope)
        if _hash_tree(target) != manifest.get("content_hash"):
            raise SkillInstallError("installed skill was modified; refusing overwrite")
        action = "update"
        if not dry_run:
            shutil.rmtree(target)
    result: dict[str, str | bool | None] = {
        **location.to_dict(),
        "action": action,
        "dry_run": dry_run,
        "documentation": SKILL_LOCATION_DOCS,
    }
    if dry_run:
        return result
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    manifest_data = {
        "version": 2,
        "owner": MANIFEST_OWNER,
        "scope": scope,
        "target": str(target.resolve()),
        "installed_at": int(time.time()),
        "content_hash": _hash_tree(target),
    }
    (target / MANIFEST_NAME).write_text(
        json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8"
    )
    return result


def uninstall_skill(
    dry_run: bool = False,
    *,
    scope: SkillScope = "user",
    user_home: Path | None = None,
    repo: Path | None = None,
    codex_home: Path | None = None,
) -> dict[str, str | bool | None]:
    location = resolve_skill_location(scope, user_home=user_home, repo=repo, codex_home=codex_home)
    target = location.target
    if target.is_symlink():
        raise SkillInstallError("target skill is a symlink; refusing destructive uninstall")
    manifest = _validated_manifest(target, scope)
    if _hash_tree(target) != manifest.get("content_hash"):
        raise SkillInstallError("installed skill was modified; refusing destructive uninstall")
    if not dry_run:
        shutil.rmtree(target)
    return {
        **location.to_dict(),
        "action": "uninstall",
        "dry_run": dry_run,
        "documentation": SKILL_LOCATION_DOCS,
    }
