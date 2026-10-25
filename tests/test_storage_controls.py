"""Storage commands address zones and preserve fresh charge configuration."""

from unittest.mock import MagicMock

import pytest

from custom_components.rointe.rointesdk.device import RointeDevice
from custom_components.rointe.rointesdk.rointe_api import RointeAPI
from custom_components.rointe.sensor_descriptions import _get_effective_power
from custom_components.rointe.number import NUMBER_DESCRIPTIONS
from test_commands import mock_socket


@pytest.fixture
def storage():
    zone = {
        "charge_percentage_min": 10,
        "charge_percentage_limit": 90,
        "additional_charge": 1,
        "charge_smart": True,
        "silence_mode": False,
        "res_auto_config": True,
        "res_man_config": False,
        "res_boost_config": True,
        "res_holiday_config": False,
        "schedule_charging": ["1" * 7 + "0" * 17] * 7,
        "schedule_charge_type": 1,
    }
    device = RointeDevice(
        "device",
        {
            "serialnumber": "serial",
            "metadata": {"zone_id": "zone"},
            "data": {
                "type": "storageheater",
                "product_type": 5,
                "product_model": 2,
                "charge_percentage": 37,
                "charging": True,
                "temp": 22,
                "power": 2,
                "nominal_power": 1560,
                **zone,
            },
            "commanded": zone,
        },
        None,
        None,
    )
    return device, zone


@pytest.fixture
def api(monkeypatch, storage):
    client = RointeAPI("user", "password", api_type="nexa")
    client.use_nexa_ws = True
    client.firebase_ws_url = "wss://offline.invalid"
    client.auth_token = "offline-token"
    client._ensure_valid_auth = MagicMock(return_value=True)
    client._read_via_websocket = MagicMock(return_value=dict(storage[1]))
    messages = mock_socket(monkeypatch)
    yield client, messages
    client.close()


def test_charge_limits_use_fresh_zone_companion(api, storage):
    client, messages = api
    device, zone = storage
    client._read_via_websocket.return_value = {**zone, "charge_percentage_min": 20}
    assert client.set_storage_settings(device, {"charge_percentage_limit": 80}).success
    client._read_via_websocket.assert_called_once_with("/zones/zone/data")
    assert messages[1]["b"]["p"] == "/zones/zone/data"
    body = messages[1]["b"]["d"]
    assert body["charge_percentage_min"] == 20
    assert body["charge_percentage_limit"] == 80
    assert "last_sync_datetime_device" not in body


def test_partial_charging_schedule_preserves_other_days_and_selects_custom(
    api, storage
):
    client, messages = api
    assert client.set_storage_settings(
        storage[0], {"schedule_charging": {"0": "01" * 12}}
    ).success
    body = messages[1]["b"]["d"]
    assert body["schedule_charging/0"] == "01" * 12
    assert "schedule_charging/1" not in body
    assert body["schedule_charge_type"] == 0


def test_smart_charging_preserves_other_zone_options(api, storage):
    client, messages = api
    assert client.set_storage_settings(storage[0], {"charge_smart": False}).success
    body = messages[1]["b"]["d"]
    assert body["charge_smart"] is False
    for field in ("silence_mode", "res_auto_config", "res_boost_config"):
        assert body[field] == storage[1][field]


@pytest.mark.parametrize(
    "changes",
    [
        {"charge_percentage_limit": 0},
        {"charge_percentage_limit": 65.5},
        {"charge_percentage_min": 35},
        {"charge_percentage_min": 7},
        {"additional_charge": 8},
        {"additional_charge": float("nan")},
        {"charge_smart": "false"},
        {"schedule_charging": {"7": "1" * 24}},
        {"schedule_charging": {"0": "C" * 24}},
        {"schedule_charging": {}},
    ],
)
def test_invalid_charge_settings_never_write(api, storage, changes):
    client, messages = api
    assert not client.set_storage_settings(storage[0], changes).success
    assert messages == []


def test_missing_pair_member_is_not_guessed(api, storage):
    client, messages = api
    client._read_via_websocket.return_value.pop("charge_percentage_min")
    assert not client.set_storage_settings(
        storage[0], {"charge_percentage_limit": 80}
    ).success
    assert messages == []


def test_other_families_never_redirect_to_zones(api, storage):
    client, messages = api
    storage[0].type = "radiator"
    assert not client.set_storage_settings(storage[0], {"charge_smart": True}).success
    client._read_via_websocket.assert_not_called()
    assert messages == []


def test_storage_power_is_unknown_without_measurement_and_controls_show_commanded(
    api, storage
):
    device, _ = storage
    assert device.type == "storage_heater"
    assert _get_effective_power(device) is None
    device._data["charge_percentage_limit"] = 100
    desc = next(d for d in NUMBER_DESCRIPTIONS if d.key == "charge_percentage_limit")
    assert desc.available_fn(device)
    assert desc.value_fn(device) == 90
    device.commanded = {}
    assert not desc.available_fn(device)


def test_enriched_storage_data_keeps_reported_and_commanded_separate(api, storage):
    client, _ = api
    result = client._enrich_nexa_device({"data": storage[0]._data}, {"zone_id": "zone"})
    assert result["data"]["charge_percentage"] == 37
    assert result["commanded"]["charge_percentage_limit"] == 90


def test_storage_cannot_override_physical_off(api, storage):
    client, messages = api
    client._read_via_websocket.return_value["power"] = 0
    assert not client.set_storage_settings(storage[0], {"charge_smart": True}).success
    assert messages == []
