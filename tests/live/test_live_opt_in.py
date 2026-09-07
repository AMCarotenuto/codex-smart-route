"""Real provider test. Collection and ordinary CI never trigger live work."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_smart_route.live_validation import main


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("SMART_ROUTE_LIVE") != "1",
    reason="explicit SMART_ROUTE_LIVE=1 consent required",
)
def test_real_codex_profiles(tmp_path: Path) -> None:
    """Still requires command-level consent to prevent ambient-env accidents."""
    if os.environ.get("SMART_ROUTE_LIVE_PYTEST_CONSENT") != "1":
        pytest.skip("set SMART_ROUTE_LIVE_PYTEST_CONSENT=1 for billable pytest execution")
    args = ["--consent-live", "--report", str(tmp_path / "live-report.json")]
    catalog = os.environ.get("SMART_ROUTE_LIVE_CATALOG")
    if catalog:
        args.extend(["--catalog", catalog])
    if os.environ.get("SMART_ROUTE_LIVE_APP_SERVER") == "1":
        args.append("--include-app-server")
    assert main(args) == 0
