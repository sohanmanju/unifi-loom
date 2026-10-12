"""AP radio configuration through the opt-in internal Network adapter."""
import copy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .client import redact
from .changes import StateRead


WIDTHS = {"ng": {20, 40}, "na": {20, 40, 80, 160}, "6e": {20, 40, 80, 160, 320}}


class RadioEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    channel: int | Literal["auto"] | None = None
    channel_width_mhz: Literal[20, 40, 80, 160, 320] | None = None
    power_mode: Literal["auto", "low", "medium", "high", "custom"] | None = None
    transmit_power_dbm: int | None = None
    min_rssi_enabled: bool | None = None
    min_rssi_dbm: int | None = Field(default=None, ge=-100, le=-1)

    @model_validator(mode="after")
    def validate_edit(self):
        if not any(value is not None for value in self.model_dump().values()):
            raise ValueError("Specify at least one radio setting")
        if isinstance(self.channel, int) and not 1 <= self.channel <= 233:
            raise ValueError("Use a positive channel number or 'auto'")
        if self.transmit_power_dbm is not None and self.power_mode != "custom":
            raise ValueError("Explicit power requires power_mode='custom'")
        if self.power_mode == "custom" and self.transmit_power_dbm is None:
            raise ValueError("Custom power requires transmit_power_dbm")
        if self.min_rssi_enabled is False and self.min_rssi_dbm is not None:
            raise ValueError("Do not supply a threshold while disabling minimum RSSI")
        return self


def device_record(response, device_id):
    records = response.get("data", response) if isinstance(response, dict) else response
    if isinstance(records, list):
        matches = [record for record in records if isinstance(record, dict) and record.get("_id") == device_id]
        if len(matches) != 1:
            raise ValueError("Expected exactly one matching AP configuration")
        records = matches[0]
    if not isinstance(records, dict) or records.get("_id") != device_id:
        raise ValueError("Console did not return the requested legacy device ID")
    if records.get("type") != "uap":
        raise ValueError("Radio controls require a UniFi access point (type uap)")
    table = records.get("radio_table")
    if not isinstance(table, list) or not table or not all(isinstance(r, dict) for r in table):
        raise ValueError("AP configuration contains no valid radio_table")
    if not all(isinstance(r.get("name"), str) and r["name"] for r in table):
        raise ValueError("Every radio must have a reported interface name")
    if len({r["name"] for r in table}) != len(table):
        raise ValueError("AP contains ambiguous radio interface names")
    return records


def edit_table(device, radio_name, edit: RadioEdit):
    table = copy.deepcopy(device["radio_table"])
    matches = [radio for radio in table if radio["name"] == radio_name]
    if len(matches) != 1:
        raise ValueError("Select an exact radio interface name returned by get_ap_radios")
    radio = matches[0]
    band = radio.get("radio")
    if band not in WIDTHS:
        raise ValueError("Unsupported radio band; configuration is unchanged")
    if edit.channel_width_mhz is not None:
        width = edit.channel_width_mhz
        if width not in WIDTHS[band]:
            raise ValueError("Channel width is incompatible with this band")
        if width in (160, 320) and radio.get("ht") not in (width, str(width)):
            if radio.get(f"has_ht{width}") is not True:
                raise ValueError("AP has not reported support for the requested wide channel")
        radio["ht"] = str(width) if isinstance(radio.get("ht"), str) else width
    if edit.channel is not None:
        if band == "ng" and isinstance(edit.channel, int) and edit.channel > 14:
            raise ValueError("Channel is incompatible with the 2.4 GHz band")
        radio["channel"] = str(edit.channel) if isinstance(radio.get("channel"), str) and edit.channel != "auto" else edit.channel
    if edit.power_mode is not None:
        radio["tx_power_mode"] = edit.power_mode
    if edit.transmit_power_dbm is not None:
        limits = [radio.get("min_txpower"), radio.get("max_txpower")]
        try:
            minimum, maximum = (int(value) for value in limits if not isinstance(value, bool))
        except (TypeError, ValueError):
            raise ValueError("AP must report power bounds before setting custom transmit power") from None
        if not minimum <= edit.transmit_power_dbm <= maximum:
            raise ValueError("Transmit power is outside this AP's reported bounds")
        radio["tx_power"] = edit.transmit_power_dbm
    if edit.min_rssi_enabled is True and edit.min_rssi_dbm is None and "min_rssi" not in radio:
        raise ValueError("Enabling minimum RSSI requires a reported or explicit threshold")
    if edit.min_rssi_enabled is not None:
        radio["min_rssi_enabled"] = edit.min_rssi_enabled
    if edit.min_rssi_dbm is not None:
        if edit.min_rssi_enabled is not True and radio.get("min_rssi_enabled") is not True:
            raise ValueError("Enable minimum RSSI when setting its threshold")
        radio["min_rssi"] = edit.min_rssi_dbm
    if table == device["radio_table"]:
        raise ValueError("Requested settings already match the AP configuration")
    return table


class Radios:
    def __init__(self, client, catalog, changes):
        self.client, self.catalog, self.changes = client, catalog, changes

    async def configuration(self, device_id, site):
        request = self.catalog.prepare_request("internalListDevices", {"site": site})
        response = await self.client.request(**request)
        return response, device_record(response, device_id)

    async def get(self, device_id, site="default"):
        _, device = await self.configuration(device_id, site)
        return {"device_id": device_id, "name": device.get("name"), "model": device.get("model"),
                "radios": redact(device["radio_table"]),
                "compatibility": "Internal API; verify behavior on your console. Use each radio's exact name.",
                "channel_validation": "Channel syntax is checked locally; country, DFS and model availability are enforced by the controller."}

    async def prepare(self, device_id, radio_name, edit, reason, site="default"):
        baseline, device = await self.configuration(device_id, site)
        table = edit_table(device, radio_name, edit)
        preview = await self.changes.prepare("internalUpdate_device", {"site": site, "id": device_id}, None,
                                            {"radio_table": table}, reason, baseline=baseline,
                                            state_read=StateRead(self.catalog.prepare_request("internalListDevices", {"site": site}), device_id, fields=("_id", "radio_table")))
        preview["radio_change"] = {"device_id": device_id, "radio_name": radio_name, "settings": edit.model_dump(exclude_none=True)}
        preview["impact"] = "Radio reprovisioning may interrupt Wi-Fi clients; controller acceptance does not prove effective RF settings."
        return preview
