"""Holiday writes respect installation scope, UTC times and toggle semantics."""

from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest
import requests

from custom_components.rointe import services
from custom_components.rointe.rointesdk.holidays import holiday_payload
from custom_components.rointe.rointesdk.rointe_api import RointeAPI, ApiResponse

PERIODS = [{"from": "2026-10-25T01:00:00+02:00", "to": "2026-10-25T03:00:00+01:00"}]


def response(flag):
    return SimpleNamespace(status_code=200, json=lambda: {"data": {"holidaysMode": flag}})


@pytest.fixture
def api():
    client = RointeAPI("user", "password", api_type="nexa")
    client.use_nexa_ws = True
    client.nexa_token = "token"
    client.get_installation_by_id = MagicMock(return_value=ApiResponse(True, {"holidaysMode": False}, None))
    yield client
    client.close()


def test_utc_conversion_across_dst_and_indefinite_period():
    assert holiday_payload(PERIODS) == [{"from": "2026-10-24 23:00:00", "to": "2026-10-25 02:00:00"}]
    assert holiday_payload([{"from": "2026-12-01T00:00:00Z"}])[0]["to"] is None


@pytest.mark.parametrize("periods", [None, [{"from": "2026-10-25T01:00:00"}],
    [{"from": "2026-12-01T00:00:00Z", "to": "2026-11-30T00:00:00Z"}],
    [{"from": "2026-12-01T00:00:00Z"}, {"from": "2026-12-02T00:00:00Z"}],
    [PERIODS[0], PERIODS[0]], [{"from": "invalid"}]])
def test_invalid_periods_never_call_cloud(api, periods):
    api._nexa_holiday_request = MagicMock()
    assert not api.set_holiday_schedule("installation", periods).success
    api.get_installation_by_id.assert_not_called()
    api._nexa_holiday_request.assert_not_called()


@pytest.mark.parametrize("existing,expected", [(False, ["PUT", "PATCH"]), (True, ["PUT"])])
def test_enable_is_idempotent_even_though_patch_toggles(api, monkeypatch, existing, expected):
    request = MagicMock(side_effect=[response(existing), response(True)])
    monkeypatch.setattr(requests, "request", request)
    assert api.set_holiday_schedule("installation", PERIODS).success
    assert [c.args[0] for c in request.call_args_list] == expected
    assert request.call_args_list[0].kwargs["json"] == {"holidays": holiday_payload(PERIODS)}
    if not existing:
        assert "json" not in request.call_args_list[1].kwargs


def test_clear_does_not_toggle_when_put_already_disabled(api, monkeypatch):
    request = MagicMock(return_value=response(False))
    monkeypatch.setattr(requests, "request", request)
    assert api.set_holiday_schedule("installation", []).success
    request.assert_called_once()


def test_unknown_flag_never_blindly_toggles(api, monkeypatch):
    api.get_installation_by_id.return_value = ApiResponse(True, {}, None)
    request = MagicMock(return_value=SimpleNamespace(status_code=200, json=lambda: {"data": {}}))
    monkeypatch.setattr(requests, "request", request)
    assert not api.set_holiday_schedule("installation", PERIODS).success
    assert request.call_count == 1


def test_uncertain_toggle_is_not_replayed(api, monkeypatch):
    request = MagicMock(side_effect=[response(False), requests.Timeout()])
    monkeypatch.setattr(requests, "request", request)
    result = api.set_holiday_schedule("installation", PERIODS)
    assert not result.success
    assert "unknown" in result.error_message
    assert request.call_count == 2


def test_explicit_401_renews_token_once(api, monkeypatch):
    request = MagicMock(side_effect=[SimpleNamespace(status_code=401), response(True)])
    monkeypatch.setattr(requests, "request", request)
    def refresh(token):
        api.nexa_token = "renewed"
        return True
    api._refresh_nexa_credentials = MagicMock(side_effect=refresh)
    assert api.set_holiday_schedule("installation", PERIODS).success
    assert request.call_args_list[1].kwargs["headers"]["token"] == "renewed"
    assert request.call_count == 2


@pytest.mark.asyncio
async def test_multiple_heaters_in_one_installation_apply_once(monkeypatch):
    handlers = {}
    registry = SimpleNamespace(async_get=lambda entity_id: SimpleNamespace(
        config_entry_id="entry", unique_id=entity_id.split(".")[1]))
    monkeypatch.setattr(services.er, "async_get", lambda _: registry)
    api = SimpleNamespace(set_holiday_schedule=MagicMock(return_value=ApiResponse(True, {}, None)))
    coordinator = SimpleNamespace(rointe_api=api, async_request_refresh=AsyncMock(), devices={
        key: SimpleNamespace(id=key, installation_id="same") for key in ("a", "b")})
    async def executor(fn, *args):
        return fn(*args)
    hass = SimpleNamespace(data={"rointe": {"entry": coordinator}}, async_add_executor_job=executor,
        services=SimpleNamespace(async_register=lambda _, name, fn, **kw: handlers.update({name: fn})))
    await services.async_setup_services(hass)
    await handlers["set_holiday_schedule"](SimpleNamespace(data={
        "entity_id": ["climate.a", "climate.b"], "periods": PERIODS}))
    api.set_holiday_schedule.assert_called_once_with("same", PERIODS)
    coordinator.async_request_refresh.assert_awaited_once()
