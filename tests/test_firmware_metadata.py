"""Optional firmware metadata must not prevent device updates."""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.rointe.device_manager import (
    RointeDeviceManager,
    determine_latest_firmware,
)
from custom_components.rointe.rointesdk.model import RointeProduct
from custom_components.rointe.rointesdk.rointe_api import ApiResponse
from custom_components.rointe.rointesdk.utils import build_update_map


def device_data():
    return {
        "serialnumber": "serial",
        "data": {"type": "radiator", "product_version": "v2", "temp_probe": 19},
        "firmware": {"firmware_version_device": "1.0"},
    }


@pytest.mark.parametrize("mapping", [
    {},
    build_update_map({}),
    {RointeProduct.RADIATOR_V2: None},
    {RointeProduct.RADIATOR_V2: {}},
    build_update_map({"towel": {"v2": {"end_user": {}}}}),
])
def test_absent_upgrade_paths_keep_installed_version(mapping):
    assert determine_latest_firmware(device_data(), mapping) == "1.0"


@pytest.mark.parametrize("firmware", [None, {}, {"firmware_version_device": None}])
def test_missing_firmware_metadata_is_unknown(firmware):
    payload = device_data()
    payload["firmware"] = firmware
    assert determine_latest_firmware(payload, build_update_map({})) is None


def test_missing_firmware_block_is_unknown():
    payload = device_data()
    del payload["firmware"]
    assert determine_latest_firmware(payload, build_update_map({})) is None


def test_alternative_version_field_matches_the_device_model():
    payload = device_data()
    payload["firmware"] = {"firmware_version": "1.0"}
    mapping = {RointeProduct.RADIATOR_V2: {"1.0": "1.1"}}
    assert determine_latest_firmware(payload, mapping) == "1.1"


def test_known_upgrade_and_unlisted_version():
    mapping = {RointeProduct.RADIATOR_V2: {"1.0": "1.1"}}
    payload = device_data()
    assert determine_latest_firmware(payload, mapping) == "1.1"
    payload["firmware"]["firmware_version_device"] = "2.0"
    assert determine_latest_firmware(payload, mapping) == "2.0"


@pytest.mark.asyncio
async def test_missing_metadata_does_not_block_other_devices():
    api = MagicMock()
    api.get_installation_devices.return_value = ApiResponse(True, ["one", "two"], None)
    first = device_data()
    del first["firmware"]
    second = device_data()
    api.get_device.side_effect = lambda device_id: ApiResponse(
        True, first if device_id == "one" else second, None,
    )
    api.get_latest_energy_stats.return_value = ApiResponse(False, None, "No stats")
    api.get_latest_firmware.return_value = ApiResponse(True, build_update_map({}), None)

    def executor(fn, *args):
        future = asyncio.get_running_loop().create_future()
        future.set_result(fn(*args))
        return future

    manager = RointeDeviceManager(
        "user", "password", "installation",
        SimpleNamespace(async_add_executor_job=executor), api,
    )
    devices = await manager.update()
    assert set(devices) == {"one", "two"}
    assert all(device.hass_available for device in devices.values())
    assert devices["one"].firmware_version is None
    assert devices["two"].latest_firmware_version == "1.0"

    second["data"]["temp_probe"] = 20
    refreshed = await manager.update()
    assert refreshed["two"].temp_probe == 20
