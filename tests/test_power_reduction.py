"""Nexa installed-power adjustment follows model gating and nominal wattage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError

from custom_components.rointe import services
from custom_components.rointe.number import NUMBER_DESCRIPTIONS, RointeNumber
from custom_components.rointe.sensor_descriptions import SENSOR_DESCRIPTIONS
from custom_components.rointe.rointesdk.device import RointeDevice
from custom_components.rointe.rointesdk.power import (
    POWER_ADJUSTABLE_MODELS,
    reduction_percentage,
    supports_power_reduction,
)
from custom_components.rointe.rointesdk.rointe_api import ApiResponse, RointeAPI
from test_commands import mock_socket


def radiator(device_id="a", installation="home", **data):
    return RointeDevice(
        device_id,
        {
            "serialnumber": f"serial-{device_id}",
            "metadata": {"installation_id": installation},
            "data": {
                "type": "radiator",
                "product_type": 1,
                "product_brand": 1,
                "product_model": 20,
                "product_version": "v2",
                "nominal_power": 1500,
                "nominal_effective_power": 1500,
                "temp": 21,
                "power": 2,
                **data,
            },
        },
        None,
        None,
    )


@pytest.fixture
def api(monkeypatch):
    client = RointeAPI("user", "password", api_type="nexa")
    client.use_nexa_ws = True
    client.firebase_ws_url = "wss://offline.invalid"
    client.auth_token = "offline-token"
    client._ensure_valid_auth = MagicMock(return_value=True)
    client.get_device = MagicMock(
        return_value=ApiResponse(True, {"data": radiator()._data}, None)
    )
    messages = mock_socket(monkeypatch)
    yield client, messages
    client.close()


@pytest.mark.parametrize("model", sorted(POWER_ADJUSTABLE_MODELS))
def test_only_app_model_allowlist_enables_control(model):
    device = radiator(product_model=model)
    assert device.supports_setting("nominal_effective_power")
    description = next(d for d in NUMBER_DESCRIPTIONS if d.key == "power_reduction")
    assert description.available_fn(device)


@pytest.mark.parametrize(
    "override",
    [
        {"product_type": 2},
        {"product_type": 5},
        {"product_brand": 2},
        {"product_brand": None},
        {"product_model": 24},
        {"product_model": 1},
        {"product_model": 99},
        {"product_model": "20"},
        {"product_brand": True},
        {"product_type": None},
        {"product_type": 99},
    ],
)
def test_shared_fields_on_other_models_do_not_enable_power_reduction(override):
    assert not radiator(**override).supports_setting("nominal_effective_power")


@pytest.mark.parametrize(
    "field,value",
    [
        ("nominal_power", None),
        ("nominal_power", 0),
        ("nominal_power", float("nan")),
        ("nominal_power", True),
        ("nominal_effective_power", None),
        ("nominal_effective_power", 0),
        ("nominal_effective_power", float("inf")),
    ],
)
def test_missing_or_invalid_power_ratings_disable_control(field, value):
    data = {**radiator()._data, field: value}
    assert not supports_power_reduction(data)
    assert reduction_percentage(data) is None


@pytest.mark.parametrize(
    "reduction,watts",
    [(0, 1500), (5, 1425), (10, 1350), (15, 1275), (20, 1200), (25, 1125), (30, 1050)],
)
def test_all_seven_adjustments_use_original_nominal_rating(api, reduction, watts):
    client, messages = api
    device = radiator(nominal_effective_power=1200)
    client.get_device.return_value = ApiResponse(True, {"data": device._data}, None)
    assert client.set_device_power_reduction(device, reduction).success
    client.get_device.assert_called_once_with("a")
    assert messages[1]["b"]["p"] == "/devices/serial-a/data"
    payload = messages[1]["b"]["d"]
    assert payload.pop("last_sync_datetime_app") > 0
    assert payload == {"nominal_effective_power": watts}


@pytest.mark.parametrize(
    "value", [-5, 35, 2.5, 7, float("nan"), float("inf"), None, True, False]
)
def test_invalid_percentage_never_reads_or_writes(api, value):
    client, messages = api
    assert not client.set_device_power_reduction(radiator(), value).success
    client.get_device.assert_not_called()
    assert messages == []


def test_latest_reported_model_and_nominal_rating_are_used(api):
    client, messages = api
    device = radiator()
    client.get_device.return_value = ApiResponse(
        True, {"data": {**device._data, "nominal_power": 1350}}, None
    )
    assert client.set_device_power_reduction(device, 5).success
    assert messages[1]["b"]["d"]["nominal_effective_power"] == 1282.5


def test_fractional_configured_watts_survive_model_and_sensor():
    device = radiator(nominal_power=1350, nominal_effective_power=1282.5)
    assert device.nominal_effective_power == 1282.5
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "configured_power")
    assert description.value_fn(device) == 1282.5
    number = next(d for d in NUMBER_DESCRIPTIONS if d.key == "power_reduction")
    assert number.value_fn(device) == 5
    assert (number.native_min_value, number.native_max_value, number.native_step) == (
        0,
        30,
        5,
    )


def test_model_that_changed_or_is_unsupported_is_not_written(api):
    client, messages = api
    assert not client.set_device_power_reduction(radiator(product_model=24), 20).success
    client.get_device.assert_not_called()
    client.get_device.return_value = ApiResponse(
        True, {"data": radiator(product_model=24)._data}, None
    )
    assert not client.set_device_power_reduction(radiator(), 20).success
    assert messages == []


def test_legacy_cannot_use_power_adjustment(api):
    client, messages = api
    client.use_nexa_ws = False
    assert not client.set_device_power_reduction(radiator(), 20).success
    client.get_device.assert_not_called()
    assert messages == []


def test_installation_action_filters_and_preflights_all_devices(api):
    client, messages = api
    devices = [
        radiator("a"),
        radiator("b", nominal_power=1000),
        radiator("unsupported", product_model=24),
        radiator("other", installation="other"),
    ]
    client.get_device.side_effect = [
        ApiResponse(True, {"data": d._data}, None) for d in devices[:2]
    ]
    result = client.set_installation_power_reduction("home", devices + [devices[0]], 20)
    assert result.success
    assert result.data["updated_device_ids"] == ["a", "b"]
    writes = [m for m in messages if m["a"] == "m"]
    assert [w["b"]["p"] for w in writes] == [
        "/devices/serial-a/data",
        "/devices/serial-b/data",
    ]
    assert [w["b"]["d"]["nominal_effective_power"] for w in writes] == [1200, 800]
    assert client.get_device.call_count == 2


def test_failed_preflight_leaves_whole_installation_unchanged(api):
    client, messages = api
    client.get_device.side_effect = [
        ApiResponse(True, {"data": radiator()._data}, None),
        ApiResponse(False, None, "Unable to read"),
    ]
    result = client.set_installation_power_reduction(
        "home", [radiator("a"), radiator("b")], 20
    )
    assert not result.success
    assert "No power adjustments sent" in result.error_message
    assert messages == []


def test_partial_write_failure_reports_acknowledged_devices(api):
    client, _ = api
    client._write_via_websocket = MagicMock(
        side_effect=[ApiResponse(True, None, None), ApiResponse(False, None, "Denied")]
    )
    result = client.set_installation_power_reduction(
        "home", [radiator("a"), radiator("b"), radiator("c")], 20
    )
    assert not result.success
    assert result.data["updated_device_ids"] == ["a"]
    assert "1 earlier device" in result.error_message
    assert client._write_via_websocket.call_count == 2


def test_empty_or_unsupported_installation_does_not_write(api):
    client, messages = api
    assert not client.set_installation_power_reduction(
        "home", [radiator(product_model=24)], 20
    ).success
    assert not client.set_installation_power_reduction(None, [radiator()], 20).success
    client.get_device.assert_not_called()
    assert messages == []


@pytest.mark.asyncio
async def test_number_routes_percentage_to_dedicated_api_and_refreshes():
    device = radiator()
    api = SimpleNamespace(
        set_device_power_reduction=MagicMock(return_value=ApiResponse(True, None, None))
    )
    coordinator = SimpleNamespace(
        devices={"a": device}, rointe_api=api, async_request_refresh=AsyncMock()
    )

    async def executor(fn, *args):
        return fn(*args)

    entity = RointeNumber(
        device,
        coordinator,
        next(d for d in NUMBER_DESCRIPTIONS if d.key == "power_reduction"),
    )
    entity.hass = SimpleNamespace(async_add_executor_job=executor)
    await entity.async_set_native_value(20)
    api.set_device_power_reduction.assert_called_once_with(device, 20)
    coordinator.async_request_refresh.assert_awaited_once()
    api.set_device_power_reduction.return_value = ApiResponse(False, None, "Denied")
    with pytest.raises(HomeAssistantError, match="Denied"):
        await entity.async_set_native_value(30)


@pytest.mark.asyncio
@pytest.mark.parametrize("success", [True, False])
async def test_installation_service_deduplicates_and_refreshes_partial_changes(
    monkeypatch, success
):
    handlers, schemas = {}, {}
    registry = SimpleNamespace(
        async_get=lambda entity_id: SimpleNamespace(
            config_entry_id="entry", unique_id=entity_id.split(".")[1]
        )
    )
    monkeypatch.setattr(services.er, "async_get", lambda _: registry)
    api = SimpleNamespace(
        use_nexa_ws=True,
        set_installation_power_reduction=MagicMock(
            return_value=ApiResponse(
                success, None, None if success else "Partial change"
            )
        ),
    )
    devices = {key: radiator(key) for key in ("a", "b")}
    coordinator = SimpleNamespace(
        devices=devices, rointe_api=api, async_request_refresh=AsyncMock()
    )

    async def executor(fn, *args):
        return fn(*args)

    def register(_, name, fn, schema):
        handlers[name], schemas[name] = fn, schema

    hass = SimpleNamespace(
        data={"rointe": {"entry": coordinator}},
        async_add_executor_job=executor,
        services=SimpleNamespace(async_register=register),
    )
    await services.async_setup_services(hass)
    call = SimpleNamespace(
        data={"entity_id": ["climate.a", "climate.b"], "reduction": 20}
    )
    if success:
        await handlers["set_installation_power_reduction"](call)
    else:
        with pytest.raises(HomeAssistantError, match="Partial change"):
            await handlers["set_installation_power_reduction"](call)
    api.set_installation_power_reduction.assert_called_once_with(
        "home", tuple(devices.values()), 20
    )
    coordinator.async_request_refresh.assert_awaited_once()
    for invalid in (False, True, float("nan"), 7):
        with pytest.raises(vol.Invalid):
            schemas["set_installation_power_reduction"](
                {**call.data, "reduction": invalid}
            )


@pytest.mark.parametrize("status", ["error", None])
def test_rejected_or_unacknowledged_power_write_is_failure(api, monkeypatch, status):
    client, _ = api
    mock_socket(monkeypatch, status)
    assert not client.set_device_power_reduction(radiator(), 20).success
