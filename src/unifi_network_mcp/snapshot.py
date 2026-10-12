"""Validate API variants at ingestion; diagrams consume one canonical snapshot."""
import ipaddress
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Attachment(CanonicalModel):
    parent_id: str | None = None
    kind: Literal["uplink", "wireless", "wired", "unknown"] = "unknown"
    port: str | int | None = None


class Device(CanonicalModel):
    id: str
    name: str
    model: str | None = None
    ip_address: str | None = None
    gateway: bool = False
    attachment: Attachment = Field(default_factory=Attachment)


class Client(CanonicalModel):
    id: str
    name: str
    network_id: str | None = None
    attachment: Attachment = Field(default_factory=Attachment)


class Network(CanonicalModel):
    id: str
    name: str
    vlan: str | int = "untagged/unspecified"
    subnet: str | None = None


class Snapshot(CanonicalModel):
    schema_version: Literal[1] = 1
    source: str = "offline"
    site: str = ""
    collected_at: str | None = None
    devices: list[Device] = Field(default_factory=list)
    clients: list[Client] = Field(default_factory=list)
    networks: list[Network] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_ids(self):
        for entities in (self.devices, self.clients, self.networks):
            if len({entity.id for entity in entities}) != len(entities):
                raise ValueError("Entity IDs must be unique within each inventory")
        return self


class RawModel(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True, populate_by_name=True)


class RawUplink(RawModel):
    parent: str | None = Field(default=None, validation_alias=AliasChoices("deviceId", "uplink_mac"))
    port: str | int | None = Field(default=None, validation_alias="uplink_remote_port")


class RawDevice(RawModel):
    id: str | None = Field(default=None, validation_alias=AliasChoices("id", "_id"))
    mac: str | None = Field(default=None, validation_alias=AliasChoices("macAddress", "mac"))
    name: str | None = None
    model: str | None = None
    ip_address: str | None = Field(default=None, validation_alias=AliasChoices("ipAddress", "ip"))
    type: str | None = None
    uplink: RawUplink | None = None
    uplink_mac: str | None = None


class RawClient(RawModel):
    id: str | None = None
    mac: str | None = Field(default=None, validation_alias=AliasChoices("macAddress", "mac"))
    name: str | None = Field(default=None, validation_alias=AliasChoices("name", "hostname"))
    network_id: str | None = Field(default=None, validation_alias=AliasChoices("networkId", "network_id"))
    uplink_device_id: str | None = Field(default=None, validation_alias="uplinkDeviceId")
    ap_mac: str | None = None
    sw_mac: str | None = None
    sw_port: str | int | None = None
    type: str | None = None


class RawIPv4(RawModel):
    subnet: str | None = None
    host: str | None = Field(default=None, validation_alias="hostIpAddress")
    prefix: int | None = Field(default=None, validation_alias="prefixLength", ge=0, le=128)


class RawNetwork(RawModel):
    id: str | None = Field(default=None, validation_alias=AliasChoices("id", "_id"))
    name: str = "Network"
    vlan: str | int | None = Field(default=None, validation_alias=AliasChoices("vlanId", "vlan"))
    subnet: str | None = Field(default=None, validation_alias="ip_subnet")
    ipv4: RawIPv4 | None = Field(default=None, validation_alias="ipv4Configuration")


class RawSnapshot(RawModel):
    source: str = "offline"
    site: str = ""
    collected_at: str | None = None
    devices: list[RawDevice] = Field(default_factory=list)
    clients: list[RawClient] = Field(default_factory=list)
    networks: list[RawNetwork] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def normalize(value: dict | Snapshot) -> Snapshot:
    """Both live data and legacy offline snapshots cross the same validated boundary."""
    if isinstance(value, Snapshot):
        return value
    if "schema_version" in value:
        return Snapshot.model_validate(value)
    raw = RawSnapshot.model_validate(value)
    warnings = list(raw.warnings)
    aliases: dict[str, str] = {}
    devices = []
    for index, item in enumerate(raw.devices):
        identity = item.id or item.mac or f"device-{index}"
        for alias in (item.id, item.mac, identity):
            if alias:
                key = alias.lower()
                if key in aliases and aliases[key] != identity:
                    raise ValueError("Ambiguous device alias")
                aliases[key] = identity
        devices.append(Device(id=identity, name=item.name or item.model or identity,
                              model=item.model, ip_address=item.ip_address,
                              gateway=item.type in ("udm", "ugw") or "UDM" in (item.model or "").upper()))

    def attachment(reference, kind, port=None):
        parent = aliases.get(reference.lower()) if reference else None
        if reference and not parent:
            warnings.append("Reported attachment parent absent from inventory: " + reference)
        return Attachment(parent_id=parent, kind=kind, port=port)

    for device, item in zip(devices, raw.devices):
        uplink = item.uplink or RawUplink()
        device.attachment = attachment(uplink.parent or item.uplink_mac, "uplink", uplink.port)
        if device.attachment.parent_id == device.id:
            device.attachment = Attachment(kind="uplink")
            warnings.append("Self-referencing uplink ignored: " + device.name)
        if not device.attachment.parent_id and not device.gateway:
            warnings.append("Uplink of " + device.name + " is unknown; no link inferred.")

    clients = []
    for index, item in enumerate(raw.clients):
        kind = "wireless" if item.ap_mac or item.type == "WIRELESS" else "wired" if item.sw_mac or item.type == "WIRED" else "unknown"
        clients.append(Client(id=item.id or item.mac or f"client-{index}", name=item.name or item.mac or "Client",
                              network_id=item.network_id,
                              attachment=attachment(item.uplink_device_id or item.ap_mac or item.sw_mac, kind, item.sw_port)))
    networks = []
    for index, item in enumerate(raw.networks):
        identity = item.id or f"network-{index}"
        ipv4 = item.ipv4 or RawIPv4()
        subnet = item.subnet or ipv4.subnet
        if not subnet and ipv4.host and ipv4.prefix is not None:
            try:
                subnet = str(ipaddress.ip_network(f"{ipv4.host}/{ipv4.prefix}", strict=False))
            except ValueError:
                warnings.append("Invalid reported subnet for " + item.name)
        networks.append(Network(id=identity, name=item.name,
                                vlan=item.vlan if item.vlan is not None else "untagged/unspecified", subnet=subnet))
    return Snapshot(source=raw.source, site=raw.site, collected_at=raw.collected_at,
                    devices=devices, clients=clients, networks=networks, warnings=warnings)
