from __future__ import annotations

from pathlib import Path

import pytest

from codex_smart_route.config import parse_config
from codex_smart_route.discovery import capabilities_from_file


@pytest.fixture
def catalog_path() -> Path:
    return Path(__file__).parents[1] / "examples" / "capabilities.example.json"


@pytest.fixture
def catalog(catalog_path: Path):
    return capabilities_from_file(catalog_path)


@pytest.fixture
def config():
    return parse_config({})
