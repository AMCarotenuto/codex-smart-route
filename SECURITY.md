# Security policy

Supported line: latest `0.1.x`. Use GitHub private vulnerability reporting when available; otherwise contact maintainers without opening a public issue. Include impact, preconditions, affected version, and reproduction details without credentials.

Never submit API keys, Codex authentication files, cookies, prompts containing private data, or raw audit logs. Smart Route does not read credential contents. Remote classifiers are disabled by default. Local services must bind `127.0.0.1`, reject unconfigured upstreams, enforce body limits, and authenticate control operations before any future HTTP adapter is enabled.

See `docs/SECURITY_MODEL.md` for threat boundaries.

