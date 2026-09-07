from __future__ import annotations

import json
from pathlib import Path

from codex_smart_route import cli
from codex_smart_route.compatibility import detect_integrations


def test_smart_route_read_only_smoke(
    catalog_path: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    runtime = tmp_path / "runtime"
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(runtime))

    code = cli.main(
        [
            "route",
            "--catalog",
            str(catalog_path),
            "--task",
            "inspect files without modifying them",
            "--dry-run",
        ]
    )

    report = json.loads(capsys.readouterr().out)
    assert code == 0
    assert report["status"] == "selected"
    assert not (runtime / "audit.jsonl").exists()
    assert not (runtime / "cache.sqlite3").exists()


def test_smart_route_file_modifying_smoke(
    catalog_path: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    runtime = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(runtime))

    def fake_run(self, decision, task, cwd, extra_args, resume_session):
        assert decision.status == "selected"
        assert task == "create deterministic.txt"
        assert cwd == project
        assert extra_args == ()
        assert resume_session is None
        (cwd / "deterministic.txt").write_text("created", encoding="utf-8")
        return 0, decision

    monkeypatch.setattr(cli.CodexCliAdapter, "run", fake_run)
    code = cli.main(
        [
            "exec",
            "--catalog",
            str(catalog_path),
            "--task",
            "create deterministic.txt",
            "--cwd",
            str(project),
        ]
    )

    assert code == 0
    assert capsys.readouterr().out == ""
    assert (project / "deterministic.txt").read_text(encoding="utf-8") == "created"


def test_complete_combination_keeps_json_and_single_routing_owner(
    catalog_path: Path, tmp_path: Path, monkeypatch, capsys
) -> None:
    environment = {
        "RTK_ACTIVE": "1",
        "CAVEMAN_LEVEL": "full",
        "CAVECREW_ACTIVE": "1",
        "CAVECREW_MODEL_OWNER": "smart-route",
        "CODEX_SMART_ROUTE_MODEL_OWNER": "smart-route",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(tmp_path / "runtime"))

    code = cli.main(
        [
            "route",
            "--catalog",
            str(catalog_path),
            "--task",
            "Fix one deterministic typo. Keep response terse.",
            "--dry-run",
        ]
    )
    route = json.loads(capsys.readouterr().out)
    compatibility = detect_integrations(
        config_enabled=True,
        adapter="codex-cli",
        auto_slug="codex-smart-route",
        environ=environment,
        which=lambda name: "rtk" if name == "rtk" else None,
    )

    assert code == 0
    assert route["status"] == "selected"
    assert route["selected_model"]
    assert route["selected_reasoning_effort"]
    assert compatibility["routing_authorities"] == 1
    assert compatibility["main_model_owner"] == "smart-route"
    assert compatibility["warnings"] == []
