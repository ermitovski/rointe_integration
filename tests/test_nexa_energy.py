"""Exercise device filtering, field parsing, caches and Nexa statistics failures."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
import requests

from custom_components.rointe.rointesdk import rointe_api as module, utils
from custom_components.rointe.rointesdk.rointe_api import RointeAPI, ApiResponse
from custom_components.rointe.rointesdk.dto import EnergyConsumptionData
from custom_components.rointe.sensor_descriptions import SENSOR_DESCRIPTIONS


@pytest.fixture
def stats(monkeypatch):
    clock = [datetime(2026, 10, 25, 23, 59, tzinfo=ZoneInfo("Europe/Madrid"))]
    monkeypatch.setattr(utils, "now", lambda: clock[0])
    api = RointeAPI("user", "password", api_type="nexa")
    api.use_nexa_ws = True
    api.nexa_token = "token"
    api._nexa_device_cache = {
        "one": {"serialNumber": "serial-one"},
        "two": {"serialNumber": "serial-two"},
    }
    data = {
        "data": {
            "consumption": [
                {"time": "2026-10-25 10:00:00", "value": 1.2},
                {"time": "2026-10-25 11:00:00", "value": 0.3},
            ],
            "cost": [{"value": 0}],
            "saving": [{"value": 0.05}],
            "temperature": [
                {"time": "11:00", "value": 21},
                {"time": "10:00", "value": 19},
            ],
            "temperatureProbe": [{"value": 20}],
        }
    }
    response = SimpleNamespace(status_code=200, json=lambda: data)
    get = MagicMock(return_value=response)
    monkeypatch.setattr(module.requests, "get", get)
    return api, clock, data, get


def test_device_filter_and_totals_preserve_zero_cost(stats):
    api, clock, data, get = stats
    result = api.get_latest_energy_stats("one")
    assert result.success
    assert result.data.kwh == 1.5
    assert result.data.cost == 0
    assert result.data.saving == 0.05
    assert result.data.temperature == 21
    assert result.data.temperature_probe == 20
    assert result.data.daily
    assert result.data.start.hour == 0
    assert get.call_args.args[0] == "https://api-v4-prod.rointe.io/api/statistics"
    assert get.call_args.kwargs["params"] == {
        "groupingType": 1,
        "groupingValue": "2026-10-25",
        "filterType": 3,
        "filterValue": "serial-one",
    }
    assert get.call_args.kwargs["headers"]["token"] == "token"
    assert get.call_args.kwargs["timeout"] > 0


def test_cache_is_per_device_expires_and_rolls_over_at_midnight(stats):
    api, clock, data, get = stats
    api.get_latest_energy_stats("one")
    api.get_latest_energy_stats("one")
    assert get.call_count == 1
    api.get_latest_energy_stats("two")
    assert get.call_count == 2
    clock[0] += timedelta(minutes=2)
    api.get_latest_energy_stats("one")
    assert get.call_count == 3  # Next local day bypasses the five-minute cache.
    clock[0] += timedelta(minutes=6)
    api.get_latest_energy_stats("one")
    assert get.call_count == 4


def test_concurrent_requests_for_same_device_share_cache(stats):
    api, clock, data, get = stats
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(
            executor.map(lambda _: api.get_latest_energy_stats("one"), range(4))
        )
    assert all(result.success for result in results)
    assert get.call_count == 1


def test_expired_token_reauthenticates_once(stats):
    api, clock, data, get = stats
    get.side_effect = [
        SimpleNamespace(status_code=401),
        SimpleNamespace(status_code=200, json=lambda: data),
    ]

    def login():
        api.nexa_token = "renewed"
        return True

    api._re_authenticate = MagicMock(side_effect=login)
    assert api.get_latest_energy_stats("one").success
    api._re_authenticate.assert_called_once()
    assert get.call_args.kwargs["headers"]["token"] == "renewed"


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"data": []},
        {"data": {}},
        {"data": {"consumption": []}},
        {"data": {"consumption": [{"value": "bad"}]}},
        {"data": {"consumption": [{"value": float("inf")}]}},
        {"data": {"consumption": [None]}},
    ],
)
def test_invalid_or_absent_data_does_not_fabricate_consumption(stats, payload):
    api, clock, data, get = stats
    get.return_value = SimpleNamespace(status_code=200, json=lambda: payload)
    assert not api.get_latest_energy_stats("one").success
    assert not api._nexa_energy_cache


def test_network_error_and_missing_serial_are_reported(stats):
    api, clock, data, get = stats
    get.side_effect = requests.RequestException("Offline")
    assert not api.get_latest_energy_stats("one").success
    get.reset_mock()
    assert not api.get_latest_energy_stats("unknown").success
    get.assert_not_called()


def test_cost_and_savings_sensor_units_and_zero_values(stats):
    api, clock, data, get = stats
    device = SimpleNamespace(energy_data=api.get_latest_energy_stats("one").data)
    for key, expected in [("energy_cost", 0), ("energy_saving", 0.05)]:
        desc = next(d for d in SENSOR_DESCRIPTIONS if d.key == key)
        assert desc.available_fn(device)
        assert desc.value_fn(device) == expected
        assert desc.native_unit_of_measurement == "EUR"
        assert desc.state_class == "total"
        assert desc.last_reset_fn(device) == device.energy_data.start


def test_legacy_statistics_path_is_unchanged(stats):
    api, clock, data, get = stats
    api.use_nexa_ws = False
    result = ApiResponse(True, "legacy", None)
    api._retrieve_hour_energy_stats = MagicMock(return_value=result)
    assert api.get_latest_energy_stats("one") == result
    get.assert_not_called()
    legacy = EnergyConsumptionData(clock[0], clock[0], 0, 0, clock[0])
    assert legacy.cost is None and not legacy.daily
