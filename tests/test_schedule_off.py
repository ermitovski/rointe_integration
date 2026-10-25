"""Keep climate, power and heating sensors aligned during schedule off slots."""

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from homeassistant.components.climate import HVACAction
from custom_components.rointe.climate import RointeHaClimate
from custom_components.rointe.rointesdk.device import RointeDevice
from custom_components.rointe.rointesdk.model import ScheduleMode
from custom_components.rointe.rointesdk import utils
from custom_components.rointe.sensor_descriptions import (
    _get_effective_power,
    _get_heating_status,
    SENSOR_DESCRIPTIONS,
)
from custom_components.rointe.binary_sensor import BINARY_SENSOR_DESCRIPTIONS


@pytest.fixture
def device(monkeypatch):
    monkeypatch.setattr(
        utils,
        "now",
        lambda: datetime(2026, 10, 26, 12, tzinfo=ZoneInfo("Europe/Madrid")),
    )
    return RointeDevice(
        device_info={
            "serialnumber": "serial",
            "data": {
                "type": "radiator",
                "product_version": "v2",
                "name": "Test",
                "power": 2,
                "mode": 1,
                "temp": 22,
                "temp_probe": 5,
                "comfort": 21,
                "eco": 18,
                "ice": 8,
                "schedule": {"0": "O" * 24},
                "ice_mode": False,
                "status_warming": 2,
                "nominal_power": 1000,
            },
        },
        device_id="device",
        energy_data=None,
        latest_fw=None,
    )


def climate(device):
    return SimpleNamespace(_radiator=device)


def test_off_slot_without_frost_requests_no_heat(device):
    assert device.get_effective_target_temperature() is None
    assert not device.heating
    assert RointeHaClimate.hvac_action.fget(climate(device)) == HVACAction.IDLE
    assert RointeHaClimate.preset_mode.fget(climate(device)) is None
    assert _get_effective_power(device) == 0
    assert _get_heating_status(device) == "idle"
    desc = next(d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == "heating")
    assert not desc.value_fn(device)


def test_off_slot_with_frost_uses_ice_target(device):
    device.ice_mode = True
    assert device.target_temperature == 8
    assert device.heating
    assert RointeHaClimate.hvac_action.fget(climate(device)) == HVACAction.HEATING
    assert RointeHaClimate.preset_mode.fget(climate(device)) == "ice"
    assert _get_effective_power(device) == 1000


@pytest.mark.parametrize("slot,target", [("C", 21), ("E", 18)])
@pytest.mark.parametrize("format", ["dict", "list"])
def test_schedule_targets_apply_to_climate_and_sensor(device, slot, target, format):
    device.schedule = {"0": slot * 24} if format == "dict" else [slot * 24] * 7
    device.status_warming = 0
    assert device.target_temperature == target
    assert RointeHaClimate.target_temperature.fget(climate(device)) == target
    desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == "target_temperature")
    assert desc.value_fn(device) == target
    assert _get_heating_status(device) == "heating"


def test_power_off_and_manual_modes(device):
    device.mode = "manual"
    assert device.target_temperature == 22
    device.power = False
    assert device.target_temperature is None
    assert not device.heating
    assert _get_effective_power(device) == 0
    assert _get_heating_status(device) == "off"
    assert RointeHaClimate.hvac_action.fget(climate(device)) == HVACAction.OFF


def test_schedule_uses_local_day_and_hour(device):
    device.schedule = {"0": "O" * 24, "6": "O" * 23 + "C"}
    # Monday UTC is still Sunday in this device's configured timezone.
    when = datetime(2026, 10, 25, 23, 30, tzinfo=ZoneInfo("America/New_York"))
    assert device.get_current_schedule_mode(when) == ScheduleMode.COMFORT
