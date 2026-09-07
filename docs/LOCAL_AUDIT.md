# Local runtime audit — 2026-09-07

- OS: Windows 11 Professional (`10.0.26200`, x86_64).
- Codex CLI: `0.153.4`, npm installation.
- Codex App Server: available and able to generate v2/experimental JSON Schema.
- Schema contract: `model/list` returns model id/name, input modalities, default and supported reasoning efforts; `turn/start` accepts `model` plus `effort`; experimental `turn/settings/update` also contains model/effort.
- Authentication: Codex auth file presence detected without reading contents; `codex doctor` selected ChatGPT auth mode. Sandbox could not resolve Codex home and TLS reachability failed, so authenticated catalog/inference was not exercised.
- Desktop version: not returned by available AppX metadata.

Current Codex app host advertised these task-creation combinations independently from App Server discovery:

- `gpt-6-astra`: low, medium, high, xhigh, max, ultra.
- `gpt-5.6-sol`: low, medium, high, xhigh, max, ultra.
- `gpt-5.6-terra`: low, medium, high, xhigh, max, ultra.
- `gpt-5.6-luna`: low, medium, high, xhigh, max.
- `gpt-5.5`: low, medium, high, xhigh.
- `gpt-5.4-mini`: low, medium, high, xhigh.
- `gpt-5.3-codex-spark`: low, medium, high, xhigh.

This host metadata establishes selectable task settings in current app context, not successful provider execution or App Server confirmation. Smart Route does not hardcode this list.
