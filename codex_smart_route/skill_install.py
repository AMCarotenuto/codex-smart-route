"""Reversible install/remove for project-owned Codex skill files."""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path


class SkillInstallError(RuntimeError):
    pass


def _hash_tree(path: Path) -> str:
    digest = hashlib.sha256()
    for file in sorted(
        item
        for item in path.rglob("*")
        if item.is_file() and item.name != ".smart-route-manifest.json"
    ):
        digest.update(file.relative_to(path).as_posix().encode())
        digest.update(file.read_bytes())
    return digest.hexdigest()


def install_skill(
    source: Path, codex_home: Path, dry_run: bool = False
) -> dict[str, str | bool | None]:
    target = codex_home / "skills" / "codex-smart-route"
    backup: Path | None = None
    if target.exists():
        manifest_path = target / ".smart-route-manifest.json"
        if not manifest_path.exists():
            raise SkillInstallError("target skill exists and is not managed by Codex Smart Route")
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        current_hash = _hash_tree(target)
        if current_hash != manifest_data.get("content_hash"):
            raise SkillInstallError("installed skill was modified; refusing overwrite")
        if dry_run:
            return {"target": str(target), "backup": None, "dry_run": True}
        shutil.rmtree(target)
    if dry_run:
        return {"target": str(target), "backup": None, "dry_run": True}
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    tree_hash = _hash_tree(target)
    manifest_data = {
        "version": 1,
        "installed_at": int(time.time()),
        "content_hash": tree_hash,
        "backup": str(backup) if backup else None,
    }
    (target / ".smart-route-manifest.json").write_text(
        json.dumps(manifest_data, indent=2) + "\n", encoding="utf-8"
    )
    return {"target": str(target), "backup": None, "dry_run": False}


def uninstall_skill(codex_home: Path, dry_run: bool = False) -> dict[str, str | bool]:
    target = codex_home / "skills" / "codex-smart-route"
    manifest_path = target / ".smart-route-manifest.json"
    if not manifest_path.exists():
        raise SkillInstallError("managed skill installation not found")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    current_hash = _hash_tree(target)
    if current_hash != manifest.get("content_hash"):
        raise SkillInstallError("installed skill was modified; refusing destructive uninstall")
    if not dry_run:
        shutil.rmtree(target)
    return {"target": str(target), "dry_run": dry_run}
