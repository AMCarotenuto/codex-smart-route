# Compatibility

Python 3.11-3.14 targeted. CI covers Windows, Ubuntu, and macOS with supported Python versions.

Local audit on 2026-09-07 found Windows 11, Codex CLI `0.153.4`, App Server command and schema generation. Schema advertised dynamic model list, input modalities, and reasoning efforts; turn start accepts model and effort. Current sandbox omitted home-directory resolution, preventing authenticated App Server startup and live catalog verification. Codex Desktop package version was not discoverable through available AppX metadata.

CLI adapter uses documented command-line overrides and is stable path. App Server proxy is experimental and version-dependent. Desktop virtual picker entry is unverified and not shipped as patch. Custom models remain supported through runtime discovery or explicit catalogs; no Luna/Terra/Sol-only list exists.

Discovery supports current v2 `model/list` pagination and legacy rows using `id`, snake-case effort
fields, or missing `inputModalities`. Unknown future response fields are ignored. A missing
`modelProvider/capabilities/read` method produces partial-discovery warning while preserving model
catalog; malformed `result.data`, cursors, or provider responses produce actionable diagnostics.
`smart-route doctor` reports observed protocol family, server user agent when exposed, page count,
provider capability status, and partial-discovery warnings. No absent capability becomes `false` or
`true` by compatibility default.

## Optional developer-tool contracts

Smart Route has no runtime dependency on RTK, Caveman, or CaveCrew. It never imports, installs,
updates, configures, or removes them. Presence detection is read-only and best-effort:
`doctor` checks executable paths, known environment-marker names, and `SKILL.md` existence. For
explicit ownership diagnostics it classifies only documented owner/wrapper values. It never emits
those values and does not read skill contents, unrelated environment variables, or credential
files.

| Setup | Main-model owner | Contract | Reproducible check |
| --- | --- | --- | --- |
| Smart Route | Smart Route | Read-only and file-modifying tasks preserve PATH, environment, stdin/stdout/stderr, exit code, and project working directory. | Offline adapter/integration suite |
| + RTK | Smart Route | RTK may wrap commands and optimize output. It must preserve argv, transport, working directory, and exit code; it cannot add `-m` or model config. | Opt-in external probe below |
| + Caveman | Smart Route | Caveman may change human prose only. JSON, audit schema, model, reasoning effort, and routing controls remain unchanged. | Offline JSON/audit tests |
| + CaveCrew | Smart Route | CaveCrew may split and orchestrate work. When Smart Route is enabled, CaveCrew delegates main-model selection and does not add another router. | Offline ownership/override tests |
| Complete combination | Smart Route | Wrapper, style, and orchestration remain orthogonal. Exactly one component selects main model and reasoning effort. | Full offline suite plus optional RTK probe |

Run deterministic offline matrix on every supported platform:

```console
python -m pytest tests/unit/test_compatibility.py tests/unit/test_cli_adapter.py tests/integration/test_cli_exec_adapter.py
```

Run real RTK transport probe only when RTK is already installed:

```console
CODEX_SMART_ROUTE_EXTERNAL_COMPAT=1 python -m pytest tests/compatibility/test_external_tools.py
```

PowerShell equivalent:

```powershell
$env:CODEX_SMART_ROUTE_EXTERNAL_COMPAT = "1"
python -m pytest tests/compatibility/test_external_tools.py
```

Without both opt-in variable and executable, test reports an explicit skip. CI never installs or
requires external projects. Probe uses temporary files only; it exercises PATH/environment,
stdin/stdout/stderr, child exit `23`, and working directory.

## Ownership diagnostics and conflicts

`smart-route doctor` reports `compatibility.integrations`, `main_model_owner`, matrix, and warning
list. Detection recognizes `rtk` on PATH, repo/user skill manifests for Caveman and CaveCrew, and
marker **names** `RTK_ACTIVE`, `RTK_SESSION`, `RTK_WRAPPER`, `CAVEMAN_ACTIVE`, `CAVEMAN_LEVEL`,
`CAVECREW_ACTIVE`, and `CAVECREW_MODEL_OWNER`. Marker values never appear in report.

For explicit troubleshooting only, `CODEX_SMART_ROUTE_MODEL_OWNER=smart-route` records ownership
and `CODEX_SMART_ROUTE_WRAPPER_CHAIN=rtk,codex` records wrapper order. Routing ignores both.
`doctor` warns when enabled Smart Route sees another declared owner, CaveCrew declares another
owner, adapter points back to Smart Route, wrapper chain contains Smart Route, or Auto slug is used
as owner. Fix by leaving Smart Route as sole main-model owner and keeping outer wrappers limited to
transport/output behavior.

Version assumptions: Smart Route targets Python 3.11-3.14 and current Codex CLI/App Server schema
described above. RTK, Caveman, and CaveCrew APIs are not linked and have no pinned versions. Conflict
reports should include Smart Route version, `doctor` JSON, OS/Python, Codex version, external tool
versions obtained from documented external-tool commands, failing matrix row, and minimal
reproduction. Remove prompts, environment values, credentials, and proprietary output first.
