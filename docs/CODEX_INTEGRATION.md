# Codex integration

## Supported CLI path

`smart-route exec` selects profile before spawning `codex exec`, passing `-m MODEL` and `-c model_reasoning_effort="EFFORT"`. Codex owns authentication and complete agent loop. Smart Route does not read or copy tokens.

Task input accepts `--task`, `--task-file`, or piped stdin. Arguments after `--` pass unchanged to Codex when both allowlisted and exposed by installed `codex exec --help`. Unsupported flags fail explicitly. `-m`, `--model`, and `-c/--config` assignments to `model` or `model_reasoning_effort` are rejected; Smart Route's selected pair is always applied last. Subprocess stdio stays inherited, so output streams directly and Codex exit code becomes `smart-route` exit code.

```bash
smart-route exec --task "Inspect screenshot" -- --image "C:\work files\screen.png" --sandbox workspace-write
Get-Content task.txt | smart-route exec --cwd "C:\work files\project" -- --json
smart-route exec --task "Return JSON" -- --output-schema schema.json -o result.json
```

Resume always names exact session/thread. Default keeps explicit profile; use `--current-profile MODEL@EFFORT` (or manual `--profile`) to prevent a mid-session change. `--reevaluate-resume` permits routing once at this new-turn boundary. Resume retains session workspace, so `--cwd` is rejected.

```bash
smart-route exec --resume SESSION_ID --current-profile MODEL@EFFORT --task "Continue"
smart-route exec --resume SESSION_ID --reevaluate-resume --task "Start next phase" -- --json
```

## Experimental App Server path

Codex CLI `0.153.4` generated JSON Schema with `model/list`, `turn/start.model`, and `turn/start.effort`. `JsonLineAppServerProxy` intercepts JSONL `turn/start`; if Auto is requested, it patches both fields and forwards remaining payload. At `thread/start`, proxy removes virtual marker and correlates response runtime thread ID with explicit Auto request. Only later model-less turns owned by that thread are routed. Model-less turns from ordinary threads pass through untouched. Explicit concrete model disables Auto for that thread; close, archive, delete, and shutdown events clear retained state. Missing or malformed thread IDs never enable global Auto. Tool outputs, raw items, compaction, cancellation, and streaming notifications otherwise pass unchanged.

Start an explicitly configured proxy with `smart-route app-server --catalog capabilities.json`. App Server integration does not accept upstream URLs from task text.

Protocol may change. Desktop picker injection is not implemented or claimed. No ASAR patch, undocumented authentication endpoint, or token passthrough exists.

Evidence fields remain separate: requested Auto entry, selected profile, forwarded pair, and confirmed pair. Confirmation stays `unverified` unless runtime response supplies trustworthy metadata.

Regenerate current schema with:

```bash
codex app-server generate-json-schema --out PATH --experimental
```
