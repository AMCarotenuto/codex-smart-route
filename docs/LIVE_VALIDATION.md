# Live validation

Live validation is explicit, billable, and disabled by default. Ordinary `pytest`, import,
discovery, and CI make no model calls. Harness forces local routing rules, disables remote
classifier and audit logging, uses `read-only` sandbox, and never stores prompts, model output,
stderr, environment variables, credentials, or full commands.

## Cost and consent

Full suite makes three Codex CLI executions. Optional App Server suite adds three turns. It reads
`pyproject.toml` in current repository and writes only chosen report path. Review account quota and
catalog/config before consent.

Windows PowerShell, first validation platform:

```powershell
$env:SMART_ROUTE_LIVE = "1"
python -m codex_smart_route.live_validation --consent-live --include-app-server --report live-report.json
```

macOS/Linux:

```bash
SMART_ROUTE_LIVE=1 python -m codex_smart_route.live_validation \
  --consent-live --include-app-server --report live-report.json
```

Use `--catalog path/to/catalog.json` when App Server discovery is unavailable. Catalog must describe
real profiles authorized by local Codex installation. Harness fails before cancellation test unless
first two tasks automatically select two distinct `model@effort` profiles. Tune normal policy or
capability overrides; do not edit test code or force manual profiles.

Pytest wrapper requires another explicit guard:

```powershell
$env:SMART_ROUTE_LIVE = "1"
$env:SMART_ROUTE_LIVE_PYTEST_CONSENT = "1"
python -m pytest tests/live/test_live_opt_in.py
```

Set `SMART_ROUTE_LIVE_APP_SERVER=1` to include experimental App Server path and
`SMART_ROUTE_LIVE_CATALOG` to catalog path. CI sets none of these values.

## Evidence

Report separates:

- `selected_profile`: router result;
- `requested_model`: virtual `codex-smart-route` Auto entry;
- `forwarded_model` and `forwarded_reasoning_effort`: exact adapter payload/flags;
- `confirmed_model` and `confirmed_reasoning_effort`: `unverified` unless Codex later exposes
  documented, trustworthy runtime metadata.

Selection, command construction, JSONL observation, or successful output never counts as runtime
confirmation. Report records platform, Python version, Codex CLI version, and Desktop version as
`unavailable:not-exposed-by-cli` because CLI exposes no trustworthy Desktop version field.

CLI validation checks two distinct routed profiles, JSONL event streaming, completed shell-tool
round trip, and process cancellation. `--include-app-server` checks virtual Auto replacement before
`turn/start`, same-thread continuation, `turn/interrupt`, and `thread/compact/start` when method is
supported. App Server route remains experimental; no native Desktop picker registration is claimed.

Review report before sharing. Delete it normally when no longer needed; it contains profile names,
event type counts, versions, and platform data, but no raw task or response content.
