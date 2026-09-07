from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from codex_smart_route.cli import main
from codex_smart_route.doctor import run_doctor
from codex_smart_route.models import TaskContext
from codex_smart_route.service import RoutingService
from codex_smart_route.skill_install import (
    MANIFEST_NAME,
    SkillInstallError,
    install_skill,
    skill_root_path,
    uninstall_skill,
)


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
    installed = install_skill(source, user_home=tmp_path)
    assert installed["scope"] == "user"
    assert installed["documented"] is True
    assert Path(str(installed["target"]), "SKILL.md").exists()
    uninstall_skill(user_home=tmp_path)
    assert not Path(str(installed["target"])).exists()


def test_skill_install_dry_run_changes_nothing(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    result = install_skill(source, dry_run=True, user_home=tmp_path)
    assert result["dry_run"] is True
    assert not (tmp_path / ".agents").exists()


def test_skill_install_validates_source_before_touching_target(tmp_path):
    with pytest.raises(SkillInstallError, match="source is invalid"):
        install_skill(tmp_path / "missing", user_home=tmp_path)
    assert not (tmp_path / ".agents").exists()


def test_skill_refuses_unmanaged_overwrite(tmp_path):
    target = tmp_path / ".agents" / "skills" / "codex-smart-route"
    target.mkdir(parents=True)
    (target / "mine.txt").write_text("mine")
    with pytest.raises(SkillInstallError):
        install_skill(
            Path(__file__).parents[2] / "skill" / "codex-smart-route",
            user_home=tmp_path,
        )


def test_skill_refuses_modified_uninstall(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    install_skill(source, user_home=tmp_path)
    target = tmp_path / ".agents" / "skills" / "codex-smart-route"
    (target / "SKILL.md").write_text("changed")
    with pytest.raises(SkillInstallError, match="modified"):
        uninstall_skill(user_home=tmp_path)
    assert target.exists()


def test_repo_scope_update_and_uninstall_preserve_unmanaged_files(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    marker = tmp_path / ".agents" / "team-owned.txt"
    marker.parent.mkdir()
    marker.write_text("keep", encoding="utf-8")
    installed = install_skill(source, scope="repo", repo=tmp_path)
    assert Path(str(installed["target"])) == (tmp_path / ".agents" / "skills" / "codex-smart-route")
    updated = install_skill(source, scope="repo", repo=tmp_path)
    assert updated["action"] == "update"
    uninstall_skill(scope="repo", repo=tmp_path)
    assert marker.read_text(encoding="utf-8") == "keep"
    assert (tmp_path / ".agents" / "skills").is_dir()


def test_legacy_scope_is_explicit_and_unverified(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    installed = install_skill(source, scope="legacy", codex_home=tmp_path)
    assert installed["target"] == str(tmp_path / "skills" / "codex-smart-route")
    assert installed["documented"] is False
    assert installed["compatibility"] == "explicit-unverified"
    uninstall_skill(scope="legacy", codex_home=tmp_path)


def test_manifest_cannot_claim_different_scope(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    installed = install_skill(source, user_home=tmp_path)
    manifest_path = Path(str(installed["target"])) / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["scope"] = "repo"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(SkillInstallError, match="does not own"):
        uninstall_skill(user_home=tmp_path)


def test_repo_scope_requires_existing_directory(tmp_path):
    source = Path(__file__).parents[2] / "skill" / "codex-smart-route"
    with pytest.raises(SkillInstallError, match="not a directory"):
        install_skill(source, scope="repo", repo=tmp_path / "missing")


def test_doctor_is_read_only(config, tmp_path):
    report = run_doctor(
        config,
        tmp_path / ".codex",
        discover=False,
        skill_scope="repo",
        repo=tmp_path,
        user_home=tmp_path,
    )
    assert report["global_changes_required"] is False
    assert report["skill_scope"] == "repo"
    assert report["selected_skill_root"] == str(tmp_path / ".agents" / "skills")
    assert report["legacy_compatibility"] == "not-detected-unverified"
    assert {item["scope"] for item in report["skill_roots"]} == {"user", "repo", "legacy"}
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


@pytest.mark.parametrize("command", ["models", "profiles", "route", "exec"])
def test_cli_commands_use_configured_catalog(command, catalog_path, tmp_path, monkeypatch, capsys):
    router_home = tmp_path / "router-home"
    router_home.mkdir()
    (router_home / "config.toml").write_text(
        f'[catalog]\nstrategy = "file"\npath = "{catalog_path.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(router_home))
    arguments = [command]
    if command in {"route", "exec"}:
        arguments.extend(["--task", "fix typo", "--dry-run"])

    assert main(arguments) == 0
    assert json.loads(capsys.readouterr().out)


def test_cli_catalog_flag_overrides_configured_catalog(catalog_path, tmp_path, monkeypatch, capsys):
    router_home = tmp_path / "router-home"
    router_home.mkdir()
    (router_home / "config.toml").write_text(
        '[catalog]\nstrategy = "file"\npath = "missing.json"\n', encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(router_home))

    assert main(["models", "--catalog", str(catalog_path)]) == 0
    assert json.loads(capsys.readouterr().out)


def test_cli_config_sources_and_doctor_show_project_identity(
    catalog_path, tmp_path, monkeypatch, capsys
):
    router_home = tmp_path / "router-home"
    router_home.mkdir()
    (router_home / "config.toml").write_text(
        f'[catalog]\nstrategy = "file"\npath = "{catalog_path.as_posix()}"\n',
        encoding="utf-8",
    )
    repo = tmp_path / "repo"
    nested = repo / "src"
    nested.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / ".codex-smart-route.toml").write_text('active_policy = "economy"')
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(router_home))
    monkeypatch.chdir(nested)

    assert main(["config", "sources"]) == 0
    sources = json.loads(capsys.readouterr().out)
    assert [item["kind"] for item in sources["sources"]] == [
        "built-in",
        "user",
        "repository",
    ]
    assert sources["project_root"] == str(repo)
    assert sources["project_id"]
    assert sources["state_directory"].startswith(str(router_home / "projects"))

    monkeypatch.setattr("codex_smart_route.doctor.shutil.which", lambda _: None)
    assert main(["doctor", "--scope", "repo", "--repo", str(repo)]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["project_id"] == sources["project_id"]
    assert doctor["config_sources"] == sources["sources"]


def test_cross_platform_path_objects():
    windows_user = skill_root_path("user", PureWindowsPath("C:/Users/A"))
    windows_repo = skill_root_path("repo", PureWindowsPath("D:/src/project"))
    posix_user = skill_root_path("user", PurePosixPath("/home/a"))
    posix_repo = skill_root_path("repo", PurePosixPath("/src/project"))
    assert windows_user == PureWindowsPath("C:/Users/A/.agents/skills")
    assert windows_repo == PureWindowsPath("D:/src/project/.agents/skills")
    assert posix_user == PurePosixPath("/home/a/.agents/skills")
    assert posix_repo == PurePosixPath("/src/project/.agents/skills")


def test_cli_repo_scope_dry_run_and_validation(config, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(tmp_path / "router"))
    code = main(["install-skill", "--scope", "repo", "--repo", str(tmp_path), "--dry-run"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["scope"] == "repo"
    assert report["target"] == str(tmp_path / ".agents" / "skills" / "codex-smart-route")
    assert not (tmp_path / ".agents").exists()

    code = main(["install-skill", "--scope", "user", "--repo", str(tmp_path)])
    assert code == 1
    assert "valid only with --scope repo" in capsys.readouterr().err


def test_packaged_skill_matches_repository_skill():
    root = Path(__file__).parents[2]
    packaged = root / "codex_smart_route" / "bundled_skill" / "SKILL.md"
    repository = root / "skill" / "codex-smart-route" / "SKILL.md"
    assert packaged.read_text().strip() == repository.read_text().strip()
    packaged_yaml = root / "codex_smart_route" / "bundled_skill" / "agents" / "openai.yaml"
    repository_yaml = root / "skill" / "codex-smart-route" / "agents" / "openai.yaml"
    assert packaged_yaml.read_text() == repository_yaml.read_text()
    assert "allow_implicit_invocation: false" in packaged_yaml.read_text()
