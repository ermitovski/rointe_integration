"""Temperature controls expose their declared limits and optional user limits."""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.rointe.number import NUMBER_DESCRIPTIONS, RointeNumber


def number(key, user_mode=False):
    device = SimpleNamespace(
        id="device", user_mode=user_mode, um_min_temp=12, um_max_temp=25,
    )
    coordinator = SimpleNamespace(devices={device.id: device}, async_add_listener=MagicMock())
    description = next(d for d in NUMBER_DESCRIPTIONS if d.key == key)
    return RointeNumber(device, coordinator, description)


@pytest.mark.parametrize("key,minimum,maximum", [
    ("comfort_temperature", 7, 30),
    ("eco_temperature", 7, 30),
    ("ice_temperature", 5, 15),
    ("max_temperature", 15, 35),
    ("min_temperature", 5, 20),
])
def test_controls_use_their_own_declared_bounds(key, minimum, maximum):
    entity = number(key)
    assert (entity.native_min_value, entity.native_max_value) == (minimum, maximum)


@pytest.mark.parametrize("key", ["comfort_temperature", "eco_temperature"])
def test_user_mode_bounds_update_with_the_device(key):
    entity = number(key, user_mode=True)
    assert (entity.native_min_value, entity.native_max_value) == (12, 25)
    entity._radiator.um_min_temp = 10
    entity._radiator.um_max_temp = 24
    assert (entity.native_min_value, entity.native_max_value) == (10, 24)
    entity._radiator.user_mode = False
    assert (entity.native_min_value, entity.native_max_value) == (7, 30)


@pytest.mark.parametrize("key,minimum,maximum", [
    ("ice_temperature", 5, 15),
    ("max_temperature", 15, 35),
    ("min_temperature", 5, 20),
])
def test_user_mode_does_not_override_fixed_or_limit_controls(key, minimum, maximum):
    entity = number(key, user_mode=True)
    assert (entity.native_min_value, entity.native_max_value) == (minimum, maximum)


@pytest.mark.parametrize("key", [d.key for d in NUMBER_DESCRIPTIONS])
def test_missing_device_uses_description_bounds(key):
    entity = number(key, user_mode=True)
    entity._radiator = None
    assert entity.native_min_value == entity.entity_description.native_min_value
    assert entity.native_max_value == entity.entity_description.native_max_value


@pytest.mark.parametrize("missing_device", [False, True])
def test_zero_is_a_valid_description_bound(missing_device):
    entity = number("ice_temperature")
    entity.entity_description = replace(
        entity.entity_description, native_min_value=0, native_max_value=0,
    )
    if missing_device:
        entity._radiator = None
    assert entity.native_min_value == 0
    assert entity.native_max_value == 0
