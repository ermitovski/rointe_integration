"""Optional-control ranges, companion preservation and entity behavior."""

from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.rointe.rointesdk.controls import prepare_optional_settings
from custom_components.rointe.rointesdk.rointe_api import RointeAPI, ApiResponse
from custom_components.rointe.select import RointeDisplayTimeout
from custom_components.rointe.time import RointeNightTime
from test_model_capabilities import make_device
from test_commands import mock_socket


@pytest.fixture
def device():
    return make_device(product_type=1, power=2, adelanto_enable=False,
                       backlight=2, backlight_on=5, backlight_time=10, buzzer=3,
                       dont_disturb_mode=True, dont_disturb_start=23 * 3600,
                       dont_disturb_end=7 * 3600, dont_disturb_decrease=True)


def test_display_change_preserves_all_companions(device):
    assert prepare_optional_settings(device, {"backlight": 0}) == {
        "backlight": 0, "backlight_on": 5, "backlight_time": 10, "buzzer": 3}


def test_night_change_preserves_overnight_period(device):
    assert prepare_optional_settings(device, {"dont_disturb_decrease": False}) == {
        "dont_disturb_mode": True, "dont_disturb_start": 82800,
        "dont_disturb_end": 25200, "dont_disturb_decrease": False}


@pytest.mark.parametrize("field,value", [("backlight", -1), ("backlight_on", 0),
    ("buzzer", 11), ("buzzer", float("nan")), ("backlight", 2.2),
    ("backlight_time", 15), ("dont_disturb_start", 86400),
    ("dont_disturb_end", -1), ("adelanto_enable", "false")])
def test_reject_invalid_settings_before_transport(device, field, value):
    with pytest.raises(ValueError):
        prepare_optional_settings(device, {field: value})


def test_unreported_fields_cannot_be_written(device):
    with pytest.raises(ValueError, match="not supported"):
        prepare_optional_settings(device, {"silence_mode": True})


def test_actual_nexa_transport_accepts_atomic_optional_payload(device, monkeypatch):
    api = RointeAPI("user", "password", api_type="nexa")
    api.use_nexa_ws = True
    api.firebase_ws_url = "wss://offline.invalid"
    api.auth_token = "offline-token"
    api._ensure_valid_auth = MagicMock(return_value=True)
    messages = mock_socket(monkeypatch)
    try:
        assert api.set_optional_settings(device, {"dont_disturb_start": 22 * 3600}).success
        payload = messages[1]["b"]["d"]
        assert payload["dont_disturb_end"] == 7 * 3600
        assert payload["dont_disturb_start"] == 22 * 3600
        assert "backlight" not in payload
    finally:
        api.close()


@pytest.mark.asyncio
async def test_time_and_timeout_entities_use_local_seconds_and_refresh(device):
    api = SimpleNamespace(set_optional_settings=MagicMock(return_value=ApiResponse(True, None, None)))
    coordinator = SimpleNamespace(devices={"device": device}, rointe_api=api,
                                  async_request_refresh=AsyncMock(), last_update_success=True)
    async def executor(fn, *args):
        return fn(*args)
    hass = SimpleNamespace(async_add_executor_job=executor)
    entity = RointeNightTime(coordinator, device, "dont_disturb_start", "Night Start")
    entity.hass = hass
    assert entity.native_value == time(23)
    await entity.async_set_value(time(22, 30))
    api.set_optional_settings.assert_called_with(device, {"dont_disturb_start": 81000})
    select = RointeDisplayTimeout(coordinator, device)
    select.hass = hass
    assert select.current_option == "10 seconds"
    await select.async_select_option("Always on")
    api.set_optional_settings.assert_called_with(device, {"backlight_time": 0})
    with pytest.raises(HomeAssistantError):
        await select.async_select_option("15 seconds")
    assert coordinator.async_request_refresh.await_count == 2


def test_controls_respect_physical_off_and_limited_mode(device):
    device._data["power"] = 0
    with pytest.raises(ValueError, match="switched off"):
        prepare_optional_settings(device, {"backlight": 2})
    device._data["power"] = 1  # Standby can still be configured.
    assert prepare_optional_settings(device, {"backlight": 2})["backlight"] == 2
    device.user_mode = True
    with pytest.raises(ValueError, match="limited mode"):
        prepare_optional_settings(device, {"adelanto_enable": True})
    assert prepare_optional_settings(device, {"buzzer": 0})["buzzer"] == 0
