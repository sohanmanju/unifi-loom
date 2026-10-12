import time
import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from unifi_network_mcp.catalog import Catalog
from unifi_network_mcp.changes import Changes
from unifi_network_mcp.client import UniFiClient, UniFiError, UniFiRejected
from unifi_network_mcp.config import Config
from unifi_network_mcp.diagrams import mermaid
from unifi_network_mcp.schema import SchemaContract, _active_discriminators
from unifi_network_mcp.snapshot import Snapshot, normalize

SITE = "11111111-1111-4111-8111-111111111111"
ZONE = "22222222-2222-4222-8222-222222222222"


class MutationClient:
    def __init__(self, directory):
        self.config = SimpleNamespace(state_dir=directory, allow_writes=True)
        self.current = {"id": ZONE, "name": "Old", "networkIds": []}
        self.writes = 0
        self.failure = None
        self.readback_failure = False

    async def request(self, adapter, method, path, query=None, body=None):
        if method == "GET":
            if self.writes and self.readback_failure:
                raise UniFiError("Read-back failed")
            return copy.deepcopy(self.current)
        self.writes += 1
        if self.failure:
            raise self.failure
        self.current.update(body)
        return copy.deepcopy(self.current)


async def prepared(directory):
    client = MutationClient(directory)
    changes = Changes(client, Catalog())
    preview = await changes.prepare("updateFirewallZone", {"siteId": SITE, "firewallZoneId": ZONE}, None,
                                    {"name": "New", "networkIds": []}, "Rename")
    return client, changes, preview["plan_id"]


async def test_audit_failure_after_acceptance_retains_success(tmp_path):
    client, changes, token = await prepared(tmp_path)
    original = changes.audit
    def audit(plan_id, request, outcome):
        if outcome == "accepted":
            raise OSError("Disk full")
        original(plan_id, request, outcome)
    changes.audit = audit
    result = await changes.apply(token)
    assert result["status"] == "accepted"
    assert result["audit_error"]
    assert result["read_back"]["name"] == "New"
    assert changes.status(token)["result"]["name"] == "New"
    with pytest.raises(ValueError, match="consumed"):
        await changes.apply(token)
    assert client.writes == 1


async def test_pre_submission_audit_failure_blocks_write(tmp_path):
    client, changes, token = await prepared(tmp_path)
    def fail(*args):
        raise OSError("Audit unavailable")
    changes.audit = fail
    with pytest.raises(OSError, match="Audit unavailable"):
        await changes.apply(token)
    assert client.writes == 0
    assert changes.status(token)["status"] == "rejected"
    assert changes.status(token)["audit_error"]


@pytest.mark.parametrize("failure, status", [(UniFiError("Original transport failure"), "unknown"),
                                               (UniFiRejected("Original rejection"), "rejected")])
async def test_failed_secondary_audit_preserves_original_error(tmp_path, failure, status):
    client, changes, token = await prepared(tmp_path)
    client.failure = failure
    original = changes.audit
    def audit(plan_id, request, outcome):
        if outcome in ("unknown", "rejected"):
            raise OSError("Secondary audit failure")
        original(plan_id, request, outcome)
    changes.audit = audit
    with pytest.raises(type(failure)) as result:
        await changes.apply(token)
    assert result.value is failure
    assert changes.status(token)["status"] == status
    assert changes.status(token)["audit_error"]
    assert client.writes == 1


async def test_readback_failure_does_not_change_acceptance(tmp_path):
    client, changes, token = await prepared(tmp_path)
    client.readback_failure = True
    result = await changes.apply(token)
    assert result["status"] == "accepted"
    assert result["verification_error"]
    assert changes.status(token)["status"] == "accepted"


async def test_cancellation_retains_unknown_submission(tmp_path):
    client, changes, token = await prepared(tmp_path)
    client.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await changes.apply(token)
    assert changes.status(token)["status"] == "unknown"
    assert client.writes == 1


async def test_cancellation_after_acceptance_keeps_accepted_outcome(tmp_path):
    client, changes, token = await prepared(tmp_path)
    original = client.request
    async def request(adapter, method, path, query=None, body=None):
        if method == "GET" and client.writes:
            raise asyncio.CancelledError()
        return await original(adapter, method, path, query, body)
    client.request = request
    with pytest.raises(asyncio.CancelledError):
        await changes.apply(token)
    assert changes.status(token)["status"] == "accepted"
    assert changes.status(token)["verification_error"]


