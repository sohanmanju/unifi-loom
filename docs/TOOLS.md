# Task-to-tool guide

Use a task-specific read tool first. Common reads require one call once the site UUID is known; they do not require `list_operations` or `describe_operation`.

The authoritative definitions, including required arguments, defaults, JSON types, descriptions and annotations, are exported directly from the server in [tool-definitions.json](tool-definitions.json).

## Choose the tool

| Job | Tool | Required arguments |
|---|---|---|
| Find console application version | `get_application_info` | None |
| Find site UUID | `list_sites` | None |
| List APs, switches and gateways | `list_devices` | `site_id` |
| Read device interfaces/firmware/uplink | `get_device_details` | `site_id`, `device_id` |
| Read latest device statistics | `get_device_stats` | `site_id`, `device_id` |
| List online clients | `list_clients` | `site_id` |
| Read one online client | `get_client_details` | `site_id`, `client_id` |
| Find hostnames/vendor/fingerprints for IoT identification | `list_client_inventory` | None; internal adapter required |
| Include previously seen/offline clients | `list_client_inventory` | `include_offline=true` |
| List VLANs/networks | `list_networks` | `site_id` |
| Read subnet/DHCP/isolation configuration | `get_network_details` | `site_id`, `network_id` |
| List SSIDs and broadcast scopes | `list_wifi_broadcasts` | `site_id` |
| Inspect firewall zones | `list_firewall_zones` | `site_id` |
| Inspect firewall policies | `list_firewall_policies` | `site_id` |
| Inspect ACL rules | `list_acl_rules` | `site_id` |
| Inspect WAN interfaces | `list_wan_interfaces` | `site_id` |
| Inspect VPN servers | `list_vpn_servers` | `site_id` |
| Inspect site-to-site VPN tunnels | `list_vpn_tunnels` | `site_id` |
| Inspect AP radio configuration | `get_ap_radios` | `device_id` (legacy `_id`) |
| Preview AP channel/width/power/minimum RSSI change | `prepare_ap_radio_change` | `device_id`, `radio_name`, `settings`, `reason` |
| Collect a diagram-ready snapshot | `network_snapshot` | `site_id` for official source |
| Generate live Mermaid topology | `generate_diagram` | `site_id` for official source |
| Render a supplied snapshot offline | `diagram_from_snapshot` | `snapshot` |
| Discover an uncommon API operation | `list_operations` | None |
| Inspect the complete API request schema | `describe_operation` | `operation_id` |
| Read an uncommon endpoint | `read_operation` | `operation_id`; operation-specific `path_params`/`query` |
| Preview a general API mutation | `prepare_change` | `operation_id`, `reason`; operation-specific parameters/body |
| Request human approval and submit a preview | `apply_change` | `plan_id` |
| Inspect a prepared/submitted plan's status | `change_status` | `plan_id` |

## Argument rules

- **Official UUIDs:** `site_id`, `device_id`, `client_id` and `network_id` on official read tools are UUIDs from the API. Do not substitute MAC addresses or legacy IDs.
- **Internal identifiers:** `list_client_inventory`, AP radio tools and generic internal operations use a legacy `site` name, default `default`. AP radio `device_id` is the legacy `_id`; select the exact interface name returned by `get_ap_radios`.
- **Pagination:** official list tools return one UniFi page with `data`, `offset`, `limit`, `count` and `totalCount`. Defaults are `offset=0`, `limit=100`. Continue at `offset + len(data)` until totalCount is reached. Schema limits are enforced before requests are sent.
- **Filtering:** where supported, `filter_expression` is the documented UniFi filter syntax, not natural-language text. Leave it out unless you know the relevant filter field. WAN listing has no filter argument.
- **Identification:** client names, vendors and fingerprint IDs are evidence, not guaranteed hardware identities. Mark uncertain IoT classifications explicitly; distinguish online clients from history.

## Copyable calls

### Version and site discovery

```json
{"tool": "get_application_info", "arguments": {}}
```

```json
{"tool": "list_sites", "arguments": {}}
```

### IoT inventory

```json
{"tool": "list_client_inventory", "arguments": {"site": "default", "include_offline": false}}
```

This returns internal inventory fields such as hostname, OUI/vendor, reported IP and numeric fingerprint IDs. It requires the opt-in internal adapter. `include_offline=true` selects known-client history, not just current connections.

### VLAN/subnet inspection

```json
{"tool": "list_networks", "arguments": {"site_id": "11111111-1111-4111-8111-111111111111"}}
```

```json
{"tool": "get_network_details", "arguments": {"site_id": "11111111-1111-4111-8111-111111111111", "network_id": "22222222-2222-4222-8222-222222222222"}}
```

The UUIDs above are synthetic examples. Substitute actual IDs from your own API responses.

### Firewall inspection

```json
{"tool": "list_firewall_policies", "arguments": {"site_id": "11111111-1111-4111-8111-111111111111", "offset": 0, "limit": 100}}
```

### Diagram generation

```json
{"tool": "generate_diagram", "arguments": {"site_id": "11111111-1111-4111-8111-111111111111", "view": "physical", "include_clients": false, "save": true}}
```

Use `view="logical"` for VLAN/subnet grouping. No links are inferred from missing data.

### AP radio preview

```json
{"tool": "get_ap_radios", "arguments": {"device_id": "LEGACY-AP-ID", "site": "default"}}
```

```json
{"tool": "prepare_ap_radio_change", "arguments": {"device_id": "LEGACY-AP-ID", "radio_name": "wifi1", "settings": {"channel": "auto", "power_mode": "medium"}, "reason": "Apply the chosen radio settings", "site": "default"}}
```

### General write workflow

1. Use `describe_operation` to obtain the full request contract, including discriminator subtypes.
2. Call `prepare_change` with validated parameters/body and a reason.
3. Review the exact preview and its impact.
4. Call `apply_change` with the returned plan ID; the host asks the human for approval.
5. Use `change_status` to inspect acceptance/rejection/unknown outcomes without replay.

For VLAN configuration, operation IDs are `createNetwork`, `updateNetwork` and `deleteNetwork`. Wi-Fi broadcasts use `createWifiBroadcast`, `updateWifiBroadcast` and `deleteWifiBroadcast`. Firewall policies use `createFirewallPolicy`, `updateFirewallPolicy`, `patchFirewallPolicy`, `deleteFirewallPolicy` and `updateFirewallPolicyOrdering`. Bodies follow the exact published schema, so these advanced writes retain schema discovery rather than pretending every deployment has the same configuration shape.

## Regenerate definitions

```sh
uv run python scripts/export_tool_definitions.py
```

CI checks that this export matches the real server. Task-specific tools share the same catalog validation and redaction as generic reads.
