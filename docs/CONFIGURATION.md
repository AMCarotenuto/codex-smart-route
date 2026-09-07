# Configuration

## Layers and precedence

Smart Route resolves configuration from lowest to highest precedence:

1. built-in defaults;
2. user config at `~/.codex-smart-route/config.toml`;
3. nearest `.codex-smart-route.toml`, searched from working directory up to Git root;
4. `CODEX_SMART_ROUTE_*` environment overrides;
5. explicit command flags such as `--catalog`.

`CODEX_SMART_ROUTE_HOME` changes user config and runtime-data root. `--config-file FILE`
is an explicit single-file mode: user, repository, and environment configuration layers are not
loaded. Built-in defaults still fill omitted fields. This keeps automation and tests isolated.

Repository tables merge recursively over user tables. Scalar and array values replace lower-layer
values. This lets a repository change only `active_policy`, one policy field, or one model capability
without copying user config. When multiple repository config files exist below Git root, nearest file
wins and only that repository layer is loaded.

Supported environment overrides:

- `CODEX_SMART_ROUTE_ACTIVE_POLICY`
- `CODEX_SMART_ROUTE_CATALOG_STRATEGY`
- `CODEX_SMART_ROUTE_CATALOG_PATH`
- `CODEX_SMART_ROUTE_ALLOWED_MODELS` (comma-separated)
- `CODEX_SMART_ROUTE_BLOCKED_MODELS` (comma-separated)
- `CODEX_SMART_ROUTE_ALLOWED_REASONING` (comma-separated)

Use `smart-route config sources` to print loaded origins, Git root, project identity, and active
runtime directory. `smart-route doctor` reports same context. `smart-route config validate` parses
without mutation; `smart-route config show` prints merged non-secret settings.

## Catalog source

Default strategy is live Codex App Server discovery. Persist a catalog choice in user or repository
config:

```toml
[catalog]
strategy = "file"
path = "local/capabilities.json"
```

Relative catalog paths resolve against config file containing them. Configured source is used by
`models`, `profiles`, `route`, `exec`, and `app-server`; `--catalog FILE` remains highest-precedence
one-command override.

## Runtime isolation and safe repository data

Inside Git repository, state, cache, and audit live under
`~/.codex-smart-route/projects/<project-id>/`. Project ID is stable SHA-256 prefix derived from
normalized absolute Git-root path; path itself is shown by diagnostics but not written to audit.
Outside Git repository, runtime data stays directly under `~/.codex-smart-route/`.

`.codex-smart-route.toml` may be committed when it contains shared policy only. Never put credentials
in config or catalogs. Keep machine-local catalogs and private data ignored, for example:

```gitignore
.codex-smart-route.local.toml
local/capabilities.json
*.sqlite3
*.log
```

Copy `examples/config.example.toml` as starting point.

## Fields

Top-level fields select policy, Auto slug, adapter, cache TTL, hysteresis, model/reasoning allowlists and blocklists, explicit fallback, unknown-capability behavior, logging, and experimental App Server support. `policies.<name>` supports custom normalized weights, quality floor, and version. `capability_overrides."MODEL"` may set only fields defined by `ModelCapability`.

Repository catalog values override bundled family priors; `capability_overrides."MODEL"` in the
selected user config override both. Prior fields include relative metrics, `prior_strengths`,
`prior_weaknesses`, `prior_source`, `prior_confidence`, `prior_version`, and
`tie_break_priority`. See [Model priors and capability cards](MODEL_PRIORS.md).

Classifier modes: `disabled` (default), `local`, and `remote`. Remote mode requires explicit fixed
model and endpoint; credentials remain transport/environment responsibility and must not be stored
in this file. Auto slug is rejected as classifier model. Set `classifier.version` whenever classifier
prompt, schema, model revision, or scoring semantics change; cache identity includes that version
when remote classification is used.
