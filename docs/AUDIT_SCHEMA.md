# Decision audit and explain

Smart Route writes one JSON object per routing decision to `audit.jsonl`. Schema
`1.0` is designed for privacy-preserving debugging: it stores identifiers and task context only
as SHA-256 fingerprints. It never stores raw task/context text, classifier prompts, model output,
commands, arguments, failure text, credentials, tokens, cookies, or repository paths.

Use:

```console
smart-route explain
smart-route explain --json
smart-route logs --limit 20
```

Human output answers why model and reasoning effort won, then lists nearest viable alternatives,
hard-gate exclusions, cache state, hysteresis state, reevaluation trigger, and policy. JSON output
returns latest complete audit object unchanged.

## Schema `1.0`

Every event has `schema_version: "1.0"` and `event_type: "routing.decision"`.

- `session_hash`, `task_hash`, `phase_hash`, and `context_fingerprint` are opaque hashes.
- `models` contains selected, requested, forwarded, and confirmed model/reasoning-effort pairs.
  Missing transport confirmation is the literal `"unverified"`, never inferred.
- `candidates` contains every viable profile's suitability, confidence, normalized total penalty,
  normalized penalty components, evidence sources, verification state, and prior/tie-break data.
  Candidate order is routing order: lowest penalty, declared prior priority, stable profile hash.
- `excluded` maps excluded profiles to explicit hard-gate reasons; `hard_gates` is their union.
- `evidence.evaluator` identifies evaluator/classifier source and version.
- `evidence.catalog` records per-model capability/prior versions and full capability-card
  fingerprint as `card_version`.
- `evidence.cache` records `hit`, `miss`, `bypassed`, `not-eligible`, or `disabled` and
  a 16-character cache-identity prefix.
- `evidence.hysteresis` records whether previous profile was retained, previous profile, and
  configured threshold.
- `evidence.reevaluation_triggers` lists `explicit` and/or
  `significant-model-failure`; singular `reevaluation_trigger` is retained when exactly one
  applies. Failure content is never stored.
- `evidence.policy` records name, version, quality floor, and normalized weights.
- `evidence.runtime` records adapter, Smart Route version, Python version, and OS family.
- `decision` records constant router reason, confidence, and verification state.

Top-level `selected`, `requested`, `forwarded`, `confirmed`, `reason`, `cached`, `policy`,
`policy_version`, and `verified` remain for existing `0.x` consumers.

## Compatibility for `0.x`

`schema_version` belongs to audit format, independent from package version. Consumers must parse
major version before interpreting fields, reject unsupported major versions, and ignore unknown
fields within supported major version. Minor schema releases may add fields or enum values; they do
not remove or reinterpret existing fields. Malformed trailing JSONL records may result from
interrupted writes; `smart-route explain` skips them and reads newest complete object.

## Retention

```toml
[logging]
enabled = true
redact = true
max_bytes = 5000000
backup_count = 3
```

Before an append would exceed `max_bytes`, current file rotates to `audit.jsonl.1`; older files
shift up to `backup_count`. Set `backup_count = 0` to retain only current file. One event larger
than `max_bytes` remains intact, so configured limit is an approximate per-file bound.

Redaction is defense in depth. Secret-shaped keys and values are replaced recursively, including
nested objects and arrays. Do not deliberately put secrets in model names, profile IDs, policy
names, or configuration.
