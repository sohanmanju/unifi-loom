import copy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from unifi_network_mcp.catalog import Catalog
from unifi_network_mcp.changes import Changes
from unifi_network_mcp.radios import RadioEdit, Radios, device_record, edit_table


DEVICE = {
    "_id": "ap1", "type": "uap", "name": "Office AP", "model": "U7",
    "radio_table": [
        {"name": "wifi0", "radio": "ng", "channel": 1, "ht": 20, "tx_power_mode": "auto",
         "min_txpower": 4, "max_txpower": 23, "min_rssi_enabled": False, "vendor_setting": True},
        {"name": "wifi1", "radio": "na", "channel": 36, "ht": 80, "tx_power_mode": "high",
         "has_ht160": True, "min_txpower": 6, "max_txpower": 26},
        {"name": "wifi2", "radio": "6e", "channel": 5, "ht": 160, "tx_power_mode": "auto", "has_ht320": True},
    ]}


@pytest.mark.parametrize("edit", [{}, {"channel": 0}, {"channel": True}, {"channel": -1},
                                   {"channel": 234}, {"channel_width_mhz": 10}, {"power_mode": "maximum"},
                                   {"power_mode": "custom"}, {"transmit_power_dbm": 15},
                                   {"min_rssi_enabled": False, "min_rssi_dbm": -70}, {"min_rssi_dbm": 0}])
def test_invalid_edits_rejected(edit):
    with pytest.raises(ValidationError):
        RadioEdit.model_validate(edit)


def test_only_selected_radio_changes_and_no_defaults_invented():
    baseline = copy.deepcopy(DEVICE)
    result = edit_table(DEVICE, "wifi1", RadioEdit(channel=44, channel_width_mhz=160, power_mode="custom", transmit_power_dbm=17))
    assert DEVICE == baseline
    assert result[0] == DEVICE["radio_table"][0]
    assert result[2] == DEVICE["radio_table"][2]
    assert result[1]["channel"] == 44 and result[1]["ht"] == 160 and result[1]["tx_power"] == 17
    assert "min_rssi" not in result[1]
    assert result[0]["vendor_setting"] is True


@pytest.mark.parametrize("name, edit", [("missing", {"channel": 6}), ("wifi0", {"channel_width_mhz": 80}), ("wifi0", {"channel": 36}),
                                        ("wifi1", {"power_mode": "custom", "transmit_power_dbm": 27}),
                                        ("wifi2", {"power_mode": "custom", "transmit_power_dbm": 17}),
                                        ("wifi0", {"min_rssi_enabled": True}), ("wifi0", {"min_rssi_dbm": -70})])
def test_capability_and_consistency_constraints(name, edit):
    with pytest.raises(ValueError):
        edit_table(DEVICE, name, RadioEdit.model_validate(edit))


def test_auto_power_and_minimum_rssi_controls():
    table = edit_table(DEVICE, "wifi0", RadioEdit(channel="auto", power_mode="medium", min_rssi_enabled=True, min_rssi_dbm=-70))
    assert table[0]["channel"] == "auto"
    assert table[0]["tx_power_mode"] == "medium"
    assert table[0]["min_rssi_enabled"] is True and table[0]["min_rssi"] == -70
    assert "tx_power" not in table[0]


def test_wide_channels_require_reported_capability():
    table = edit_table(DEVICE, "wifi2", RadioEdit(channel_width_mhz=320))
    assert table[2]["ht"] == 320
    device = copy.deepcopy(DEVICE)
    del device["radio_table"][2]["has_ht320"]
    with pytest.raises(ValueError, match="reported support"):
        edit_table(device, "wifi2", RadioEdit(channel_width_mhz=320))


def test_ambiguous_and_non_ap_devices_rejected():
    with pytest.raises(ValueError):
        device_record({"data": []}, "ap1")
    with pytest.raises(ValueError):
        device_record({**DEVICE, "type": "usw"}, "ap1")
    with pytest.raises(ValueError):
        device_record(DEVICE, "another")
    device = copy.deepcopy(DEVICE)
    device["radio_table"][1]["name"] = "wifi0"
    with pytest.raises(ValueError, match="ambiguous"):
        device_record(device, "ap1")


class Client:
    def __init__(self, path):
        self.config = SimpleNamespace(state_dir=path, allow_writes=True)
        self.device = copy.deepcopy(DEVICE)
        self.calls = []

    async def request(self, adapter, method, path, query=None, body=None):
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "PUT":
            self.device.update(copy.deepcopy(body))
        return {"meta": {"rc": "ok"}, "data": [copy.deepcopy(self.device)]}


async def test_radio_preview_uses_existing_approval_plan_and_readback(tmp_path):
    client = Client(tmp_path)
    catalog = Catalog()
    changes = Changes(client, catalog)
    radios = Radios(client, catalog, changes)
    info = await radios.get("ap1")
    assert len(info["radios"]) == 3
    client.calls.clear()
    preview = await radios.prepare("ap1", "wifi1", RadioEdit(channel=44), "Reduce channel congestion")
    assert [c[0] for c in client.calls] == ["GET"]
    assert preview["radio_change"]["radio_name"] == "wifi1"
    result = await changes.apply(preview["plan_id"])
    assert [c[0] for c in client.calls] == ["GET", "GET", "PUT", "GET"]
    assert result["status"] == "accepted"
    assert result["read_back"]["radio_table"][1]["channel"] == 44
    assert client.calls[2][1] == "/proxy/network/api/s/default/rest/device/ap1"
    assert list(client.calls[2][2]) == ["radio_table"]


async def test_intervening_radio_edits_block_stale_overwrite(tmp_path):
    client = Client(tmp_path)
    catalog = Catalog()
    changes = Changes(client, catalog)
    preview = await Radios(client, catalog, changes).prepare("ap1", "wifi1", RadioEdit(channel=44), "Change channel")
    client.device["radio_table"][0]["channel"] = 6
    with pytest.raises(ValueError, match="changed"):
        await changes.apply(preview["plan_id"])
    assert all(call[0] != "PUT" for call in client.calls)
    assert changes.status(preview["plan_id"])["status"] == "rejected"


async def test_unknown_radio_fails_before_a_plan_is_created(tmp_path):
    client = Client(tmp_path)
    catalog = Catalog()
    changes = Changes(client, catalog)
    with pytest.raises(ValueError, match="exact radio"):
        await Radios(client, catalog, changes).prepare("ap1", "5ghz", RadioEdit(channel=44), "Change")
    assert not changes.plans
    assert all(call[0] == "GET" for call in client.calls)


def test_legacy_numeric_representation_and_noops():
    device = copy.deepcopy(DEVICE)
    device['radio_table'][0]['ht'] = '20'
    with pytest.raises(ValueError, match='already match'):
        edit_table(device, 'wifi0', RadioEdit(channel_width_mhz=20))
    table = edit_table(device, 'wifi0', RadioEdit(channel_width_mhz=40))
    assert table[0]['ht'] == '40'


async def test_radio_precondition_ignores_unrelated_live_statistics(tmp_path):
    client = Client(tmp_path)
    catalog = Catalog()
    changes = Changes(client, catalog)
    preview = await Radios(client, catalog, changes).prepare('ap1', 'wifi1', RadioEdit(channel=44), 'Change channel')
    client.device['uptime'] = 999
    assert (await changes.apply(preview['plan_id']))['status'] == 'accepted'


def test_radio_reader_selects_target_from_full_inventory():
    response = {'data': [{'_id': 'gateway', 'type': 'udm'}, DEVICE]}
    assert device_record(response, 'ap1') is DEVICE
