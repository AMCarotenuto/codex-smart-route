# Codex integration

## Supported CLI path

`smart-route exec` selects profile before spawning `codex exec`, passing `-m MODEL` and `-c model_reasoning_effort="EFFORT"`. Codex owns authentication and complete agent loop. Smart Route does not read or copy tokens.

## Experimental App Server path

Codex CLI `0.153.4` generated JSON Schema with `model/list`, `turn/start.model`, and `turn/start.effort`. `JsonLineAppServerProxy` intercepts JSONL `turn/start`; if Auto is requested, it patches both fields and forwards remaining payload. `thread/start` Auto marker is removed so concrete choice happens at turn boundary. Tool outputs, raw items, compaction, cancellation, and streaming notifications otherwise pass unchanged.

Start an explicitly configured proxy with `smart-route app-server --catalog capabilities.json`. App Server integration does not accept upstream URLs from task text.

Protocol may change. Desktop picker injection is not implemented or claimed. No ASAR patch, undocumented authentication endpoint, or token passthrough exists.

Evidence fields remain separate: requested Auto entry, selected profile, forwarded pair, and confirmed pair. Confirmation stays `unverified` unless runtime response supplies trustworthy metadata.

Regenerate current schema with:

```bash
codex app-server generate-json-schema --out PATH --experimental
```
