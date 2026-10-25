"""Persistent energy totals survive calendar resets, corrections and restarts."""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from custom_components.rointe.energy import DailyEnergyTotal
from custom_components.rointe.sensor import (
    RointeSensor,
    RointeCumulativeEnergySensor,
    _create_sensor,
)
from custom_components.rointe.sensor_descriptions import SENSOR_DESCRIPTIONS
from custom_components.rointe.rointesdk.dto import EnergyConsumptionData


DAY = date(2026, 10, 25)
NEXT = date(2026, 10, 26)


def test_calendar_rollover_accumulates_even_when_new_daily_value_is_larger():
    total = DailyEnergyTotal()
    assert total.update(DAY, 2) == 2
    assert total.update(DAY, 5) == 5
    assert total.update(NEXT, 7) == 12
    assert total.update(NEXT, 8) == 13


def test_same_day_corrections_and_repeated_samples_do_not_inflate_total():
    total = DailyEnergyTotal()
    assert [total.update(DAY, v) for v in (3, 3, 2, 3, 4)] == [3, 3, 3, 3, 4]
    assert total.update(NEXT, 0) == 4
    assert total.update(NEXT, 1) == 5
    assert total.update(DAY, 100) == 5


def test_restart_restores_offset_and_last_period():
    total = DailyEnergyTotal()
    total.update(DAY, 4)
    total.update(NEXT, 2)
    restored = DailyEnergyTotal.restore(total.attributes())
    assert restored.update(NEXT, 3) == 7
    assert restored.update(date(2026, 10, 27), 1) == 8


@pytest.mark.parametrize(
    "attributes",
    [
        {},
        {"energy_period_date": "bad"},
        {
            "energy_period_date": "2026-10-25",
            "cumulative_offset": float("nan"),
            "last_daily_value": 3,
        },
        {
            "energy_period_date": "2026-10-25",
            "cumulative_offset": -1,
            "last_daily_value": 3,
        },
    ],
)
def test_corrupt_saved_state_is_discarded(attributes):
    assert DailyEnergyTotal.restore(attributes).update(DAY, 2) == 2


@pytest.mark.parametrize("value", [-1, float("inf"), float("nan")])
def test_invalid_energy_cannot_change_counters(value):
    total = DailyEnergyTotal()
    total.update(DAY, 3)
    with pytest.raises(ValueError):
        total.update(NEXT, value)
    assert total.value == 3


def make_sensor():
    timestamp = datetime(2026, 10, 26, tzinfo=timezone.utc)
    data = EnergyConsumptionData(timestamp, timestamp, 2, 0, timestamp, daily=True)
    device = SimpleNamespace(id="one", energy_data=data, hass_available=True)
    coordinator = SimpleNamespace(
        devices={"one": device},
        last_update_success=True,
        rointe_api=SimpleNamespace(use_nexa_ws=True),
    )
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "energy")
    return (
        _create_sensor(device, coordinator, description),
        device,
        coordinator,
        description,
    )


@pytest.mark.asyncio
async def test_sensor_restores_before_its_first_published_value(monkeypatch):
    sensor, device, coordinator, description = make_sensor()
    assert isinstance(sensor, RointeCumulativeEnergySensor)
    assert sensor.unique_id == "one_energy"
    assert (
        sensor.native_value == 2
    )  # Valid immediately, before the next coordinator refresh.
    restored = DailyEnergyTotal(DAY, 10, 4)
    monkeypatch.setattr(RointeSensor, "async_added_to_hass", AsyncMock())
    monkeypatch.setattr(
        sensor,
        "async_get_last_state",
        AsyncMock(return_value=SimpleNamespace(attributes=restored.attributes())),
    )
    await sensor.async_added_to_hass()
    assert sensor.native_value == 16
    assert sensor.extra_state_attributes["energy_period_date"] == "2026-10-26"
    device.energy_data = None
    assert sensor.native_value is None
    assert sensor.extra_state_attributes["cumulative_offset"] == 14


def test_legacy_sensor_keeps_original_energy_behavior():
    sensor, device, coordinator, description = make_sensor()
    coordinator.rointe_api.use_nexa_ws = False
    legacy = _create_sensor(device, coordinator, description)
    assert type(legacy) is RointeSensor
    assert legacy.unique_id == "one_energy"
    assert legacy.native_value == 2
