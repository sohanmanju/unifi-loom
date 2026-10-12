import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from unifi_network_mcp.catalog import Catalog
from unifi_network_mcp.changes import Changes
from unifi_network_mcp.client import UniFiClient, UniFiError, redact, validate_path
from unifi_network_mcp.config import Config
from unifi_network_mcp.topology import Topology
from unifi_network_mcp.diagrams import mermaid
from unifi_network_mcp.server import apply_change, read_operation

SITE = "11111111-1111-4111-8111-111111111111"
ZONE = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def catalog():
    return Catalog()


def test_spec_covers_every_official_operation(catalog):
    expected = {op["operationId"] for path in catalog.spec["paths"].values()
                for method, op in path.items() if method in ("get", "post", "put", "patch", "delete")}
    assert expected == {k for k, v in catalog.operations.items() if v["adapter"] == "official"}
    assert len(expected) == 73
    desc = catalog.describe("createNetwork")
    assert desc["requires_change_plan"]
    assert desc["schemas"]


@pytest.mark.parametrize("path", [
    "/proxy/network/../protect/api", "/proxy/network/api/s/default/../../auth",
    "/proxy/network/api/s/default/rest/%2e%2e", "/proxy/network/api/s/default/rest/device?x=1",
    "/proxy/network/api/s/default/rest/device\\..", "https://attacker.invalid/",
    "/api/auth/login", "/proxy/network/api/s/default//rest/device",
])
def test_route_escape_rejected(path):
    with pytest.raises(ValueError):
        validate_path(path, "internal")


def test_schema_validation(catalog):
    req = catalog.prepare_request("getNetworksOverviewPage", {"siteId": SITE}, {"limit": 200})
    assert req["path"].endswith(f"/sites/{SITE}/networks")
    for params, query in [({"siteId": "not-a-uuid"}, {}), ({"siteId": SITE}, {"limit": 201}),
                          ({"siteId": SITE}, {"unrecognized": "x"})]:
        with pytest.raises(ValueError):
            catalog.prepare_request("getNetworksOverviewPage", params, query)
    with pytest.raises(ValueError):
        catalog.prepare_request("createFirewallPolicy", {"siteId": SITE}, body={})
    with pytest.raises(ValueError):
        catalog.prepare_request("internalUpdate_device", {"site": "../protect", "id": "x"}, body={})


def test_discriminator_subtype_validation(catalog):
    # Use the exact action schema published in this spec rather than inventing action fields.
    op = catalog.operations["executeAdoptedDeviceAction"]
    schema = op["requestBody"]["content"]["application/json"]["schema"]
    catalog.validate({"action": "RESTART"}, schema)
    with pytest.raises(ValueError, match="discriminator"):
        catalog.validate({"action": "MADE_UP_ACTION"}, schema)


def test_redaction_recursive():
    raw = {"x_passphrase": "wifi-secret", "nested": [{"privateKey": "key", "name": "AP"}], "password": "pw"}
    clean = redact(raw)
    assert "wifi-secret" not in json.dumps(clean)
    assert clean["nested"][0]["name"] == "AP"
    assert raw["password"] == "pw"


async def test_auth_cookie_csrf_and_no_key_leak(tmp_path):
    received = []
    def handler(request):
        received.append(request)
        if request.url.path == "/api/auth/login":
            return httpx.Response(200, json={"ok": True}, headers={
                "Set-Cookie": "TOKEN=session; Path=/; Secure; HttpOnly", "X-CSRF-Token": "csrf"})
        return httpx.Response(200, json={"meta": {"rc": "ok"}, "data": []})
    client = UniFiClient(Config("https://console.test", api_key="integration-key", username="admin",
                               password="pw", internal_enabled=True, state_dir=tmp_path), httpx.MockTransport(handler))
    try:
        await client.request("internal", "POST", "/proxy/network/api/s/default/cmd/stamgr", body={"cmd": "kick-sta"})
        assert received[1].headers["x-csrf-token"] == "csrf"
        assert "TOKEN=session" in received[1].headers["cookie"]
        assert all("x-api-key" not in r.headers for r in received)
        await client.request("official", "GET", "/proxy/network/integration/v1/sites")
        assert received[-1].headers["x-api-key"] == "integration-key"
        assert "cookie" not in received[-1].headers
    finally:
        await client.close()


async def test_http_errors_do_not_echo_secrets_and_writes_are_not_retried():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"password": "sensitive response"})
    client = UniFiClient(Config("https://console.test", api_key="key"), httpx.MockTransport(handler))
    try:
        with pytest.raises(UniFiError) as error:
            await client.request("official", "POST", "/proxy/network/integration/v1/sites/test/networks", body={})
        assert "sensitive" not in str(error.value)
        assert len(calls) == 1
    finally:
        await client.close()


class FakeClient:
    def __init__(self, tmp_path, allow=True):
        self.config = SimpleNamespace(state_dir=tmp_path, allow_writes=allow)
        self.current = {"id": ZONE, "name": "Old zone"}
        self.calls = []

    async def request(self, adapter, method, path, query=None, body=None):
        self.calls.append(method)
        if method == "GET":
            return copy.deepcopy(self.current)
        if method == "PUT":
            self.current.update(body)
        return copy.deepcopy(self.current)


