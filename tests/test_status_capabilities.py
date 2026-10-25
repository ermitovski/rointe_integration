"""Optional statuses need reported data and must respect model capabilities."""

from types import SimpleNamespace

import pytest

from custom_components.rointe.binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    RointeBinarySensor,
)
from custom_components.rointe.discovery import setup_description_entities
from custom_components.rointe.sensor import RointeSensor
from custom_components.rointe.sensor_descriptions import SENSOR_DESCRIPTIONS
from test_model_capabilities import make_device


BINARY = {description.key: description for description in BINARY_SENSOR_DESCRIPTIONS}
SENSORS = {description.key: description for description in SENSOR_DESCRIPTIONS}


def test_missing_optional_data_does_not_publish_default_statuses():
    device = make_device(product_type=1, product_brand=1, product_model=1)
    for key in (
        "boost_active", "timer_active", "connected", "local_lock", "remote_lock",
        "presence_mode", "pilot_wire", "legionella",
    ):
        assert not BINARY[key].available_fn(device), key
    for key in ("surface_temperature", "wifi_signal"):
        assert not SENSORS[key].available_fn(device), key


def test_reported_false_statuses_are_available_including_read_only_local_lock():
    device = make_device(
        product_type=1, product_brand=1, product_model=1,
        timer_mode=False, timer_time=0, is_alive=False,
        block_local=False, block_remote=False, pir_mode=False, pilot_mode=False,
    )
    coordinator = SimpleNamespace(
        devices={device.id: device}, last_update_success=True,
    )
    assert not device.supports_setting("block_local")
    for key in (
        "boost_active", "timer_active", "connected", "local_lock", "remote_lock",
        "presence_mode", "pilot_wire",
    ):
        entity = RointeBinarySensor(device, coordinator, BINARY[key])
        assert entity.available, key
        assert entity.is_on is False, key


def test_shared_firmware_fields_do_not_enable_unsupported_presence_or_timer():
    radiator = make_device(
        product_type=1, product_brand=1, product_model=24, pir_mode=False,
    )
    assert not BINARY["presence_mode"].available_fn(radiator)
    water_heater = make_device(
        product_type=3, timer_mode=True, timer_time=4_000_000_000,
    )
    assert not BINARY["boost_active"].available_fn(water_heater)
    assert not BINARY["timer_active"].available_fn(water_heater)
    assert not SENSORS["boost_time_remaining"].available_fn(water_heater)
    assert not SENSORS["timer_time_remaining"].available_fn(water_heater)


def test_legacy_boost_status_without_nexa_timer_fields_is_preserved():
    device = make_device(boost_active=True, boost_countdown=120)
    assert BINARY["boost_active"].available_fn(device)
    assert BINARY["boost_active"].value_fn(device)
    assert SENSORS["boost_time_remaining"].available_fn(device)
    assert SENSORS["boost_time_remaining"].value_fn(device) == 120


@pytest.mark.parametrize("family", [{"product_type": 3}, {"type": "acs"}, {"type": "water_heater"}])
def test_legionella_status_is_available_for_water_heaters_with_reported_data(family):
    assert not BINARY["legionella"].available_fn(make_device(**family))
    for active in (False, True):
        device = make_device(**family, legionella_status=active)
        assert BINARY["legionella"].available_fn(device)
        assert BINARY["legionella"].value_fn(device) is active
    assert not BINARY["legionella"].available_fn(
        make_device(product_type=1, legionella_status=False)
    )


def test_diagnostics_appear_when_reported_and_recover_without_duplicates():
    device = make_device(product_type=1)
    listeners = []
    coordinator = SimpleNamespace(
        devices={device.id: device}, last_update_success=True,
        async_add_listener=lambda fn: listeners.append(fn),
    )
    entry = SimpleNamespace(async_on_unload=lambda _: None)
    added = []
    setup_description_entities(
        entry, coordinator, added.extend,
        [SENSORS["surface_temperature"], SENSORS["wifi_signal"]], RointeSensor,
    )
    assert added == []
    payload = {**device._data, "temp_surface": 34.5, "wifisignal": -60}
    device.update_data({"data": payload}, None, None)
    listeners[0]()
    assert [entity.native_value for entity in added] == [34.5, -60]
    assert all(entity.available for entity in added)

    device.update_data({"data": {"product_type": 1}}, None, None)
    listeners[0]()
    assert all(not entity.available for entity in added)
    device.update_data({"data": payload}, None, None)
    listeners[0]()
    assert len(added) == 2
    assert all(entity.available for entity in added)


def test_window_status_requires_a_reported_reading():
    device = make_device(product_type=1, windows_open_mode=True)
    assert not BINARY["window_open"].available_fn(device)
    device.update_data(
        {"data": {**device._data, "windows_open_status": False}}, None, None,
    )
    assert BINARY["window_open"].available_fn(device)
    assert BINARY["window_open"].value_fn(device) is False
