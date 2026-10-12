# Validation — 2026-10-12, version 0.1.4

Target: UDM Pro, UniFi OS 5.1.33, Network 10.6.106.

- 89 automated tests pass, including API-key/session isolation, TLS-name configuration, projected stale-state checks and legacy radio setting types.
- Twenty-eight-tool MCP stdio smoke passes.
- Live verified HTTPS MCP reads pass: application version, site/device/network inventory, canonical topology, Mermaid generation, and AP radio configuration.
- Internal inventory authentication with the integration key succeeds without an MFA login.
- REST device-item GET returned 404; radio reads use the working device inventory endpoint.
- Local trust is scoped to this MCP's console certificate and certificate name; global system trust is unchanged.

No network configuration writes were attempted. Writes remain disabled. Device provisioning, radio changes and complete Network UI coverage remain unverified. Outcomes are retained in memory only, not durable backups or automatic rollback.

Release source was scanned against known credential patterns and the local deployment’s actual secrets/identifiers. The clean publishing checkout contains synthetic fixtures and excludes local credentials, certificates, live inventories and topology exports.
