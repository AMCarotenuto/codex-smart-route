from __future__ import annotations

import json
from pathlib import Path

from codex_smart_route.compatibility import compatibility_matrix, detect_integrations
from codex_smart_route.doctor import run_doctor


def test_detection_uses_paths_and_marker_names_without_exposing_values(tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    repo = tmp_path / "repo"
    for root, skill in ((user_home, "caveman"), (repo, "cavecrew")):
        manifest = root / ".agents" / "skills" / skill / "SKILL.md"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("secret-like-content-must-not-be-read", encoding="utf-8")

    environment = {
        "RTK_ACTIVE": "token-that-must-not-appear",
        "CAVEMAN_LEVEL": "full",
        "CAVECREW_MODEL_OWNER": "codex-smart-route",
        "UNRELATED_SECRET": "never-return-this",
    }
    report = detect_integrations(
        config_enabled=True,
        adapter="codex-cli",
        auto_slug="codex-smart-route",
        project_root=repo,
        user_home=user_home,
        environ=environment,
        which=lambda name: "/tools/rtk" if name == "rtk" else None,
    )

    encoded = json.dumps(report)
    assert "token-that-must-not-appear" not in encoded
    assert "never-return-this" not in encoded
    assert "secret-like-content-must-not-be-read" not in encoded
    assert report["integrations"]["rtk"]["detected"] is True
    assert report["integrations"]["caveman"]["detected"] is True
    assert report["integrations"]["cavecrew"]["detected"] is True
    assert report["main_model_owner"] == "smart-route"
    assert report["routing_authorities"] == 1
    assert report["warnings"] == []


def test_obvious_recursive_and_double_routing_configs_warn() -> None:
    report = detect_integrations(
        config_enabled=True,
        adapter="codex-smart-route",
        auto_slug="auto-local",
        environ={
            "CODEX_SMART_ROUTE_WRAPPER_CHAIN": "rtk,codex-smart-route,codex",
            "CODEX_SMART_ROUTE_MODEL_OWNER": "another-router",
            "CAVECREW_MODEL_OWNER": "auto-local",
        },
        which=lambda _name: None,
    )

    assert report["routing_authorities"] == 1
    assert len(report["warnings"]) == 5
    assert any("recursively" in warning for warning in report["warnings"])
    assert any("WRAPPER_CHAIN" in warning for warning in report["warnings"])
    assert any("MODEL_OWNER" in warning for warning in report["warnings"])


def test_disabled_router_yields_external_or_manual_owner() -> None:
    report = detect_integrations(
        config_enabled=False,
        adapter="codex-cli",
        auto_slug="codex-smart-route",
        environ={"CAVECREW_MODEL_OWNER": "cavecrew"},
        which=lambda _name: None,
    )
    assert report["main_model_owner"] == "external-or-manual"
    assert report["routing_authorities"] == 0
    assert report["warnings"] == []


def test_matrix_has_unique_scenarios_and_one_owner() -> None:
    matrix = compatibility_matrix()
    assert len(matrix) == 5
    assert len({row["scenario"] for row in matrix}) == len(matrix)
    assert {row["owner"] for row in matrix} == {"smart-route"}
    assert matrix[-1]["scenario"] == "smart-route+rtk+caveman+cavecrew"


def test_doctor_embeds_compatibility_without_discovery(config, tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("codex_smart_route.compatibility.shutil.which", lambda _name: None)
    report = run_doctor(
        config,
        discover=False,
        user_home=tmp_path / "user",
        project_root=tmp_path / "repo",
    )
    assert report["compatibility"]["inspection"] == "paths-and-marker-names-only"
    assert report["compatibility"]["main_model_owner"] == "smart-route"
    assert len(report["compatibility"]["matrix"]) == 5
