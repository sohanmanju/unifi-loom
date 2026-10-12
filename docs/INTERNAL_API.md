# Internal Network API extensions

Internal routes are undocumented and version-dependent. Prefer the official API when it supports the operation.

Set `UNIFI_INTERNAL_ENABLED=true` and explicitly select `UNIFI_INTERNAL_AUTH=api_key` or `session`. API-key acceptance varies by route/console. Session mode uses a dedicated local account and does not handle SSO/MFA challenges.

Custom operations are loaded only from the operator-owned JSON file named by `UNIFI_INTERNAL_MANIFEST`:

```json
[
  {
    "operation_id": "yourVerifiedOperation",
    "method": "PUT",
    "path": "/v2/api/site/{site}/YOUR-VERIFIED-RESOURCE/{id}",
    "summary": "Describe the exact verified operation",
    "body_schema": {
      "type": "object",
      "required": ["name"],
      "properties": {"name": {"type": "string"}}
    }
  }
]
```

Verify the route and payload against the Network UI and application version before enabling it. The example file is illustrative and is not loaded automatically.

Paths must remain inside allowed Network site APIs. External schema references and arbitrary URLs are rejected. The model cannot add operations. Every non-GET operation uses the same preview/human approval flow. Custom query parameters are not implemented.

Radio configuration reads select an AP from `/stat/device` because the REST item GET is unavailable on the tested version. A projected state-read descriptor binds the preview/read-back to the AP ID and radio table. Configuration writes still target the device REST update route; their hardware behavior remains unverified.