async def test_write_plan_staleness_single_use_and_audit(tmp_path, catalog):
    client = FakeClient(tmp_path)
    changes = Changes(client, catalog)
    plan = await changes.prepare("updateFirewallZone", {"siteId": SITE, "firewallZoneId": ZONE}, None,
                                 {"name": "New zone", "networkIds": []}, "Rename zone")
    assert client.calls == ["GET"]
    client.current["name"] = "Someone else edited"
    with pytest.raises(ValueError, match="changed"):
        await changes.apply(plan["plan_id"])
    assert "PUT" not in client.calls
    plan = await changes.prepare("updateFirewallZone", {"siteId": SITE, "firewallZoneId": ZONE}, None,
                                 {"name": "New zone", "networkIds": []}, "Rename zone")
    result = await changes.apply(plan["plan_id"])
    assert result["status"] == "accepted"
    assert result["read_back"]["name"] == "New zone"
    with pytest.raises(ValueError, match="consumed"):
        await changes.apply(plan["plan_id"])
    assert client.calls.count("PUT") == 1
    records = [json.loads(s) for s in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert "rejected" in [r["outcome"] for r in records]
    assert "accepted" in [r["outcome"] for r in records]
    assert all("body" not in r for r in records)


async def test_read_tool_cannot_write(catalog):
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"catalog": catalog}))
    with pytest.raises(ValueError):
        await read_operation("deleteFirewallZone", ctx, {"siteId": SITE, "firewallZoneId": ZONE})


async def test_human_approval_cannot_be_bypassed(tmp_path, catalog):
    client = FakeClient(tmp_path)
    changes = Changes(client, catalog)
    plan = await changes.prepare("updateFirewallZone", {"siteId": SITE, "firewallZoneId": ZONE}, None,
                                 {"name": "New zone", "networkIds": []}, "Rename")
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"config": client.config, "changes": changes}))
    async def declined(**kwargs):
        return SimpleNamespace(action="decline", data=None)
    ctx.elicit = declined
    assert (await apply_change(plan["plan_id"], ctx))["status"] == "not_applied"
    assert "PUT" not in client.calls
    async def unsupported(**kwargs):
        raise RuntimeError("Client does not support elicitation")
    ctx.elicit = unsupported
    with pytest.raises(RuntimeError):
        await apply_change(plan["plan_id"], ctx)
    assert "PUT" not in client.calls
    async def accepted(**kwargs):
        assert "New zone" in kwargs["message"]
        return SimpleNamespace(action="accept", data=SimpleNamespace(approve=True))
    ctx.elicit = accepted
    assert (await apply_change(plan["plan_id"], ctx))["status"] == "accepted"
    assert client.calls.count("PUT") == 1


async def test_disabled_and_expired_plans_do_not_write(tmp_path, catalog):
    client = FakeClient(tmp_path, allow=False)
    changes = Changes(client, catalog)
    plan = await changes.prepare("updateFirewallZone", {"siteId": SITE, "firewallZoneId": ZONE}, None,
                                 {"name": "New zone", "networkIds": []}, "Rename")
    with pytest.raises(ValueError, match="disabled"):
        await changes.apply(plan["plan_id"])
    client.config.allow_writes = True
    changes.plans[plan["plan_id"]]["expires"] = 0
    with pytest.raises(ValueError, match="expired"):
        await changes.apply(plan["plan_id"])
    assert "PUT" not in client.calls


async def test_pagination_uses_actual_page_size(catalog):
    offsets = []
    class Paged:
        async def request(self, **request):
            offset = request["query"]["offset"]
            offsets.append(offset)
            return {"totalCount": 3, "data": [{"id": str(offset)}]}
    result = await Topology(Paged(), catalog).page_all("getAdoptedDeviceOverviewPage", SITE)
    assert len(result) == 3
    assert offsets == [0, 1, 2]


def test_mermaid_edges_escaping_unknown_links():
    snapshot = {"devices": [
        {"id": "gateway", "name": 'UDM\"] --> evil["', "model": "UDM Pro"},
        {"id": "switch", "name": "Switch", "uplink": {"deviceId": "gateway"}},
        {"id": "ap", "name": "AP", "uplink": {"deviceId": "switch"}},
        {"id": "isolated", "name": "Unknown AP"}], "networks": [{"id": "lan", "name": "LAN", "vlanId": 10}]}
    result = mermaid(snapshot)
    assert result["mermaid"].count(" -->|") == 2
    assert 'evil["' not in result["mermaid"]
    assert any("Unknown AP" in warning for warning in result["warnings"])
    logical = mermaid(snapshot, "logical")
    assert "VLAN 10" in logical["mermaid"]
    assert " --> " not in logical["mermaid"]


def test_internal_manifest_stays_in_network(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([{"operation_id": "bad", "method": "POST", "path": "/../protect/api", "summary": "bad"}]))
    with pytest.raises(ValueError):
        Catalog(internal_manifest=str(path))


def test_logical_subnet_uses_reported_gateway_prefix():
    result = mermaid({"networks": [{"id": "n", "name": "IoT", "vlanId": 20,
                    "ipv4Configuration": {"hostIpAddress": "192.168.20.1", "prefixLength": 24}}]}, "logical")
    assert "192.168.20.0/24" in result["mermaid"]
