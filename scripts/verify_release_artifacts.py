#!/usr/bin/env python3
"""Validate source metadata and release archives without importing build output."""

from __future__ import annotations

import argparse
import hashlib
import re
import tarfile
import tomllib
import zipfile
from pathlib import Path

PROJECT = "codex-smart-route"
MODULE = "codex_smart_route"
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:a[0-9]+|b[0-9]+|rc[0-9]+)?$")


class ReleaseValidationError(RuntimeError):
    """Release input or output violates a distribution invariant."""


def _read_version_assignment(text: str) -> str:
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    if match is None:
        raise ReleaseValidationError("package __version__ assignment is missing")
    return match.group(1)


def _read_skill_version(text: str) -> str:
    frontmatter = text.split("---", 2)
    if len(frontmatter) < 3:
        raise ReleaseValidationError("skill YAML frontmatter is missing")
    match = re.search(r'^\s+version:\s*["\']([^"\']+)["\']\s*$', frontmatter[1], re.MULTILINE)
    if match is None:
        raise ReleaseValidationError("skill metadata.version is missing")
    return match.group(1)


def _require_equal(label: str, actual: str, expected: str) -> None:
    if actual != expected:
        raise ReleaseValidationError(f"{label} is {actual!r}, expected {expected!r}")


def changelog_notes(root: Path, version: str) -> str:
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = re.compile(rf"^## \[{re.escape(version)}\](?:\s+-\s+[^\n]+)?\s*$", re.MULTILINE)
    match = heading.search(changelog)
    if match is None:
        raise ReleaseValidationError(f"CHANGELOG.md has no section for {version}")
    next_heading = re.search(r"^## \[", changelog[match.end() :], re.MULTILINE)
    end = match.end() + next_heading.start() if next_heading else len(changelog)
    body = changelog[match.end() : end].strip()
    if not body:
        raise ReleaseValidationError(f"CHANGELOG.md section for {version} is empty")
    return f"# Codex Smart Route {version}\n\n{body}\n"


def validate_source(root: Path, expected_version: str) -> str:
    if SEMVER.fullmatch(expected_version) is None:
        raise ReleaseValidationError(f"unsupported release version: {expected_version!r}")
    metadata = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    _require_equal("project.version", metadata["project"]["version"], expected_version)
    _require_equal(
        "package __version__",
        _read_version_assignment((root / MODULE / "__init__.py").read_text(encoding="utf-8")),
        expected_version,
    )
    source_skill = root / "skill" / PROJECT
    bundled_skill = root / MODULE / "bundled_skill"
    for relative in (Path("SKILL.md"), Path("agents/openai.yaml")):
        source = (source_skill / relative).read_bytes()
        bundled = (bundled_skill / relative).read_bytes()
        if source != bundled:
            raise ReleaseValidationError(
                f"bundled skill differs from source: {relative.as_posix()}"
            )
    _require_equal(
        "bundled skill metadata.version",
        _read_skill_version((bundled_skill / "SKILL.md").read_text(encoding="utf-8")),
        expected_version,
    )
    for required in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if not (root / required).is_file():
            raise ReleaseValidationError(f"required distribution notice is missing: {required}")
    return changelog_notes(root, expected_version)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_files(dist: Path, version: str) -> tuple[Path, Path]:
    wheel = dist / f"{MODULE}-{version}-py3-none-any.whl"
    sdist = dist / f"{MODULE}-{version}.tar.gz"
    missing = [path.name for path in (wheel, sdist) if not path.is_file()]
    unexpected = sorted(
        path.name
        for path in dist.iterdir()
        if path.is_file() and path.suffix in {".whl", ".gz"} and path not in {wheel, sdist}
    )
    if missing or unexpected:
        raise ReleaseValidationError(
            f"release artifact set mismatch; missing={missing}, unexpected={unexpected}"
        )
    return wheel, sdist


def _validate_wheel(wheel: Path, version: str) -> None:
    dist_info = f"{MODULE}-{version}.dist-info"
    required = {
        f"{MODULE}/__init__.py",
        f"{MODULE}/bundled_skill/SKILL.md",
        f"{MODULE}/bundled_skill/agents/openai.yaml",
        f"{dist_info}/METADATA",
        f"{dist_info}/licenses/LICENSE",
        f"{dist_info}/licenses/THIRD_PARTY_NOTICES.md",
    }
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        missing = sorted(required - names)
        if missing:
            raise ReleaseValidationError(f"wheel is missing required files: {missing}")
        metadata = archive.read(f"{dist_info}/METADATA").decode("utf-8")
        if f"Version: {version}\n" not in metadata:
            raise ReleaseValidationError("wheel METADATA version does not match release")
        _require_equal(
            "wheel package __version__",
            _read_version_assignment(archive.read(f"{MODULE}/__init__.py").decode("utf-8")),
            version,
        )
        _require_equal(
            "wheel skill metadata.version",
            _read_skill_version(archive.read(f"{MODULE}/bundled_skill/SKILL.md").decode("utf-8")),
            version,
        )


def _validate_sdist(sdist: Path, version: str) -> None:
    prefix = f"{MODULE}-{version}"
    required = {
        f"{prefix}/LICENSE",
        f"{prefix}/THIRD_PARTY_NOTICES.md",
        f"{prefix}/{MODULE}/bundled_skill/SKILL.md",
        f"{prefix}/{MODULE}/bundled_skill/agents/openai.yaml",
    }
    with tarfile.open(sdist, "r:gz") as archive:
        names = set(archive.getnames())
    missing = sorted(required - names)
    if missing:
        raise ReleaseValidationError(f"sdist is missing required files: {missing}")


def validate_dist(dist: Path, version: str, compare: Path | None = None) -> tuple[Path, Path]:
    wheel, sdist = artifact_files(dist, version)
    _validate_wheel(wheel, version)
    _validate_sdist(sdist, version)
    if compare is not None:
        other_wheel, other_sdist = artifact_files(compare, version)
        for first, second in ((wheel, other_wheel), (sdist, other_sdist)):
            if _sha256(first) != _sha256(second):
                raise ReleaseValidationError(f"artifact is not reproducible: {first.name}")
    return wheel, sdist


def write_checksums(paths: tuple[Path, Path], output: Path) -> None:
    lines = [f"{_sha256(path)}  {path.name}" for path in sorted(paths)]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--version", required=True)
    parser.add_argument("--notes-out", type=Path)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--checksums", type=Path)
    args = parser.parse_args()
    notes = validate_source(args.root.resolve(), args.version)
    if args.notes_out:
        args.notes_out.write_text(notes, encoding="utf-8", newline="\n")
    if args.dist:
        artifacts = validate_dist(args.dist.resolve(), args.version, args.compare)
        if args.checksums:
            write_checksums(artifacts, args.checksums.resolve())
    elif args.compare or args.checksums:
        parser.error("--compare and --checksums require --dist")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
