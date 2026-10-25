"""Retain refreshed devices and discover entities after setup without duplicates."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.rointe.discovery import setup_description_entities
from custom_components.rointe.device_manager import RointeDeviceManager
from custom_components.rointe.coordinator import RointeDataUpdateCoordinator
from custom_components.rointe.rointesdk.rointe_api import ApiResponse


def test_conditional_entities_and_new_devices_appear_without_reload():
    listeners = []
    cleanups = []
    device = SimpleNamespace(boost_active=False)
    coordinator = SimpleNamespace(devices={"one": device})

    def subscribe(fn):
        listeners.append(fn)
        return lambda: listeners.remove(fn)

    coordinator.async_add_listener = subscribe
    entry = SimpleNamespace(async_on_unload=cleanups.append)
    description = SimpleNamespace(key="boost", available_fn=lambda d: d.boost_active)
    added = []
    setup_description_entities(
        entry,
        coordinator,
        added.extend,
        [description],
        lambda d, c, desc: (d, desc.key),
    )
    assert added == []
    device.boost_active = True
    listeners[0]()
    assert added == [(device, "boost")]
    listeners[0]()
    device.boost_active = False
    listeners[0]()
    device.boost_active = True
    listeners[0]()
    assert len(added) == 1
    second = SimpleNamespace(boost_active=True)
    coordinator.devices["two"] = second
    listeners[0]()
    assert added[-1] == (second, "boost")
    assert len(added) == 2
    cleanups[0]()
    assert listeners == []


def test_climate_and_update_discovery_are_idempotent():
    device = SimpleNamespace(id="one")
    coordinator = SimpleNamespace(
        data={"one": device},
        _registered_devices={"climate": set()},
        unregistered_keys={"climate": {"one": device}},
    )
    added = []
    constructor = lambda d, c: d.id
    add = RointeDataUpdateCoordinator.add_entities_for_seen_keys
    add(coordinator, added.extend, [constructor], "climate")
    coordinator.unregistered_keys["climate"]["one"] = device
    add(coordinator, added.extend, [constructor], "climate")
    coordinator.data["two"] = SimpleNamespace(id="two")
    add(coordinator, added.extend, [constructor], "climate")
    assert added == ["one", "two"]


@pytest.mark.asyncio
async def test_manager_keeps_existing_devices_in_every_snapshot():
    api = MagicMock()
    api.get_installation_devices.return_value = ApiResponse(True, ["one"], None)
    payload = {
        "serialnumber": "serial",
        "data": {
            "type": "radiator",
            "product_version": "v2",
            "name": "One",
            "power": 2,
            "temp_probe": 19,
        },
    }
    api.get_device.return_value = ApiResponse(True, payload, None)
    api.get_latest_energy_stats.return_value = ApiResponse(False, None, "No stats")
    api.get_latest_firmware.return_value = ApiResponse(True, {}, None)

    def executor(fn, *args):
        future = asyncio.get_running_loop().create_future()
        future.set_result(fn(*args))
        return future

    manager = RointeDeviceManager(
        "user",
        "password",
        "installation",
        SimpleNamespace(async_add_executor_job=executor),
        api,
    )
    first = await manager.update()
    original = first["one"]
    payload["data"]["temp_probe"] = 20
    second = await manager.update()
    assert second["one"] is original
    assert original.temp_probe == 20
    assert second is not first
    api.get_device.return_value = ApiResponse(False, None, "Offline")
    third = await manager.update()
    assert third["one"] is original
    assert not original.hass_available
    api.get_device.return_value = ApiResponse(True, payload, None)
    fourth = await manager.update()
    assert fourth["one"] is original
    assert original.hass_available
    api.get_installation_devices.return_value = ApiResponse(False, None, "Offline")
    assert (await manager.update())["one"] is original
    assert not original.hass_available
