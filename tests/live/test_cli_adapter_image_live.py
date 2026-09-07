from __future__ import annotations

import os
from pathlib import Path

import pytest

from codex_smart_route.adapters import CodexCliAdapter
from codex_smart_route.models import Decision


@pytest.mark.live
def test_opt_in_image_capable_invocation() -> None:
    if os.environ.get("SMART_ROUTE_LIVE") != "1":
        pytest.skip("set SMART_ROUTE_LIVE=1 to authorize a billable model call")
    image = os.environ.get("SMART_ROUTE_LIVE_IMAGE")
    model = os.environ.get("SMART_ROUTE_LIVE_MODEL")
    if not image or not model:
        pytest.skip("set SMART_ROUTE_LIVE_IMAGE and SMART_ROUTE_LIVE_MODEL")
    image_path = Path(image)
    if not image_path.is_file():
        pytest.fail("SMART_ROUTE_LIVE_IMAGE is not a file")
    decision = Decision("selected", model, "low", f"{model}@low", "codex-smart-route")
    code, forwarded = CodexCliAdapter().run(
        decision,
        "Describe this image in one sentence.",
        extra_args=("--image", str(image_path), "--ephemeral"),
    )
    assert code == 0
    assert forwarded.forwarded_model == model
