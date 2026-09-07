"""`smart-route` command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal, cast

from . import __version__
from .adapters import CodexCliAdapter, JsonLineAppServerProxy
from .config import ConfigError, config_as_dict, default_config_path, default_home, load_config
from .discovery import AppServerDiscovery, DiscoveryError, apply_overrides, capabilities_from_file
from .doctor import run_doctor
from .models import Decision, TaskContext
from .priors import apply_priors
from .service import RoutingService
from .skill_install import SkillInstallError, SkillScope, install_skill, uninstall_skill
from .state import RuntimeState


def _json(value: Any) -> None:
    print(json.dumps(value, indent=2, default=list, ensure_ascii=False))


def _task(args: argparse.Namespace) -> str:
    if args.task is not None:
        return cast(str, args.task)
    if args.task_file is not None:
        return cast(Path, args.task_file).read_text(encoding="utf-8")
    raise ConfigError("route requires --task or --task-file")


def _catalog(args: argparse.Namespace, config: Any, *, enrich: bool = True) -> list[Any]:
    if args.catalog:
        models = capabilities_from_file(args.catalog)
    else:
        models = AppServerDiscovery().discover()
    return apply_overrides(apply_priors(models), config) if enrich else models


def _context(args: argparse.Namespace, state: dict[str, Any]) -> TaskContext:
    task = _task(args)
    modalities = frozenset(["text", "image"] if args.image else ["text"])
    return TaskContext(
        task=task,
        session_id=args.session,
        task_id=args.task_id,
        phase_id=args.phase,
        event=args.event,
        required_modalities=modalities,
        tools_required=args.tools,
        estimated_context_tokens=args.context_tokens,
        manual_profile=args.profile or state.get("manual_override"),
        current_profile=args.current_profile,
        force_reevaluation=bool(state.get("reevaluate_next")),
    )


def _input_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (_input_text(item) for item in value)))
    if isinstance(value, dict):
        return "\n".join(
            filter(None, (_input_text(value.get(key)) for key in ("text", "content", "input")))
        )
    return ""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smart-route", description="Automatic model + reasoning router for Codex"
    )
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config-file", type=Path, default=default_config_path())
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--scope", choices=["user", "repo", "legacy"], default="user")
    doctor.add_argument("--repo", type=Path)
    doctor.add_argument("--codex-home", type=Path)
    sub.add_parser("models").add_argument("--catalog", type=Path)
    sub.add_parser("profiles").add_argument("--catalog", type=Path)
    route = sub.add_parser("route")
    execute = sub.add_parser("exec")
    for command in (route, execute):
        command.add_argument("--task")
        command.add_argument("--task-file", type=Path)
        command.add_argument("--catalog", type=Path)
        command.add_argument("--session", default="local")
        command.add_argument("--task-id", default="task")
        command.add_argument("--phase", default="initial")
        command.add_argument(
            "--event",
            choices=["new_task", "new_phase", "tool_loop", "continuation"],
            default="new_task",
        )
        command.add_argument("--image", action="store_true")
        command.add_argument("--tools", action="store_true")
        command.add_argument("--context-tokens", type=int, default=0)
        command.add_argument("--profile")
        command.add_argument("--current-profile")
        command.add_argument("--dry-run", action="store_true")
    execute.add_argument("--cwd", type=Path)
    sub.add_parser("explain")
    sub.add_parser("status")
    sub.add_parser("enable")
    sub.add_parser("disable")
    policy = sub.add_parser("policy")
    policy.add_argument("name", choices=["economy", "balanced", "quality"])
    override = sub.add_parser("override")
    override.add_argument("profile", nargs="?")
    sub.add_parser("reevaluate")
    config_cmd = sub.add_parser("config")
    config_cmd.add_argument("action", choices=["validate", "show"])
    cache = sub.add_parser("cache")
    cache.add_argument("action", choices=["clear"])
    logs = sub.add_parser("logs")
    logs.add_argument("--limit", type=int, default=20)
    install = sub.add_parser("install-skill")
    install.add_argument("--scope", choices=["user", "repo", "legacy"], default="user")
    install.add_argument("--repo", type=Path)
    install.add_argument("--codex-home", type=Path)
    install.add_argument("--dry-run", action="store_true")
    uninstall = sub.add_parser("uninstall-skill")
    uninstall.add_argument("--scope", choices=["user", "repo", "legacy"], default="user")
    uninstall.add_argument("--repo", type=Path)
    uninstall.add_argument("--codex-home", type=Path)
    uninstall.add_argument("--dry-run", action="store_true")
    app_server = sub.add_parser("app-server")
    app_server.add_argument("--catalog", type=Path, required=True)
    for command_parser in sub.choices.values():
        command_parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config_file)
        home = default_home()
        state_store = RuntimeState(home / "state.json")
        state = state_store.read()
        if args.command == "doctor":
            scope = cast(SkillScope, args.scope)
            if args.repo is not None and scope != "repo":
                raise ConfigError("--repo is valid only with --scope repo")
            if args.codex_home is not None and scope != "legacy":
                raise ConfigError("--codex-home is valid only with --scope legacy")
            _json(
                run_doctor(
                    config,
                    args.codex_home,
                    skill_scope=scope,
                    repo=args.repo,
                )
            )
        elif args.command in {"models", "profiles"}:
            models = _catalog(args, config)
            rows = (
                [model.to_dict() for model in models]
                if args.command == "models"
                else [profile.to_dict() for model in models for profile in model.profiles()]
            )
            _json(rows)
        elif args.command in {"route", "exec"}:
            models = _catalog(args, config, enrich=False)
            active_policy = state.get("policy", config.active_policy)
            if active_policy in config.policies:
                config = type(config)(
                    **{
                        **config.__dict__,
                        "active_policy": active_policy,
                        "enabled": bool(state.get("enabled", True)),
                    }
                )
            service = RoutingService(config, models, home)
            context = _context(args, state)
            decision = service.route(context, dry_run=args.dry_run)
            if state.get("reevaluate_next") and not args.dry_run:
                state_store.update(reevaluate_next=False)
            _json(decision.to_dict())
            if args.command == "exec" and not args.dry_run:
                code, forwarded = CodexCliAdapter().run(decision, context.task, args.cwd)
                service.audit.decision(context, forwarded)
                return code
            return 0 if decision.status == "selected" else 2
        elif args.command == "explain":
            path = home / "audit.jsonl"
            print(
                path.read_text(encoding="utf-8").splitlines()[-1]
                if path.exists()
                else "No decision recorded."
            )
        elif args.command == "status":
            _json(state)
        elif args.command in {"enable", "disable"}:
            _json(state_store.update(enabled=args.command == "enable"))
        elif args.command == "policy":
            _json(state_store.update(policy=args.name))
        elif args.command == "override":
            _json(state_store.update(manual_override=args.profile))
        elif args.command == "reevaluate":
            _json(state_store.update(reevaluate_next=True))
        elif args.command == "config":
            _json({"valid": True} if args.action == "validate" else config_as_dict(config))
        elif args.command == "cache":
            models_path = Path("examples/capabilities.example.json")
            if models_path.exists():
                RoutingService.from_catalog_file(config, models_path, home).clear_cache()
            elif (home / "cache.sqlite3").exists():
                (home / "cache.sqlite3").unlink()
            _json({"cleared": True})
        elif args.command == "logs":
            path = home / "audit.jsonl"
            lines = (
                path.read_text(encoding="utf-8").splitlines()[-args.limit :]
                if path.exists()
                else []
            )
            print("\n".join(lines))
        elif args.command == "app-server":
            models = capabilities_from_file(args.catalog)
            service = RoutingService(config, models, home)
            tasks: dict[str, str] = {}

            def route_turn(params: dict[str, Any]) -> Decision:
                thread_id = str(params.get("threadId", "unknown"))
                current = _input_text(params.get("input", []))
                if current:
                    tasks[thread_id] = current
                task_text = tasks.get(thread_id, "continuation")
                event: Literal["new_task", "new_phase", "tool_loop", "continuation"] = (
                    "tool_loop"
                    if params.get("toolOutput") is not None or not current
                    else "new_task"
                )
                return service.route(
                    TaskContext(
                        task_text,
                        session_id=thread_id,
                        task_id=thread_id,
                        event=event,
                        tools_required=bool(params.get("toolOutput")),
                    )
                )

            return JsonLineAppServerProxy(route_turn, auto_slug=config.auto_slug).run()
        elif args.command in {"install-skill", "uninstall-skill"}:
            scope = cast(SkillScope, args.scope)
            if args.repo is not None and scope != "repo":
                raise ConfigError("--repo is valid only with --scope repo")
            if args.codex_home is not None and scope != "legacy":
                raise ConfigError("--codex-home is valid only with --scope legacy")
            if args.command == "install-skill":
                source = Path(__file__).resolve().parent / "bundled_skill"
                _json(
                    install_skill(
                        source,
                        args.dry_run,
                        scope=scope,
                        repo=args.repo,
                        codex_home=args.codex_home,
                    )
                )
            else:
                _json(
                    uninstall_skill(
                        args.dry_run,
                        scope=scope,
                        repo=args.repo,
                        codex_home=args.codex_home,
                    )
                )
        return 0
    except (
        ConfigError,
        DiscoveryError,
        SkillInstallError,
        OSError,
        ValueError,
        RuntimeError,
    ) as exc:
        print(f"smart-route: {exc}", file=sys.stderr)
        return 1
