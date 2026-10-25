"""Persistent Nexa transport regressions; all sockets and tokens stay offline."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import websocket

import custom_components.rointe as integration
from custom_components.rointe.rointesdk import nexa_ws
from custom_components.rointe.rointesdk.rointe_api import ApiResponse, RointeAPI


@pytest.fixture
def sockets(monkeypatch):
    """A socket stays open until explicitly closed, just like the real receiver."""
    instances = []

    class Socket:
        handler = None
        open_immediately = True

        def __init__(self, url, **callbacks):
            self.url = url
            self.callbacks = callbacks
            self.done = threading.Event()
            self.started = threading.Event()
            self.messages = []
            self.pinged = threading.Event()
            instances.append(self)

        def run_forever(self, **kwargs):
            self.started.set()
            if self.open_immediately:
                self.callbacks["on_open"](self)
            self.done.wait()

        def respond(self, request, body):
            self.callbacks["on_message"](
                self, json.dumps({"t": "d", "d": {"r": request["r"], "b": body}})
            )

        def send(self, raw):
            if self.done.is_set():
                raise OSError("socket closed")
            if raw == "0":
                self.pinged.set()
                self.callbacks["on_message"](self, "0")
                return
            request = json.loads(raw)["d"]
            self.messages.append(request)
            if Socket.handler is not None:
                body = Socket.handler(self, request)
            else:
                body = {"s": "ok", "d": request["b"].get("p")}
            if body is not None:
                self.respond(request, body)

        def close(self):
            self.done.set()
            self.callbacks["on_close"](self, 1000, "closed")

    monkeypatch.setattr(websocket, "WebSocketApp", Socket)
    yield SimpleNamespace(instances=instances, Socket=Socket)
    assert all(socket.done.is_set() for socket in instances)


@pytest.fixture
def transport(sockets):
    client = nexa_ws.NexaWebSocket("wss://offline.invalid", lambda: "token")
    yield client
    client.close()
    assert not client._keepalive_thread or not client._keepalive_thread.is_alive()


@pytest.fixture
def api(sockets):
    client = RointeAPI("user", "password", api_type="nexa")
    client.firebase_ws_url = "wss://offline.invalid"
    client.auth_token = "token"
    client._ensure_valid_auth = MagicMock(return_value=True)
    yield client
    client.close()


def test_reads_and_atomic_writes_share_one_authentication(api, sockets):
    assert api._read_via_websocket("/one") == "/one"
    assert api._write_via_websocket("serial", {"boost_active": True}).success
    assert api._read_via_websocket("/two") == "/two"
    assert len(sockets.instances) == 1
    messages = sockets.instances[0].messages
    assert [message["a"] for message in messages] == ["auth", "g", "m", "g"]
    assert messages[2]["b"]["d"]["boost_active"] is True


def test_concurrent_device_polls_share_transport_and_match_responses(api, sockets):
    barrier = threading.Barrier(8)

    def read(index):
        barrier.wait(timeout=2)
        return api._read_via_websocket(f"/device/{index}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert list(executor.map(read, range(8))) == [f"/device/{i}" for i in range(8)]
    assert len(sockets.instances) == 1
    messages = sockets.instances[0].messages
    assert len({message["r"] for message in messages}) == 9
    assert [message["a"] for message in messages].count("auth") == 1


def test_token_rotation_reconnects_and_authenticates(api, sockets):
    assert api._read_via_websocket("/before") == "/before"
    api.auth_token = "rotated-token"
    assert api._read_via_websocket("/after") == "/after"
    assert len(sockets.instances) == 2
    assert sockets.instances[0].done.is_set()
    assert sockets.instances[1].messages[0]["b"]["cred"] == "rotated-token"


@pytest.mark.parametrize("kind", ["read", "write"])
def test_permission_denied_forces_reauth_even_with_same_token(api, sockets, kind):
    def handle(socket, request):
        return {"s": "ok"} if request["a"] == "auth" else {"s": "permission_denied"}

    sockets.Socket.handler = handle
    if kind == "read":
        assert api._read_via_websocket("/denied") is None
    else:
        assert not api._write_via_websocket("serial", {"temp": 21}).success
    assert len(sockets.instances) == 2
    assert api.auth_token_expire_date < datetime.now()
    assert all(socket.messages[0]["a"] == "auth" for socket in sockets.instances)


def test_expired_token_refresh_is_shared_between_polling_threads(sockets):
    client = RointeAPI("user", "password", api_type="nexa")
    client.auth_token = "old"
    client.auth_token_expire_date = datetime.now() - timedelta(seconds=1)

    def refresh():
        client.auth_token = "new"
        client.auth_token_expire_date = datetime.now() + timedelta(hours=1)
        return True

    client._refresh_token = MagicMock(side_effect=refresh)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(executor.map(lambda _: client._ensure_valid_auth(), range(8)))
    client._refresh_token.assert_called_once()
    client.close()


def test_auth_failure_refreshes_then_retries(api, sockets):
    calls = 0

    def handle(socket, request):
        if request["a"] == "auth" and len(sockets.instances) == 1:
            return {"s": "invalid_token"}
        return {"s": "ok", "d": "data"}

    def valid_auth():
        nonlocal calls
        calls += 1
        if calls == 2:
            api.auth_token = "refreshed"
        return True

    sockets.Socket.handler = handle
    api._ensure_valid_auth.side_effect = valid_auth
    assert api._read_via_websocket("/path") == "data"
    assert len(sockets.instances) == 2
    assert sockets.instances[1].messages[0]["b"]["cred"] == "refreshed"


def test_no_token_does_not_open_socket(transport, sockets):
    transport._token_provider = lambda: None
    assert transport.request("g", {"p": "/path"}) is None
    assert sockets.instances == []


@pytest.mark.parametrize("failure", ["disconnect", "error", "timeout"])
def test_interrupted_request_reconnects_once(transport, sockets, monkeypatch, failure):
    monkeypatch.setattr(nexa_ws, "REQUEST_TIMEOUT_SECONDS", 0.01)

    def handle(socket, request):
        if request["a"] != "auth" and len(sockets.instances) == 1:
            if failure == "disconnect":
                socket.callbacks["on_close"](socket, 1006, "lost")
            elif failure == "error":
                socket.callbacks["on_error"](socket, OSError("lost"))
            return None
        return {"s": "ok", "d": "restored"}

    sockets.Socket.handler = handle
    assert transport.request("g", {"p": "/path"})["d"] == "restored"
    assert len(sockets.instances) == 2
    assert sockets.instances[0].done.is_set()


def test_repeated_timeout_is_bounded_and_not_success(transport, sockets, monkeypatch):
    monkeypatch.setattr(nexa_ws, "REQUEST_TIMEOUT_SECONDS", 0.01)
    sockets.Socket.handler = lambda socket, req: (
        {"s": "ok"} if req["a"] == "auth" else None
    )
    assert transport.request("m", {"p": "/path", "d": {"temp": 21}}) is None
    assert len(sockets.instances) == 2
    assert transport._pending == {}


def test_close_during_request_never_reopens_or_replays(transport, sockets):
    sent = threading.Event()

    def handle(socket, request):
        if request["a"] == "auth":
            return {"s": "ok"}
        sent.set()
        return None

    sockets.Socket.handler = handle
    with ThreadPoolExecutor(max_workers=2) as executor:
        active = executor.submit(
            transport.request, "m", {"p": "/path", "d": {"temp": 21}}
        )
        assert sent.wait(2)
        queued = executor.submit(transport.request, "g", {"p": "/another"})
        transport.close()
        assert active.result(timeout=2) is None
        assert queued.result(timeout=2) is None
    assert transport.request("g", {"p": "/after-close"}) is None
    assert len(sockets.instances) == 1
    assert transport._stopped.is_set()
    assert transport._pending == {}
    assert transport._ws is None


def test_close_during_connect_does_not_wait_for_timeout(
    transport, sockets, monkeypatch
):
    started = threading.Event()
    sockets.Socket.open_immediately = False
    original = sockets.Socket.run_forever

    def run(socket, **kwargs):
        started.set()
        original(socket, **kwargs)

    monkeypatch.setattr(sockets.Socket, "run_forever", run)
    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(transport.request, "g", {"p": "/path"})
        assert started.wait(2)
        transport.close()
        assert pending.result(timeout=2) is None
    assert len(sockets.instances) == 1


def test_old_receiver_cannot_answer_new_request(transport, sockets):
    assert transport.request("g", {"p": "/first"}) is not None
    old = sockets.instances[0]
    transport.invalidate_auth()

    def handle(socket, request):
        old.respond(request, {"s": "ok", "d": "stale"})
        return {"s": "ok", "d": "fresh"}

    sockets.Socket.handler = handle
    assert transport.request("g", {"p": "/second"})["d"] == "fresh"


def test_heartbeat_and_stale_reconnect(transport, sockets, monkeypatch):
    monkeypatch.setattr(nexa_ws, "KEEPALIVE_INTERVAL_SECONDS", 0.01)
    renewed = threading.Event()

    def handle(socket, request):
        if len(sockets.instances) == 2:
            renewed.set()
        return {"s": "ok"}

    sockets.Socket.handler = handle
    assert transport.request("g", {"p": "/path"}) is not None
    assert sockets.instances[0].pinged.wait(2)
    with transport._lock:
        transport._last_message_time = (
            time.monotonic() - nexa_ws.STALE_AFTER_SECONDS - 1
        )
    assert renewed.wait(2)
    assert len(sockets.instances) == 2
    assert sockets.instances[0].done.is_set()


def test_protocol_noise_does_not_complete_request(transport, sockets):
    def handle(socket, request):
        for raw in ("0", "not json", "[]", "1", '{"t":"c"}', '{"t":"d","d":null}'):
            socket.callbacks["on_message"](socket, raw)
        return {"s": "ok", "d": "valid"}

    sockets.Socket.handler = handle
    assert transport.request("g", {"p": "/path"})["d"] == "valid"


def test_closed_api_cannot_create_another_transport(api, sockets):
    api._read_via_websocket("/before")
    api.close()
    assert api._read_via_websocket("/after") is None
    assert not api._write_via_websocket("serial", {"temp": 21}).success
    assert len(sockets.instances) == 1


@pytest.mark.parametrize("read_status", ["error", "permission_denied"])
def test_rejected_read_is_never_returned_as_device_data(api, sockets, read_status):
    sockets.Socket.handler = lambda socket, req: (
        {"s": "ok"}
        if req["a"] == "auth"
        else {"s": read_status, "d": "not device data"}
    )
    assert api._read_via_websocket("/path") is None


@pytest.mark.asyncio
async def test_failed_setup_registers_async_transport_cleanup(monkeypatch):
    api = SimpleNamespace(
        initialize_authentication=MagicMock(
            return_value=ApiResponse(False, None, "offline")
        ),
        close=MagicMock(),
    )
    monkeypatch.setattr(integration, "RointeAPI", lambda *args, **kwargs: api)
    entry = SimpleNamespace(
        options={},
        data={integration.CONF_USERNAME: "user", integration.CONF_PASSWORD: "password"},
        async_on_unload=MagicMock(),
    )
    hass = SimpleNamespace(
        async_add_executor_job=AsyncMock(side_effect=lambda fn: fn()),
        bus=SimpleNamespace(async_listen_once=MagicMock(return_value=MagicMock())),
    )
    with pytest.raises(integration.ConfigEntryNotReady):
        await integration.async_setup_entry(hass, entry)
    cleanup = entry.async_on_unload.call_args_list[0].args[0]
    await cleanup()
    api.close.assert_called_once()
    assert hass.async_add_executor_job.call_args.args == (api.close,)

    stop_event, stop_callback = hass.bus.async_listen_once.call_args.args
    assert stop_event == integration.EVENT_HOMEASSISTANT_STOP
    assert stop_callback is cleanup
    assert (
        entry.async_on_unload.call_args_list[1].args[0]
        is hass.bus.async_listen_once.return_value
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("finish", ["unload", "stop", "setup_failure"])
async def test_entry_lifecycle_closes_an_open_connection(
    api, sockets, monkeypatch, finish
):
    api.initialize_authentication = MagicMock(
        return_value=ApiResponse(True, None, None)
    )
    monkeypatch.setattr(integration, "RointeAPI", lambda *args, **kwargs: api)
    monkeypatch.setattr(integration, "RointeDeviceManager", lambda **kwargs: object())

    async def refresh():
        assert api._read_via_websocket("/first-refresh") == "/first-refresh"
        if finish == "setup_failure":
            raise integration.ConfigEntryNotReady("first refresh failed")

    coordinator = SimpleNamespace(async_config_entry_first_refresh=refresh)
    monkeypatch.setattr(
        integration, "RointeDataUpdateCoordinator", lambda *args: coordinator
    )
    callbacks = []
    unsubscribe = MagicMock()
    entry = SimpleNamespace(
        entry_id="entry",
        options={},
        data={
            integration.CONF_USERNAME: "user",
            integration.CONF_PASSWORD: "password",
            integration.CONF_INSTALLATION: "installation",
        },
        async_on_unload=callbacks.append,
    )
    hass = SimpleNamespace(
        data={},
        async_add_executor_job=AsyncMock(side_effect=lambda fn: fn()),
        bus=SimpleNamespace(async_listen_once=MagicMock(return_value=unsubscribe)),
        services=SimpleNamespace(has_service=lambda *args: True),
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
        ),
    )
    if finish == "setup_failure":
        with pytest.raises(integration.ConfigEntryNotReady):
            await integration.async_setup_entry(hass, entry)
    else:
        assert await integration.async_setup_entry(hass, entry)
    assert len(sockets.instances) == 1
    assert not sockets.instances[0].done.is_set()
    if finish == "stop":
        await hass.bus.async_listen_once.call_args.args[1](None)
    else:
        if finish == "unload":
            assert await integration.async_unload_entry(hass, entry)
            assert "entry" not in hass.data["rointe"]
        # Home Assistant processes these callbacks on unload and setup failure.
        callbacks[1]()
        await callbacks[0]()
        unsubscribe.assert_called_once()
    assert sockets.instances[0].done.is_set()
    assert api._get_nexa_ws() is None
    assert not api._nexa_ws._keepalive_thread.is_alive()
