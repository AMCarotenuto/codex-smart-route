"""Offline demonstration using fictional capability values."""

from pathlib import Path

from codex_smart_route.config import parse_config
from codex_smart_route.discovery import capabilities_from_file
from codex_smart_route.models import TaskContext
from codex_smart_route.service import RoutingService

root = Path(__file__).parent
service = RoutingService(
    parse_config({}),
    capabilities_from_file(root / "capabilities.example.json"),
    root / ".demo-state",
)
for task in (
    "Fix a typo",
    "Investigate a subtle regression across multiple modules",
    "Inspect this image",
):
    context = TaskContext(
        task,
        task_id=task,
        required_modalities=frozenset({"text", "image"})
        if "image" in task
        else frozenset({"text"}),
    )
    decision = service.route(context, dry_run=True)
    print(f"{task}: {decision.selected_profile or decision.reason}")
