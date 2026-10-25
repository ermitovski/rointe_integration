"""Nexa installation discovery recovers expired credentials without a reload."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from custom_components.rointe.rointesdk import rointe_api as module
from custom_components.rointe.rointesdk.rointe_api import ApiResponse, RointeAPI


@pytest.fixture
def api(monkeypatch):
    client = RointeAPI("user", "password", api_type="nexa")
    client.use_nexa_ws = True
    client.auth_token = "firebase-old"
    client.refresh_token = "refresh-old"
    client.auth_token_expire_date = datetime.now() + timedelta(hours=1)
    client.nexa_token = "nexa-old"
    client.nexa_refresh_token = "nexa-refresh-old"
    client._nexa_auth_combo = ("nexa", "token-header")
    client._clean_credentials()
    monkeypatch.setattr(
        client,
        "_login_user",
        MagicMock(return_value=ApiResponse(True, {
            "auth_token": "firebase-new",
            "refresh_token": "refresh-new",
            "expires": datetime.now() + timedelta(hours=1),
            "local_id": "user-id",
            "nexa_token": "nexa-new",
            "nexa_refresh_token": "nexa-refresh-new",
            "use_nexa_ws": True,
        }, None)),
    )
    yield client
    client.close()


def response(status=200):
    return SimpleNamespace(status_code=status, text="", json=lambda: {
        "data": [{"id": "installation", "zones": [{"devices": [
            {"id": "device", "serialNumber": "serial"},
        ]}]}],
    })


def test_expired_token_recovers_device_discovery(api, monkeypatch):
    get = MagicMock(side_effect=[response(401), response()])
    monkeypatch.setattr(module.requests, "get", get)

    result = api.get_installation_devices("installation")

    assert result.success and result.data == ["device"]
    api._login_user.assert_called_once()
    assert get.call_count == 2
    assert get.call_args_list[0].kwargs["headers"]["token"] == "nexa-old"
    assert get.call_args_list[1].kwargs["headers"]["token"] == "nexa-new"
    assert api.auth_token == "firebase-new"
    assert api.refresh_token == "refresh-new"
    assert api.username is None and api.password is None


def test_failed_login_stops_without_header_probing(api, monkeypatch):
    api._login_user.return_value = ApiResponse(False, None, "Rejected")
    get = MagicMock(return_value=response(401))
    monkeypatch.setattr(module.requests, "get", get)

    assert not api.get_installation_devices("installation").success
    api._login_user.assert_called_once()
    assert get.call_count == 1


def test_repeated_401_retries_only_once(api, monkeypatch):
    get = MagicMock(return_value=response(401))
    monkeypatch.setattr(module.requests, "get", get)

    result = api.get_installation_devices("installation")

    assert not result.success and "401" in result.error_message
    api._login_user.assert_called_once()
    assert get.call_count == 2


@pytest.mark.parametrize("status", [403, 418, 500])
def test_other_server_errors_do_not_trigger_login(api, monkeypatch, status):
    get = MagicMock(return_value=response(status))
    monkeypatch.setattr(module.requests, "get", get)

    assert not api.get_installation_devices("installation").success
    api._login_user.assert_not_called()
    assert get.call_count == 1


def test_network_failure_does_not_trigger_login(api, monkeypatch):
    monkeypatch.setattr(module.requests, "get", MagicMock(
        side_effect=requests.Timeout("Offline"),
    ))
    assert not api.get_installation_devices("installation").success
    api._login_user.assert_not_called()


def test_uncached_header_negotiation_is_preserved(api, monkeypatch):
    api._nexa_auth_combo = None
    get = MagicMock(side_effect=[response(401), response()])
    monkeypatch.setattr(module.requests, "get", get)

    assert api.get_installation_devices("installation").success
    assert api._nexa_auth_combo == ("nexa", "bearer")
    api._login_user.assert_not_called()


def test_concurrent_401s_share_one_login(api, monkeypatch):
    barrier = threading.Barrier(4)

    def get(url, *, headers, timeout):
        if headers.get("token") == "nexa-old":
            barrier.wait(timeout=2)
            return response(401)
        assert headers["token"] == "nexa-new"
        return response()

    monkeypatch.setattr(module.requests, "get", get)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(
            lambda _: api.get_installation_devices("installation"), range(4),
        ))

    assert all(result.success and result.data == ["device"] for result in results)
    api._login_user.assert_called_once()


def test_initial_negotiation_can_recover_an_expired_token(api, monkeypatch):
    api._nexa_auth_combo = None

    def get(url, *, headers, timeout):
        return response() if headers.get("token") == "nexa-new" else response(401)

    monkeypatch.setattr(module.requests, "get", get)
    assert api.get_installation_devices("installation").success
    assert api._nexa_auth_combo == ("nexa", "token-header")
    api._login_user.assert_called_once()


def test_energy_and_installations_share_credential_recovery(api, monkeypatch):
    api._nexa_device_cache = {"device": {"serialNumber": "serial"}}
    barrier = threading.Barrier(2)

    def get(url, *, headers, timeout, **kwargs):
        if headers.get("token") == "nexa-old":
            barrier.wait(timeout=2)
            return response(401)
        assert headers["token"] == "nexa-new"
        if url == module.NEXA_API_V4_STATISTICS_URL:
            return SimpleNamespace(status_code=200, json=lambda: {
                "data": {"consumption": [{"value": 1.5}]},
            })
        return response()

    monkeypatch.setattr(module.requests, "get", get)
    with ThreadPoolExecutor(max_workers=2) as executor:
        installation = executor.submit(api.get_installation_devices, "installation")
        energy = executor.submit(api.get_latest_energy_stats, "device")
        assert installation.result(timeout=3).success
        assert energy.result(timeout=3).data.kwh == 1.5
    api._login_user.assert_called_once()
