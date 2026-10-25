"""Boost uses the app's timer fields, never the obsolete boost_* schema."""

from datetime import datetime, timezone
from types import SimpleNamespace

from custom_components.rointe.climate import RointeHaClimate
from unittest.mock import MagicMock

import pytest

from custom_components.rointe.rointesdk import utils
from custom_components.rointe.rointesdk.rointe_api import RointeAPI, ApiResponse
from test_model_capabilities import make_device


@pytest.fixture
def setup(monkeypatch):
    now = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(utils, "now", lambda: now)
    api = RointeAPI("user", "password", api_type="nexa")
    api.use_nexa_ws = True
    api.set_device_settings = MagicMock(return_value=ApiResponse(True, None, None))
    device = make_device(product_type=1, power=2, timer_mode=False,
                         timer_time=0, timer_temp=23, timer_config_temp=22,
                         timer_config_time=7200)
    yield api, device, int(now.timestamp())
    api.close()


def test_boost_writes_expiry_and_config_atomically(setup):
    api, device, now = setup
    assert api.set_device_boost(device, 3600, 24).success
    payload = api.set_device_settings.call_args.args[1]
    assert payload == {"timer_mode": True, "timer_time": now + 3600,
                       "timer_temp": 24, "timer_config_temp": 24,
                       "timer_config_time": 3600, "mode": 0, "power": 2}
    assert api.set_device_boost(device, 0).success
    assert api.set_device_settings.call_args.args[1] == {"timer_mode": False, "timer_time": 0}


@pytest.mark.parametrize("duration,temp", [(-1, 22), (299, 22), (14401, 22),
                                          (300.5, 22), (3600, float("nan")),
                                          (3600, 31), (3600, 21.2)])
def test_invalid_boost_never_sends(setup, duration, temp):
    api, device, _ = setup
    assert not api.set_device_boost(device, duration, temp).success
    api.set_device_settings.assert_not_called()


def test_missing_or_unused_timer_fields_are_rejected(setup):
    api, _, _ = setup
    assert not api.set_device_boost(make_device(), 3600).success
    assert not api.set_device_boost(make_device(product_type=3, timer_mode=False,
                                               timer_time=0, timer_temp=55), 3600).success
    api.set_device_settings.assert_not_called()


def test_active_expired_and_cancelled_timer_drive_target_and_remaining(setup):
    _, device, now = setup
    data = {**device._data, "timer_mode": True, "timer_time": now + 90,
            "timer_temp": 24, "temp": 19}
    device.update_data({"data": data}, None, None)
    assert device.boost_active
    assert device.boost_countdown == 90
    assert device.target_temperature == 24
    assert RointeHaClimate.target_temperature.fget(SimpleNamespace(_radiator=device)) == 24
    device.timer_time = now - 1
    assert not device.boost_active
    assert device.boost_countdown == 0
    assert device.target_temperature == 19
    device.timer_time = now + 90
    device.timer_mode = False
    assert not device.boost_active


def test_boost_cannot_override_physical_off(setup):
    api, device, _ = setup
    device._data["power"] = 0
    assert not api.set_device_boost(device, 3600, 22).success
    api.set_device_settings.assert_not_called()
