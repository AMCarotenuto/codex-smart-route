from __future__ import annotations

import hashlib
import importlib.util
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_release_artifacts.py"
SPEC = importlib.util.spec_from_file_location("verify_release_artifacts", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def _source_tree(root: Path, version: str = "1.2.3") -> None:
    (root / "codex_smart_route/bundled_skill/agents").mkdir(parents=True)
    (root / "skill/codex-smart-route/agents").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nversion = "{version}"\n', encoding="utf-8")
    (root / "codex_smart_route/__init__.py").write_text(
        f'__version__ = "{version}"\n', encoding="utf-8"
    )
    skill = f'---\nname: codex-smart-route\nmetadata:\n  version: "{version}"\n---\n'
    for base in (root / "codex_smart_route/bundled_skill", root / "skill/codex-smart-route"):
        (base / "SKILL.md").write_text(skill, encoding="utf-8")
        (base / "agents/openai.yaml").write_text("interface: {}\n", encoding="utf-8")
    (root / "LICENSE").write_text("license\n", encoding="utf-8")
    (root / "THIRD_PARTY_NOTICES.md").write_text("notices\n", encoding="utf-8")
    (root / "CHANGELOG.md").write_text(
        f"# Changelog\n\n## [{version}] - 2026-09-07\n\n- Release notes.\n",
        encoding="utf-8",
    )


def _archives(dist: Path, version: str = "1.2.3") -> tuple[Path, Path]:
    dist.mkdir()
    wheel = dist / f"codex_smart_route-{version}-py3-none-any.whl"
    dist_info = f"codex_smart_route-{version}.dist-info"
    skill = f'---\nmetadata:\n  version: "{version}"\n---\n'
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("codex_smart_route/__init__.py", f'__version__ = "{version}"\n')
        archive.writestr("codex_smart_route/bundled_skill/SKILL.md", skill)
        archive.writestr("codex_smart_route/bundled_skill/agents/openai.yaml", "interface: {}\n")
        archive.writestr(f"{dist_info}/METADATA", f"Version: {version}\n")
        archive.writestr(f"{dist_info}/licenses/LICENSE", "license\n")
        archive.writestr(f"{dist_info}/licenses/THIRD_PARTY_NOTICES.md", "notices\n")
    sdist = dist / f"codex_smart_route-{version}.tar.gz"
    staging = dist.parent / f"codex_smart_route-{version}"
    for relative in (
        "LICENSE",
        "THIRD_PARTY_NOTICES.md",
        "codex_smart_route/bundled_skill/SKILL.md",
        "codex_smart_route/bundled_skill/agents/openai.yaml",
    ):
        target = staging / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(skill if relative.endswith("SKILL.md") else "content\n", encoding="utf-8")
    with tarfile.open(sdist, "w:gz") as archive:
        archive.add(staging, arcname=staging.name)
    return wheel, sdist


def test_validate_source_and_extract_notes(tmp_path: Path) -> None:
    _source_tree(tmp_path)

    notes = release.validate_source(tmp_path, "1.2.3")

    assert notes == "# Codex Smart Route 1.2.3\n\n- Release notes.\n"


def test_validate_source_rejects_skill_drift(tmp_path: Path) -> None:
    _source_tree(tmp_path)
    (tmp_path / "codex_smart_route/bundled_skill/SKILL.md").write_text(
        '---\nmetadata:\n  version: "9.9.9"\n---\n', encoding="utf-8"
    )

    with pytest.raises(release.ReleaseValidationError, match="bundled skill differs"):
        release.validate_source(tmp_path, "1.2.3")


def test_validate_dist_and_write_checksums(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    wheel, sdist = _archives(dist)

    assert release.validate_dist(dist, "1.2.3") == (wheel, sdist)
    output = tmp_path / "SHA256SUMS"
    release.write_checksums((wheel, sdist), output)

    expected = {
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}" for path in (wheel, sdist)
    }
    assert set(output.read_text(encoding="utf-8").splitlines()) == expected


def test_validate_dist_rejects_non_reproducible_copy(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    _archives(first)
    wheel, _ = _archives(second)
    wheel.write_bytes(wheel.read_bytes() + b"drift")

    with pytest.raises(release.ReleaseValidationError, match="not reproducible"):
        release.validate_dist(first, "1.2.3", second)
