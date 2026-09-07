from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from codex_smart_route.adapters import CodexCliAdapter
from codex_smart_route.models import Decision
from codex_smart_route.router import RoutingError


def decision() -> Decision:
    return Decision("selected", "future-model", "high", "future-model@high", "auto")


SUPPORTED = frozenset(
    {"--image", "-i", "--sandbox", "--output-schema", "--json", "--config", "-c", "--profile"}
)


def adapter() -> CodexCliAdapter:
    return CodexCliAdapter("codex", SUPPORTED)


def test_pass_through_preserves_windows_paths_spaces_and_metacharacters() -> None:
    extra = (
        "--image",
        r"C:\work files\shot & notes.png",
        "--sandbox=workspace-write",
        "--output-schema",
        r"C:\schemas\result $(safe).json",
        "--json",
    )
    command = adapter().command(decision(), "inspect & report", Path(r"C:\work files"), extra)
    assert command[2 : 2 + len(extra)] == list(extra)
    assert command[-1] == "inspect & report"
    assert command[-3:-1] == ["-C", r"C:\work files"]


@pytest.mark.parametrize(
    "extra",
    [
        ("--model", "attacker"),
        ("-m", "attacker"),
        ("--config", 'model="attacker"'),
        ("-c", 'model_reasoning_effort="low"'),
        ("--config=model=attacker",),
        ("--image", "--model", "attacker"),
        ("task injected as positional",),
    ],
)
def test_routing_overrides_and_argument_injection_are_rejected(extra: tuple[str, ...]) -> None:
    with pytest.raises(RoutingError, match="controlled|unsupported|cannot start"):
        adapter().command(decision(), "safe task", extra_args=extra)


def test_installed_version_must_expose_allowlisted_option() -> None:
    old = CodexCliAdapter("codex", frozenset({"--json"}))
    with pytest.raises(RoutingError, match="does not support --image"):
        old.command(decision(), "inspect", extra_args=("--image", "shot.png"))


def test_resume_places_identity_before_prompt_and_routing_flags_last() -> None:
    command = adapter().command(
        decision(),
        "continue safely",
        extra_args=("--json", "--profile", "team"),
        resume_session="018f-session",
    )
    assert command[:3] == ["codex", "exec", "resume"]
    assert command[3:6] == ["--json", "--profile", "team"]
    assert command[-2:] == ["018f-session", "continue safely"]
    assert command[-6:-2] == ["-m", "future-model", "-c", 'model_reasoning_effort="high"']


def test_resume_rejects_cwd() -> None:
    with pytest.raises(RoutingError, match="session workspace"):
        adapter().command(decision(), "continue", Path("elsewhere"), resume_session="session")


def test_help_discovery_extracts_only_real_options(monkeypatch) -> None:
    def fake_run(command, **kwargs):
        assert command == ["codex", "exec", "resume", "--help"]
        return CompletedProcess(command, 0, "Options:\n  -i, --image <FILE>\n      --json\n", "")

    monkeypatch.setattr("codex_smart_route.adapters.subprocess.run", fake_run)
    assert CodexCliAdapter().supported_options(resume=True) == {"-i", "--image", "--json"}


def test_run_preserves_exit_code_and_inherited_streams(monkeypatch) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return CompletedProcess(command, 37)

    monkeypatch.setattr("codex_smart_route.adapters.subprocess.run", fake_run)
    code, _forwarded = adapter().run(decision(), "task")
    assert code == 37
    assert calls[0][1] == {"check": False}


def test_cancellation_is_not_swallowed(monkeypatch) -> None:
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("codex_smart_route.adapters.subprocess.run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        adapter().run(decision(), "task")
