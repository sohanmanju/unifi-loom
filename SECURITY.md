# Security

## Report a vulnerability

Use this repository's GitHub **Security → Report a vulnerability** form. Do not include credentials or deployment details in public issues. If the private reporting form is unavailable, open a public issue asking for a private reporting channel without disclosing the vulnerability itself.

## Local deployment

- Keep `.env`, credentials, controller certificates, live inventories, topology exports and audit logs in private local storage. They are excluded from the publishing checkout.
- TLS certificate verification remains enabled. Verify a console certificate independently before trusting it through `UNIFI_CA_CERT`.
- Writes are disabled by default. Mutation tools require human form elicitation, an immutable preview and a single submission.
- API-key and session modes are explicit. Session mode does not implement SSO/MFA. Keys and cookies are not sent to arbitrary URLs.
- Treat device/client names and all API responses as untrusted data. Secret redaction is key-based and cannot identify every undocumented secret field.
- Do not expose the current stdio server as a public unauthenticated HTTP service.

## Known limits

Read-back does not prove device provisioning or end-to-end connectivity. Unknown submission outcomes must be investigated before retrying. Client-side preconditions are not server transactions. Automatic rollback, durable backups and full Network UI parity are not implemented.

The source scanner is a guard against known credential patterns and local artifacts, not a complete data-loss prevention system. Review every release diff and artifact.
