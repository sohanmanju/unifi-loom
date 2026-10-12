"""Direct, typed tools for common jobs; advanced endpoints stay in the API catalog."""
from mcp.server.fastmcp import Context
from mcp.types import ToolAnnotations

from .client import redact

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)


async def execute(ctx, operation, path_params=None, query=None):
    state = ctx.request_context.lifespan_context
    request = state['catalog'].prepare_request(operation, path_params, query)
    return redact(await state['client'].request(**request))


async def page(ctx, operation, site_id=None, offset=0, limit=100, filter_expression=None):
    query = {'offset': offset, 'limit': limit}
    if filter_expression is not None:
        query['filter'] = filter_expression
    return await execute(ctx, operation, {'siteId': site_id} if site_id else {}, query)


def register_task_tools(mcp):
    @mcp.tool(annotations=READ)
    async def get_application_info(ctx: Context) -> dict:
        """Read the actual UniFi Network application version. No site ID or discovery step required."""
        return await execute(ctx, 'getInfo')

    @mcp.tool(annotations=READ)
    async def list_sites(ctx: Context, offset: int = 0, limit: int = 100) -> dict:
        """List sites and their official UUIDs. Call first when site_id is unknown. Returns one page; follow totalCount."""
        return await page(ctx, 'getSiteOverviewPage', offset=offset, limit=limit)

    @mcp.tool(annotations=READ)
    async def list_devices(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """List adopted UniFi APs, switches and gateways with model, status and official device UUID. One page; optional documented UniFi filter."""
        return await page(ctx, 'getAdoptedDeviceOverviewPage', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def get_device_details(site_id: str, device_id: str, ctx: Context) -> dict:
        """Read one adopted device's interfaces, firmware and reported uplink. Uses official UUIDs, not legacy IDs."""
        return await execute(ctx, 'getAdoptedDeviceDetails', {'siteId': site_id, 'deviceId': device_id})

    @mcp.tool(annotations=READ)
    async def get_device_stats(site_id: str, device_id: str, ctx: Context) -> dict:
        """Read the latest adopted-device statistics for traffic and health inspection. Uses official UUIDs."""
        return await execute(ctx, 'getAdoptedDeviceLatestStatistics', {'siteId': site_id, 'deviceId': device_id})

    @mcp.tool(annotations=READ)
    async def list_clients(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """List currently connected clients with names, IPs and connection types. Uses site UUID; returns one page, not offline history."""
        return await page(ctx, 'getConnectedClientOverviewPage', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def get_client_details(site_id: str, client_id: str, ctx: Context) -> dict:
        """Read one connected client's reported identity and uplink. Uses official site/client UUIDs."""
        return await execute(ctx, 'getConnectedClientDetails', {'siteId': site_id, 'clientId': client_id})

    @mcp.tool(annotations=READ)
    async def list_client_inventory(ctx: Context, site: str = 'default', include_offline: bool = False) -> dict:
        """Read internal client inventory with hostnames, OUI/vendor and fingerprint IDs, useful for IoT identification. Opt-in internal adapter required. Legacy site name; include_offline selects known-client history. Numeric fingerprints alone do not establish a model."""
        operation = 'internalListKnownClients' if include_offline else 'internalListClients'
        return await execute(ctx, operation, {'site': site})

    @mcp.tool(annotations=READ)
    async def list_networks(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """List configured networks and VLAN IDs. For subnet/DHCP details use get_network_details. One page; site UUID required."""
        return await page(ctx, 'getNetworksOverviewPage', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def get_network_details(site_id: str, network_id: str, ctx: Context) -> dict:
        """Read one VLAN/network's full reported configuration including subnet, DHCP, isolation and zone where available. Uses official UUIDs."""
        return await execute(ctx, 'getNetworkDetails', {'siteId': site_id, 'networkId': network_id})

    @mcp.tool(annotations=READ)
    async def list_wifi_broadcasts(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """List SSIDs/Wi-Fi broadcasts and reported network/broadcast scope. Credentials are redacted. One page; site UUID required."""
        return await page(ctx, 'getWifiBroadcastPage', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def list_firewall_zones(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """List firewall zones used to group networks for security policies. One page; site UUID required."""
        return await page(ctx, 'getFirewallZones', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def list_firewall_policies(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """Inspect configured firewall policies, actions and source/destination matching. One page; no mutation."""
        return await page(ctx, 'getFirewallPolicies', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def list_acl_rules(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """List network access-control (ACL) rules for security inspection. One page; no mutation."""
        return await page(ctx, 'getAclRulePage', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def list_wan_interfaces(site_id: str, ctx: Context, offset: int = 0, limit: int = 100) -> dict:
        """Read WAN interface inventory/configuration exposed by the official API. This tool does not configure WAN links."""
        return await page(ctx, 'getWansOverviewPage', site_id, offset, limit)

    @mcp.tool(annotations=READ)
    async def list_vpn_servers(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """Read configured VPN server inventory exposed by the official API. One page; configuration writes are not exposed here."""
        return await page(ctx, 'getVpnServerPage', site_id, offset, limit, filter_expression)

    @mcp.tool(annotations=READ)
    async def list_vpn_tunnels(site_id: str, ctx: Context, offset: int = 0, limit: int = 100, filter_expression: str | None = None) -> dict:
        """Read site-to-site VPN tunnel inventory. One page; this tool does not create or change a VPN."""
        return await page(ctx, 'getSiteToSiteVpnTunnelPage', site_id, offset, limit, filter_expression)
