# Compatibility

Python 3.11-3.14 targeted. CI covers Windows, Ubuntu, and macOS with supported Python versions.

Local audit on 2026-09-07 found Windows 11, Codex CLI `0.153.4`, App Server command and schema generation. Schema advertised dynamic model list, input modalities, and reasoning efforts; turn start accepts model and effort. Current sandbox omitted home-directory resolution, preventing authenticated App Server startup and live catalog verification. Codex Desktop package version was not discoverable through available AppX metadata.

CLI adapter uses documented command-line overrides and is stable path. App Server proxy is experimental and version-dependent. Desktop virtual picker entry is unverified and not shipped as patch. Custom models remain supported through runtime discovery or explicit catalogs; no Luna/Terra/Sol-only list exists.

