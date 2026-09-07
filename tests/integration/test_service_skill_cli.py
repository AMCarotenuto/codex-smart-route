from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from codex_smart_route.cli import main
from codex_smart_route.doctor import run_doctor
from codex_smart_route.models import TaskContext
from codex_smart_route.service import RoutingService
from codex_smart_route.skill_install import SkillInstallError, install_skill, uninstall_skill


def test_tool_loop_uses_persistent_cache(config, catalog, tmp_path):
    service = RoutingService(config, catalog, tmp_path)
    first = service.route(TaskContext("format file", session_id="s", task_id="t"))
    second = service.route(
        TaskContext(
            "proceed",
            session_id="s",
            task_id="t",
            event="tool_loop",
            relevant_context="format file",
        )
    )
    # Changed relevant context changes key; no unsafe hash of only "proceed".
    assert first.selected_profile is not None
    assert second.cached is False
    same = service.route(TaskContext("format file", session_id="s", task_id="t", event="tool_loop"))
    assert same.cached is True


def test_new_phase_reassesses(config, catalog, tmp_path):
    service = RoutingService(config, catalog, tmp_path)
    first = service.route(TaskContext("format", phase_id="one"))
    second = service.route(
        TaskContext("investigate architecture failure", phase_id="two", event="new_phase")
    )
    assert first.cached is False and second.cached is False


def test_skill_install_remove(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    installed = install_skill(source, tmp_path)
    assert Path(str(installed["target"]), "SKILL.md").exists()
    uninstall_skill(tmp_path)
    assert not Path(str(installed["target"])).exists()


def test_skill_install_dry_run_changes_nothing(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    result = install_skill(source, tmp_path, dry_run=True)
    assert result["dry_run"] is True
    assert not (tmp_path / "skills").exists()


def test_skill_refuses_unmanaged_overwrite(tmp_path):
    target = tmp_path / "skills" / "codex-smart-route"
    target.mkdir(parents=True)
    (target / "mine.txt").write_text("mine")
    with pytest.raises(SkillInstallError):
        install_skill(Path(__file__).parents[2] / "skill" / "codex-smart-route", tmp_path)


def test_skill_refuses_modified_uninstall(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    install_skill(source, tmp_path)
    target = tmp_path / "skills" / "codex-smart-route"
    (target / "SKILL.md").write_text("changed")
    with pytest.raises(SkillInstallError, match="modified"):
        uninstall_skill(tmp_path)
    assert target.exists()


def test_doctor_is_read_only(config, tmp_path):
    report = run_doctor(config, tmp_path, discover=False)
    assert report["global_changes_required"] is False
    assert not (tmp_path / "config.toml").exists()


def test_cli_route_json(config, catalog_path, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(tmp_path))
    code = main(
        ["--json", "route", "--catalog", str(catalog_path), "--task", "fix typo", "--dry-run"]
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out)["status"] == "selected"
    assert not (tmp_path / "cache.sqlite3").exists()
    assert not (tmp_path / "audit.jsonl").exists()


def test_cross_platform_path_objects():
    assert str(PureWindowsPath("C:/Users/A/.codex-smart-route/config.toml")).endswith("config.toml")
    assert str(PurePosixPath("/home/a/.codex-smart-route/config.toml")).endswith("config.toml")


def test_packaged_skill_matches_repository_skill():
    root = Path(__file__).parents[2]
    packaged = root / "codex_smart_route" / "bundled_skill" / "SKILL.md"
    repository = root / "skill" / "codex-smart-route" / "SKILL.md"
    assert packaged.read_text().strip() == repository.read_text().strip()