@pytest.mark.parametrize("operation", ["createNetwork", "createWifiBroadcast", "createFirewallPolicy"])
def test_discovery_contains_all_discriminator_targets(operation):
    catalog = Catalog()
    described = catalog.describe(operation)
    for schema in described["schemas"].values():
        for ref in schema.get("discriminator", {}).get("mapping", {}).values():
            assert ref in described["schemas"]
    assert described["schemas"] == catalog.contract.reachable(described["operation"])


def test_discovered_gateway_contract_is_used_for_validation():
    catalog = Catalog()
    description = catalog.describe("createNetwork")
    target = "#/components/schemas/IntegrationGatewayManagedNetworkCreateUpdateDto"
    assert "ipv4Configuration" in description["schemas"][target]["required"]
    body = {"management": "GATEWAY", "enabled": True, "name": "IoT", "vlanId": 20,
            "cellularBackupEnabled": False, "internetAccessEnabled": True, "isolationEnabled": True,
            "ipv4Configuration": {"autoScaleEnabled": False, "hostIpAddress": "192.168.20.1", "prefixLength": 24}}
    catalog.prepare_request("createNetwork", {"siteId": SITE}, body=body)
    del body["ipv4Configuration"]
    with pytest.raises(ValueError, match="required"):
        catalog.prepare_request("createNetwork", {"siteId": SITE}, body=body)
    assert not _active_discriminators.get()


def test_discriminator_validation_obeys_anyof_semantics():
    document = {"components": {"schemas": {
        "strict": {"type": "object", "discriminator": {"propertyName": "kind", "mapping": {"A": "#/components/schemas/a"}}},
        "a": {"type": "object", "required": ["requiredField"]}}}}
    contract = SchemaContract(document)
    # One failing branch must not invalidate a different successful anyOf branch.
    contract.validate({"kind": "A"}, {"anyOf": [{"$ref": "#/components/schemas/strict"}, {"type": "object"}]})
    assert not _active_discriminators.get()


@pytest.mark.parametrize("schema", [{"$ref": "https://example.invalid/schema"},
                                    {"discriminator": {"propertyName": "kind", "mapping": {"A": "https://example.invalid/schema"}}}])
def test_manifest_rejects_external_schema_targets(tmp_path, schema):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps([{"operation_id": "custom", "method": "POST", "path": "/api/s/{site}/cmd/stamgr",
                                    "summary": "Example", "body_schema": schema}]))
    with pytest.raises(ValueError, match="local"):
        Catalog(internal_manifest=str(manifest))


async def test_write_401_invalidates_session_and_next_operation_logs_in():
    logins, writes = [], []
    def handler(request):
        if request.url.path == "/api/auth/login":
            logins.append(request)
            return httpx.Response(200, json={}, headers={"Set-Cookie": "TOKEN=session; Path=/; Secure", "X-CSRF-Token": "csrf"})
        writes.append(request)
        return httpx.Response(401 if len(writes) == 1 else 200, json={})
    client = UniFiClient(Config("https://console.test", username="local", password="pw", internal_enabled=True),
                         httpx.MockTransport(handler))
    try:
        with pytest.raises(UniFiRejected):
            await client.request("internal", "POST", "/proxy/network/api/s/default/cmd/stamgr", body={"cmd": "kick-sta"})
        assert not client.logged_in and not client.csrf and not client.internal.cookies
        assert len(writes) == 1  # Rejected write was not replayed.
        await client.request("internal", "POST", "/proxy/network/api/s/default/cmd/stamgr", body={"cmd": "kick-sta"})
        assert len(logins) == 2 and len(writes) == 2
    finally:
        await client.close()


async def test_read_401_retry_is_bounded_and_second_failure_invalidates():
    reads, logins = [], []
    def handler(request):
        if request.url.path == "/api/auth/login":
            logins.append(request)
            return httpx.Response(200, json={}, headers={"Set-Cookie": "TOKEN=session; Path=/; Secure", "X-CSRF-Token": "csrf"})
        reads.append(request)
        return httpx.Response(401, json={})
    client = UniFiClient(Config("https://console.test", username="local", password="pw", internal_enabled=True),
                         httpx.MockTransport(handler))
    try:
        with pytest.raises(UniFiRejected):
            await client.request("internal", "GET", "/proxy/network/api/s/default/stat/device")
        assert len(reads) == 2 and len(logins) == 2
        assert not client.logged_in and not client.csrf and not client.internal.cookies
    finally:
        await client.close()


@pytest.mark.parametrize("value", [{"devices": None}, {"devices": [{"id": "ap", "uplink": "unknown"}]},
                                  {"schema_version": 1, "devices": [{"id": "ap", "name": "AP", "attachment": "unknown"}]}])
