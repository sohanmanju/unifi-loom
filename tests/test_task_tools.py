from types import SimpleNamespace

import pytest

from unifi_network_mcp.catalog import Catalog
from unifi_network_mcp.task_tools import register_task_tools

SITE = '11111111-1111-4111-8111-111111111111'
DEVICE = '22222222-2222-4222-8222-222222222222'


class Registry:
    def __init__(self):
        self.functions = {}

    def tool(self, **kwargs):
        def decorate(function):
            assert kwargs['annotations'].readOnlyHint
            self.functions[function.__name__] = function
            return function
        return decorate


class Client:
    def __init__(self):
        self.requests = []

    async def request(self, **request):
        self.requests.append(request)
        return {'data': [], 'password': 'private'}


def setup():
    registry = Registry()
    register_task_tools(registry)
    client = Client()
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={'catalog': Catalog(), 'client': client}))
    return registry.functions, client, ctx


@pytest.mark.parametrize('name,path', [
    ('list_devices', 'devices'), ('list_clients', 'clients'), ('list_networks', 'networks'),
    ('list_wifi_broadcasts', 'wifi/broadcasts'), ('list_firewall_zones', 'firewall/zones'),
    ('list_firewall_policies', 'firewall/policies'), ('list_acl_rules', 'acl-rules'),
    ('list_wan_interfaces', 'wans'), ('list_vpn_servers', 'vpn/servers'),
    ('list_vpn_tunnels', 'vpn/site-to-site-tunnels'),
])
async def test_task_read_routes_and_redaction(name, path):
    functions, client, ctx = setup()
    result = await functions[name](site_id=SITE, ctx=ctx, offset=3, limit=25)
    request = client.requests[0]
    assert request['method'] == 'GET'
    assert request['path'].endswith(f'/sites/{SITE}/{path}')
    assert request['query'] == {'offset': 3, 'limit': 25}
    assert result['password'] == '[REDACTED]'


async def test_task_inputs_are_validated_before_io():
    functions, client, ctx = setup()
    with pytest.raises(ValueError):
        await functions['list_devices'](site_id='not-a-uuid', ctx=ctx)
    with pytest.raises(ValueError):
        await functions['list_networks'](site_id=SITE, ctx=ctx, limit=999)
    assert not client.requests


async def test_details_sites_and_client_history():
    functions, client, ctx = setup()
    assert len(functions) == 17
    await functions['get_application_info'](ctx)
    assert client.requests[-1]['path'].endswith('/v1/info')
    await functions['list_sites'](ctx)
    assert client.requests[-1]['path'].endswith('/v1/sites')
    await functions['get_device_details'](SITE, DEVICE, ctx)
    assert client.requests[-1]['path'].endswith(f'/devices/{DEVICE}')
    await functions['get_device_stats'](SITE, DEVICE, ctx)
    assert client.requests[-1]['path'].endswith(f'/devices/{DEVICE}/statistics/latest')
    await functions['get_client_details'](SITE, DEVICE, ctx)
    assert client.requests[-1]['path'].endswith(f'/clients/{DEVICE}')
    await functions['get_network_details'](SITE, DEVICE, ctx)
    assert client.requests[-1]['path'].endswith(f'/networks/{DEVICE}')
    await functions['list_client_inventory'](ctx, include_offline=False)
    assert client.requests[-1]['path'].endswith('/stat/sta')
    await functions['list_client_inventory'](ctx, include_offline=True)
    assert client.requests[-1]['path'].endswith('/list/user')
