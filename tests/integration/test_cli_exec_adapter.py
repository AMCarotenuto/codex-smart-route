from __future__ import annotations

import io

from codex_smart_route import cli
from codex_smart_route.models import TaskContext


def test_exec_reads_stdin_and_forwards_image_args(catalog_path, tmp_path, monkeypatch) -> None:
    recorded: dict[str, object] = {}

    def fake_run(self, decision, task, cwd, extra_args, resume_session):
        recorded.update(task=task, cwd=cwd, extra_args=extra_args, resume=resume_session)
        return 23, decision

    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO("inspect piped input & preserve it"))
    monkeypatch.setattr(cli.CodexCliAdapter, "run", fake_run)
    code = cli.main(
        [
            "exec",
            "--catalog",
            str(catalog_path),
            "--image",
            "--",
            "--image",
            r"C:\work files\shot.png",
            "--json",
        ]
    )
    assert code == 23
    assert recorded == {
        "task": "inspect piped input & preserve it",
        "cwd": None,
        "extra_args": ("--image", r"C:\work files\shot.png", "--json"),
        "resume": None,
    }


def test_resume_profile_is_kept_until_explicit_safe_reevaluation() -> None:
    parser = cli.build_parser()
    kept = parser.parse_args(
        [
            "exec",
            "--resume",
            "thread-1",
            "--current-profile",
            "future-model@high",
            "--task",
            "continue",
        ]
    )
    context = cli._context(kept, {})
    assert context == TaskContext(
        "continue",
        session_id="thread-1",
        event="continuation",
        manual_profile="future-model@high",
        current_profile="future-model@high",
    )

    reevaluated = parser.parse_args(
        ["exec", "--resume", "thread-1", "--reevaluate-resume", "--task", "continue"]
    )
    context = cli._context(reevaluated, {})
    assert context.manual_profile is None
    assert context.force_reevaluation is True


def test_task_sources_are_mutually_exclusive(tmp_path) -> None:
    parser = cli.build_parser()
    task_file = tmp_path / "task.txt"
    task_file.write_text("task", encoding="utf-8")
    try:
        parser.parse_args(["exec", "--task", "one", "--task-file", str(task_file)])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("parser accepted two task sources")


def test_exec_does_not_pollute_codex_stdout(catalog_path, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("CODEX_SMART_ROUTE_HOME", str(tmp_path))
    monkeypatch.setattr(cli.CodexCliAdapter, "run", lambda *args: (0, args[1]))
    code = cli.main(["exec", "--catalog", str(catalog_path), "--task", "fix typo"])
    assert code == 0
    assert capsys.readouterr().out == ""
