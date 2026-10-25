"""Identify device families and avoid controls for unused shared firmware fields."""


import pytest

from custom_components.rointe.rointesdk.device import RointeDevice
from custom_components.rointe.rointesdk.rointe_api import RointeAPI
from custom_components.rointe.switch import SWITCH_DESCRIPTIONS
from custom_components.rointe.number import NUMBER_DESCRIPTIONS


def make_device(**data):
    return RointeDevice("device", {"serialnumber": "serial", "data": {
        "type": "radiator", "product_version": "v2", "temp": 21, **data,
    }}, None, None)


@pytest.mark.parametrize("code,kind", [(1, "radiator"), (2, "towel"), (3, "acs"),
                                       (4, "therm"), (5, "storage_heater")])
def test_numeric_family_and_model_survive_updates(code, kind):
    device = make_device(product_type=code, product_brand=1, product_model=24)
    assert device.type == kind
    assert "brand 1, model 24" in device.model_name
    assert "Olympia" not in device.model_name
    device.update_data({"data": {"product_type": code, "product_model": 25}}, None, None)
    assert device.product_model == 25
    assert device.type == kind


def test_legacy_model_name_and_missing_version():
    assert make_device().model_name == "D-Series Radiator"
    assert RointeDevice("id", {"data": {"product_type": 1}}, None, None).type == "radiator"


def test_shared_unused_fields_are_not_capabilities():
    fields = dict(product_type=1, product_brand=1, product_model=24,
                  silence_mode=False, pir_mode=False, block_local=False, windows_open_mode=False)
    device = make_device(**fields)
    assert device.supports_setting("windows_open_mode")
    for field in ("silence_mode", "pir_mode", "block_local", "dont_disturb_mode"):
        assert not device.supports_setting(field)
    assert make_device(**{**fields, "product_model": 1}).supports_setting("pir_mode")
    assert make_device(**{**fields, "product_type": 5}).supports_setting("silence_mode")
    assert not make_device(**{**fields, "product_type": 3}).supports_setting("windows_open_mode")


def test_unknown_family_is_conservative_and_false_is_supported():
    device = make_device(product_type=99, dont_disturb_mode=False, ice_mode=False)
    assert not device.supports_setting("dont_disturb_mode")
    assert device.supports_setting("ice_mode")
    assert make_device(dont_disturb_mode=False).supports_setting("dont_disturb_mode")
    assert not make_device(dont_disturb_mode=None).supports_setting("dont_disturb_mode")


def test_entity_descriptions_gate_controls():
    device = make_device(product_type=1, product_brand=1, product_model=24, silence_mode=False)
    assert not next(d for d in SWITCH_DESCRIPTIONS if d.key == "silence_mode").available_fn(device)
    assert not next(d for d in NUMBER_DESCRIPTIONS if d.key == "comfort_temperature").available_fn(device)


def test_inventory_context_and_model_metadata_reach_realtime_device():
    api = RointeAPI("user", "password")
    zones = [{"id": "parent", "zones": [{"id": "child", "devices": [
        {"id": "device", "serialNumber": "serial", "product_type": 5, "product_model": 2}
    ]}]}]
    assert api._extract_devices_any(zones, "installation") == ["device"]
    meta = api._nexa_device_cache["device"]
    assert meta["installation_id"] == "installation"
    assert meta["zone_id"] == "child"
    info = api._enrich_nexa_device({"data": {"product_model": 3}}, meta)
    assert info["data"] == {"product_type": 5, "product_model": 3}
    assert info["metadata"] is meta
    api._extract_devices_any([{**zones[0], "zones": [{"id": "new", "devices": [
        {"id": "device", "serialNumber": "serial", "name": "Renamed"}
    ]}]}], "installation")
    assert api._nexa_device_cache["device"]["zone_id"] == "new"
    api.close()
