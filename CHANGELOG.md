# Changelog

## 0.1.4 — 2026-10-12

- Publish as UniFi Loom with task-specific read tools and a task-to-tool guide.
- Add 17 direct read tools, 28 exported MCP definitions, and a task-to-tool guide; 89 tests and the 28-tool smoke pass.
- Add portable setup documentation, CI, contribution/security guidance, and release packaging.
- Exclude credentials, certificates, live inventories and local machine paths from the publishing checkout.

## 0.1.3 — 2026-10-12

- Validate live read-only device/network inventory and AP radio reads; export Mermaid topology diagrams.
- Add explicit internal API-key authentication, avoiding session/MFA login on this console.
- Add a certificate-name override for verified HTTPS connections by IP.
- Read AP configuration through device inventory after the REST item GET returned 404. Bind radio previews/read-back to an explicit projected state read.
- Preserve legacy numeric setting representations and reject equivalent no-op edits.
- 77 regression tests and 11-tool MCP smoke checks pass. Network writes remain disabled and unverified.

## 0.1.2 — 2026-10-12

- Add `get_ap_radios` and `prepare_ap_radio_change` for channel/auto selection, channel width, transmit-power presets/custom dBm, and minimum RSSI.
- Preserve the complete radio table; validate reported capabilities and bind changes to the exact configuration read before constructing the preview.
- Reuse human approval, stale-state detection, single submission, audit and read-back.
- Add focused radio tests and MCP schema checks. Hardware verification remains pending.

## 0.1.1 — 2026-10-12

- Preserve accepted mutation outcomes through audit/read-back failures, retain rejected/unknown outcomes, and expose `change_status`. Keep pre-submission auditing mandatory.
- Use a shared local schema graph for discovery and validation, including discriminator subtypes and inherited schemas. Reject external manifest references.
- Invalidate all expired internal sessions; limit read retries to one and never replay writes.
- Normalize official, internal and offline topology into validated canonical records before Mermaid rendering.
- Add regression tests; 45 tests and the nine-tool MCP stdio smoke pass.

At version 0.1.1, verification was offline only.
