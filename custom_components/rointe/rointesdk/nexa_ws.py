"""Shared Firebase WebSocket transport for Nexa devices.

Adapted from alex1075's persistent connection proposal in JYewman/rointe_integration#9.
"""

from __future__ import annotations

import json
import logging
import ssl
import threading
import time
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger(__name__)
KEEPALIVE_INTERVAL_SECONDS = 25
STALE_AFTER_SECONDS = 300
CONNECT_TIMEOUT_SECONDS = 5
AUTH_TIMEOUT_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 8


class NexaWebSocket:
    """Reuse one authenticated socket, serializing requests and reconnects.

    The receiver only signals waiters; it never takes the request lock. Closing
    is terminal and wakes waiters before waiting for that lock, so an interrupted
    request cannot reconnect after config entry unload.
    """

    def __init__(self, url: str, token_provider: Callable[[], str | None]) -> None:
        self._url = url
        self._token_provider = token_provider
        self._ws: Any = None
        self._ws_thread: threading.Thread | None = None
        self._keepalive_thread: threading.Thread | None = None
        self._connected = threading.Event()
        self._stopped = threading.Event()
        self._opened = threading.Event()
        self._authed_token: str | None = None
        self._last_message_time = 0.0
        self._lock = threading.RLock()
        self._pending_lock = threading.Lock()
        self._pending: dict[int, dict[str, Any]] = {}
        self._next_rid = 0

    def request(self, action: str, body: dict[str, Any]) -> dict[str, Any] | None:
        """Return an acknowledged response, retrying a lost connection once."""
        with self._lock:
            for _ in range(2):
                if self._stopped.is_set() or not self._ensure_connected():
                    return None
                response = self._send_and_wait(action, body)
                if response is not None:
                    return response
                self._close_socket()
        return None

    def invalidate_auth(self) -> None:
        """Require authentication again even if the refreshed token is unchanged."""
        with self._lock:
            self._authed_token = None

    def close(self) -> None:
        """Permanently stop this transport and wake any unfinished request."""
        self._stopped.set()
        self._opened.set()
        self._fail_pending()
        with self._lock:
            self._close_socket()
        if self._keepalive_thread:
            self._keepalive_thread.join(timeout=2)

    def _is_alive(self) -> bool:
        return (
            self._ws is not None
            and self._connected.is_set()
            and time.monotonic() - self._last_message_time < STALE_AFTER_SECONDS
        )

    def _ensure_connected(self) -> bool:
        """Called with the request lock, including token refresh."""
        if self._stopped.is_set():
            return False
        token = self._token_provider()
        if not token or self._stopped.is_set():
            return False
        if self._is_alive() and self._authed_token == token:
            return True
        return self._renew(token)

    def _renew(self, token: str) -> bool:
        """Replace the socket while holding the request lock."""
        try:
            import websocket
        except ImportError:
            LOGGER.error("websocket-client is required for Nexa devices")
            return False

        self._close_socket()
        if self._stopped.is_set():
            return False
        self._opened.clear()

        def on_open(ws):
            if ws is not self._ws:
                return
            if not self._stopped.is_set():
                self._last_message_time = time.monotonic()
                self._connected.set()
            self._opened.set()

        def on_message(ws, message):
            if ws is not self._ws or self._stopped.is_set():
                return
            self._last_message_time = time.monotonic()
            if message.strip() == "0":
                return
            try:
                msg = json.loads(message)
                if not isinstance(msg, dict) or msg.get("t") != "d":
                    return
                block = msg.get("d")
                if not isinstance(block, dict) or not isinstance(block.get("b"), dict):
                    return
                with self._pending_lock:
                    waiter = self._pending.get(block.get("r"))
                    if waiter is not None:
                        waiter["response"] = block["b"]
                        waiter["event"].set()
            except (ValueError, TypeError, AttributeError):
                LOGGER.debug("Ignoring non-response Nexa WebSocket message")

        def on_error(ws, error):
            if ws is self._ws:
                LOGGER.debug("Nexa WebSocket error: %s", error)
                self._connected.clear()
                self._opened.set()
                self._fail_pending()

        def on_close(ws, code, message):
            if ws is self._ws:
                self._connected.clear()
                self._opened.set()
                self._fail_pending()

        ws = websocket.WebSocketApp(
            self._url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        self._ws = ws
        self._ws_thread = threading.Thread(
            target=lambda: ws.run_forever(sslopt={"cert_reqs": ssl.CERT_NONE}),
            daemon=True,
            name="rointe-nexa-ws",
        )
        self._ws_thread.start()
        if (
            not self._opened.wait(CONNECT_TIMEOUT_SECONDS)
            or self._stopped.is_set()
            or not self._connected.is_set()
        ):
            self._close_socket()
            return False
        auth = self._send_and_wait("auth", {"cred": token}, AUTH_TIMEOUT_SECONDS)
        if not auth or auth.get("s") != "ok" or self._stopped.is_set():
            self._close_socket()
            return False
        self._authed_token = token
        if self._keepalive_thread is None:
            self._keepalive_thread = threading.Thread(
                target=self._keepalive_loop, daemon=True, name="rointe-nexa-keepalive"
            )
            self._keepalive_thread.start()
        return True

    def _close_socket(self) -> None:
        """Detach the old receiver before closing it; caller holds request lock."""
        ws, self._ws = self._ws, None
        self._authed_token = None
        self._connected.clear()
        self._fail_pending()
        if ws is not None:
            try:
                ws.close()
            except Exception:
                LOGGER.debug("Nexa WebSocket close failed", exc_info=True)
        if self._ws_thread:
            self._ws_thread.join(timeout=2)
            self._ws_thread = None

    def _send_and_wait(
        self, action: str, body: dict[str, Any], timeout: float | None = None
    ) -> dict[str, Any] | None:
        ws = self._ws
        if ws is None or self._stopped.is_set():
            return None
        with self._pending_lock:
            self._next_rid += 1
            rid = self._next_rid
            waiter: dict[str, Any] = {"event": threading.Event(), "response": None}
            self._pending[rid] = waiter
            # close() can set the stop event just before registering this waiter.
            if self._stopped.is_set():
                self._pending.pop(rid)
                return None
        try:
            ws.send(json.dumps({"t": "d", "d": {"r": rid, "a": action, "b": body}}))
            waiter["event"].wait(
                REQUEST_TIMEOUT_SECONDS if timeout is None else timeout
            )
            return waiter["response"]
        except Exception as err:  # noqa: BLE001
            LOGGER.debug("Nexa WebSocket send failed: %s", err)
            self._connected.clear()
            return None
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def _fail_pending(self) -> None:
        with self._pending_lock:
            for waiter in self._pending.values():
                waiter["event"].set()

    def _keepalive_loop(self) -> None:
        while not self._stopped.wait(KEEPALIVE_INTERVAL_SECONDS):
            with self._lock:
                if self._stopped.is_set():
                    return
                if self._ws is None:
                    continue  # A request will reconnect after a failed attempt.
                if not self._is_alive():
                    self._ensure_connected()
                    continue
                try:
                    self._ws.send("0")
                except Exception as err:  # noqa: BLE001
                    LOGGER.debug("Nexa WebSocket keep-alive failed: %s", err)
                    self._connected.clear()
