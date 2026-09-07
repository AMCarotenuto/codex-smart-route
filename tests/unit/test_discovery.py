from __future__ import annotations

import json

import pytest

from codex_smart_route.config import parse_config
from codex_smart_route.discovery import (
    DiscoveryError,
    apply_overrides,
    capabilities_from_app_server,
    capabilities_from_file,
)


def test_app_server_catalog_is_dynamic():
    result = capabilities_from_app_server(
        {
            "data": [
                {
                    "model": "future-model",
                    "displayName": "Future",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "new-effort", "description": ""}
                    ],
                    "inputModalities": ["text", "image"],
                }
            ]
        }
    )
    assert result[0].model == "future-model"
    assert result[0].reasoning_efforts == ("new-effort",)
    assert result[0].supports_tools is None


def test_app_server_empty_catalog_rejected():
    with pytest.raises(DiscoveryError):
        capabilities_from_app_server({"data": []})


def test_file_catalog_requires_efforts(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps([{"model": "x"}]))
    with pytest.raises(DiscoveryError):
        capabilities_from_file(path)


def test_capability_override_marks_concrete_fields(catalog):
    config = parse_config(
        {
            "capability_overrides": {
                catalog[0].model: {"supports_tools": False, "context_window": 99}
            }
        }
    )
    result = apply_overrides(catalog, config)
    assert result[0].supports_tools is False
    assert result[0].context_window == 99


def test_unknown_override_rejected(catalog):
    config = parse_config({"capability_overrides": {catalog[0].model: {"fiction": True}}})
    with pytest.raises(DiscoveryError):
        apply_overrides(catalog, config)


def test_profiles_are_model_plus_effort(catalog):
    profiles = catalog[0].profiles()
    assert all(profile.id == f"{profile.model}@{profile.effort}" for profile in profiles)
