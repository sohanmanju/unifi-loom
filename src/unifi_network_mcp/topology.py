import asyncio
from datetime import datetime, timezone

from .client import UniFiError
from .snapshot import normalize


def rows(response):
    if isinstance(response, list):
        return response
    if isinstance(response, dict) and isinstance(response.get("data"), list):
        return response["data"]
    raise UniFiError("Expected a UniFi list response")


class Topology:
    def __init__(self, client, catalog):
        self.client, self.catalog = client, catalog

    async def page_all(self, operation, site_id):
        result, offset = [], 0
        for _ in range(500):
            request = self.catalog.prepare_request(operation, {"siteId": site_id}, {"offset": offset, "limit": 200})
            response = await self.client.request(**request)
            page = rows(response)
            result.extend(page)
            offset += len(page)
            total = response.get("totalCount") if isinstance(response, dict) else None
            if not page or (total is not None and offset >= total) or (total is None and len(page) < 200):
                return result
        raise UniFiError("Pagination exceeded 100,000 rows; narrow the site")

    async def collect(self, site_id="", site="default", source="official", include_clients=False):
        warnings = []
        if source == "internal":
            operations = ["internalListDevices", "internalListNetworks"]
            if include_clients:
                operations.append("internalListClients")
            responses = await asyncio.gather(*[
                self.client.request(**self.catalog.prepare_request(op, {"site": site})) for op in operations])
            devices, networks = rows(responses[0]), rows(responses[1])
            clients = rows(responses[2]) if include_clients else []
            warnings.append("Internal topology fields are version-dependent and require validation on your console.")
        elif source == "official":
            devices, networks = await asyncio.gather(
                self.page_all("getAdoptedDeviceOverviewPage", site_id),
                self.page_all("getNetworksOverviewPage", site_id))
            semaphore = asyncio.Semaphore(6)
            async def details(item, operation, parameter, kind):
                async with semaphore:
                    try:
                        req = self.catalog.prepare_request(operation, {"siteId": site_id, parameter: item["id"]})
                        return await self.client.request(**req)
                    except UniFiError:
                        warnings.append(kind + " details unavailable: " + str(item.get("id")))
                        return item
            devices = await asyncio.gather(*[details(d, "getAdoptedDeviceDetails", "deviceId", "Device") for d in devices])
            networks = await asyncio.gather(*[details(n, "getNetworkDetails", "networkId", "Network") for n in networks])
            clients = await self.page_all("getConnectedClientOverviewPage", site_id) if include_clients else []
            warnings.append("Official client overviews may omit attachment/network fields; missing links remain unknown.")
        else:
            raise ValueError("source must be official or internal")
        return normalize({"collected_at": datetime.now(timezone.utc).isoformat(), "source": source,
                "site": site_id if source == "official" else site,
                "devices": devices, "networks": networks, "clients": clients, "warnings": warnings}).model_dump()
