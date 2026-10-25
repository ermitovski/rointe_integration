"""Regression tests for settings delivery and Firebase acknowledgements."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, AsyncMock

import pytest
import websocket

from homeassistant.exceptions import HomeAssistantError
from custom_components.rointe.rointesdk.rointe_api import RointeAPI, ApiResponse
from custom_components.rointe import services


@pytest.fixture
def api():
    client = RointeAPI("user", "password", api_type="nexa")
    client.use_nexa_ws = True
    client.firebase_ws_url = "wss://offline.invalid"
    client.auth_token = "offline-token"
    client._ensure_valid_auth = MagicMock(return_value=True)
    return client


def mock_socket(monkeypatch, status="ok"):
    messages = []

    class Socket:
        sock = True

        def __init__(self, url, **callbacks):
            self.callbacks = callbacks
            self.done = threading.Event()

        def run_forever(self, **kwargs):
            self.callbacks["on_open"](self)
            self.done.wait(2)

        def send(self, raw):
            request = json.loads(raw)["d"]
            messages.append(request)
            if request["a"] == "auth":
                result = "ok"
            elif status is None:
                self.callbacks["on_close"](self, 1006, "Connection lost")
                return
            else:
                result = status
            self.callbacks["on_message"](
                self,
                json.dumps({"t": "d", "d": {"r": request["r"], "b": {"s": result}}}),
            )

        def close(self):
            self.done.set()

    monkeypatch.setattr(websocket, "WebSocketApp", Socket)
    return messages


@pytest.mark.parametrize(
    "body",
    [
        {"boost_active": True, "boost_countdown": 3600},
        {"boost_active": False, "boost_countdown": 0},
        {"block_local": 1, "block_remote": 0},
        {"windows_open_mode": 1, "silence_mode": 0, "pir_mode": 1},
        {"dont_disturb_mode": 1, "ice_mode": False, "user_mode": True},
        {"um_min_temp": 10, "um_max_temp": 25},
        {
            "temp": 21,
            "comfort": 22,
            "eco": 18,
            "ice": 8,
            "mode": 1,
            "power": 2,
            "status": "none",
        },
    ],
)
def test_settings_are_sent_atomically(api, monkeypatch, body):
    messages = mock_socket(monkeypatch)
    result = api._write_via_websocket("serial", body)
    assert result.success
    assert [message["a"] for message in messages] == ["auth", "m"]
    assert messages[1]["b"]["p"] == "/devices/serial/data"
    payload = messages[1]["b"]["d"]
    assert payload.pop("last_sync_datetime_app") > 0
    assert payload == body


def test_partial_schedule_preserves_other_days(api, monkeypatch):
    messages = mock_socket(monkeypatch)
    assert api._write_via_websocket("serial", {"schedule": {0: "c" * 24}}).success
    payload = messages[1]["b"]["d"]
    assert payload["schedule/0"] == "C" * 24
    assert "schedule" not in payload
    assert "schedule/1" not in payload


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"unsupported": 1},
        {"temp": 21, "unsupported": 1},
        {"schedule": {"7": "C" * 24}},
        {"schedule": {"0": "C"}},
        {"schedule": {"0": "X" * 24}},
        {"schedule": {}},
        {"temp": float("nan")},
    ],
)
def test_invalid_updates_fail_before_opening_socket(api, monkeypatch, body):
    constructor = MagicMock()
    monkeypatch.setattr(websocket, "WebSocketApp", constructor)
    assert not api._write_via_websocket("serial", body).success
    constructor.assert_not_called()


@pytest.mark.parametrize("status", ["error", None])
def test_rejected_or_unacknowledged_write_is_not_success(api, monkeypatch, status):
    mock_socket(monkeypatch, status)
    assert not api._write_via_websocket("serial", {"block_local": 1}).success


def test_permission_denied_retries_only_once(api, monkeypatch):
    messages = mock_socket(monkeypatch, "permission_denied")
    assert not api._write_via_websocket("serial", {"block_local": 1}).success
    assert len(messages) == 4


def test_legacy_settings_use_rest_and_preserve_schedule_days(api):
    api.use_nexa_ws = False
    api._send_patch_request = MagicMock(return_value=ApiResponse(True, None, None))
    device = SimpleNamespace(id="device", schedule=["E" * 24] * 7)
    assert api.set_device_settings(
        device, {"schedule": {"0": "C" * 24}, "block_local": True}
    ).success
    body = api._send_patch_request.call_args.args[2]
    assert body["schedule"] == ["C" * 24] + ["E" * 24] * 6
    assert body["block_local"] is True
    assert not api.set_device_settings(device, {"boost_active": True}).success


@pytest.mark.asyncio
async def test_services_resolve_climate_id_and_propagate_rejection(monkeypatch):
    handlers = {}
    registry = SimpleNamespace(
        async_get=lambda _: SimpleNamespace(config_entry_id="entry", unique_id="device")
    )
    monkeypatch.setattr(services.er, "async_get", lambda _: registry)
    api = SimpleNamespace(
        set_device_settings=MagicMock(return_value=ApiResponse(False, None, "Denied"))
    )
    device = SimpleNamespace(id="device")
    coordinator = SimpleNamespace(
        devices={"device": device}, rointe_api=api, async_request_refresh=AsyncMock()
    )

    async def executor(fn, *args):
        return fn(*args)

    hass = SimpleNamespace(
        data={"rointe": {"entry": coordinator}},
        async_add_executor_job=executor,
        services=SimpleNamespace(
            async_register=lambda name, service, handler, **kwargs: handlers.update(
                {service: handler}
            )
        ),
    )
    await services.async_setup_services(hass)
    with pytest.raises(HomeAssistantError, match="Denied"):
        await handlers["activate_boost"](
            SimpleNamespace(data={"entity_id": ["climate.test"], "duration": 60})
        )
    api.set_device_settings.assert_called_once_with(
        device, {"boost_active": True, "boost_countdown": 3600}
    )
    coordinator.async_request_refresh.assert_not_awaited()