def test_offline_snapshots_fail_at_validation_boundary(value):
    with pytest.raises(ValidationError):
        mermaid(value)


def test_official_and_internal_topology_render_identically():
    official = {"devices": [{"id": "gateway", "macAddress": "AA:00", "name": "Gateway", "model": "UDM"},
                            {"id": "ap", "macAddress": "BB:00", "name": "AP", "uplink": {"deviceId": "gateway"}}],
                "clients": [{"id": "client", "name": "Laptop", "type": "WIRELESS", "uplinkDeviceId": "ap"}]}
    internal = {"devices": [{"_id": "gateway", "mac": "AA:00", "name": "Gateway", "model": "UDM"},
                            {"_id": "ap", "mac": "BB:00", "name": "AP", "uplink": {"uplink_mac": "aa:00"}}],
                "clients": [{"id": "client", "name": "Laptop", "ap_mac": "bb:00"}]}
    a, b = normalize(official), normalize(internal)
    assert isinstance(a, Snapshot)
    assert a.devices[1].attachment.parent_id == "gateway"
    assert a.clients[0].attachment.kind == "wireless"
    assert a.model_dump() == b.model_dump()
    assert mermaid(a, include_clients=True)["mermaid"] == mermaid(b, include_clients=True)["mermaid"]
    assert mermaid(a.model_dump(), include_clients=True) == mermaid(a, include_clients=True)


def test_missing_parent_and_duplicate_identity_are_explicit():
    snapshot = normalize({"devices": [{"id": "ap", "name": "AP", "uplink": {"deviceId": "absent"}}]})
    assert snapshot.devices[0].attachment.parent_id is None
    assert any("absent" in warning for warning in snapshot.warnings)
    with pytest.raises(ValidationError, match="unique"):
        normalize({"devices": [{"id": "ap"}, {"id": "ap"}]})


async def test_plan_status_and_outcome_retention(tmp_path):
    from unifi_network_mcp.changes import MutationOutcome
    client, changes, token = await prepared(tmp_path)
    assert changes.status(token)["status"] == "prepared"
    changes.plans[token]["expires"] = time.monotonic() - 1
    assert changes.status(token)["status"] == "expired"
    for index in range(257):
        changes.record(str(index), MutationOutcome("accepted"))
    assert len(changes.outcomes) == 256
    with pytest.raises(ValueError, match="Unknown"):
        changes.status("0")
    changes.outcomes["256"].recorded_at = time.monotonic() - 3601
    with pytest.raises(ValueError, match="retention"):
        changes.status("256")


def test_canonical_unknown_uplink_has_warning():
    snapshot = {"schema_version": 1, "devices": [{"id": "ap", "name": "AP"}]}
    result = mermaid(snapshot)
    assert any("Uplink of AP is unknown" in warning for warning in result["warnings"])
    assert " -->|" not in result["mermaid"]


@pytest.mark.parametrize('method', ['GET', 'PUT'])
async def test_explicit_internal_api_key_mode_never_logs_in(method):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})
    config = Config('https://console.test', api_key='key', internal_enabled=True, internal_auth='api_key')
    client = UniFiClient(config, httpx.MockTransport(handler))
    try:
        await client.request('internal', method, '/proxy/network/api/s/default/rest/device/ap1', body={} if method=='PUT' else None)
        assert len(requests) == 1
        assert requests[0].headers['x-api-key'] == 'key'
        assert 'cookie' not in requests[0].headers and 'x-csrf-token' not in requests[0].headers
        assert not client.logged_in
    finally:
        await client.close()


async def test_explicit_tls_name_applies_to_all_requests():
    import certifi
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={})
    config = Config('https://console.test', api_key='key', internal_enabled=True, internal_auth='api_key',
                    ca_cert=certifi.where(), tls_server_name='unifi.local')
    client = UniFiClient(config, httpx.MockTransport(handler))
    try:
        await client.request('official','GET','/proxy/network/integration/v1/info')
        await client.request('internal','GET','/proxy/network/api/s/default/stat/device')
        assert all(request.extensions['sni_hostname']=='unifi.local' for request in requests)
    finally:
        await client.close()


def test_auth_and_tls_configuration_boundaries():
    with pytest.raises(ValueError):
        Config('https://console.test', internal_enabled=True, internal_auth='api_key')
    with pytest.raises(ValueError):
        Config('https://console.test', tls_server_name='unifi.local')
    with pytest.raises(ValueError):
        Config('https://console.test', internal_auth='guess')
