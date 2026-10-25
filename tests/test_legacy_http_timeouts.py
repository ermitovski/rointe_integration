"""Legacy cloud calls have finite deadlines and recover after a timeout."""

import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest
import requests

from custom_components.rointe.rointesdk import rointe_api as module
from custom_components.rointe.rointesdk.rointe_api import RointeAPI


CALLS = [
    ("get_local_id", (), "post", {"users": [{"localId": "user"}]}),
    ("get_installation_by_id", ("installation",), "get", {
        "installation": {"location": "Home", "zones": {}},
    }),
    ("get_latest_firmware", (), "get", {
        "radiator": {"v2": {"end_user": {}}},
    }),
    ("get_installations", (), "get", {
        "installation": {"location": "Home", "zones": {}},
    }),
    ("get_device", ("device",), "get", {"data": {"type": "radiator"}}),
    ("_retrieve_hour_energy_stats", ("device", datetime(2026, 10, 25, 12)),
     "get", {"kw_h": 1.2, "effective_power": 350}),
    ("_send_patch_request", ("https://offline.invalid/device", {}, {"temp": 20}),
     "patch", {}),
]


@pytest.fixture
def api():
    client = RointeAPI("user", "password", api_type="rointe")
    client.auth_token = "firebase-token"
    client.refresh_token = "refresh-token"
    client.auth_token_expire_date = datetime.now() + timedelta(hours=1)
    client.local_id = "user"
    yield client
    client.close()


def response(payload):
    result = requests.Response()
    result.status_code = 200
    result._content = json.dumps(payload).encode()
    return result


@pytest.mark.parametrize("method,args,verb,payload", CALLS, ids=[c[0] for c in CALLS])
def test_legacy_calls_bound_connection_and_read_time(
    api, monkeypatch, method, args, verb, payload
):
    request = MagicMock(return_value=response(payload))
    monkeypatch.setattr(module.requests, verb, request)

    assert getattr(api, method)(*args).success

    timeout = request.call_args.kwargs["timeout"]
    assert len(timeout) == 2
    assert all(0 < seconds <= 30 for seconds in timeout)


@pytest.mark.parametrize("method,args,verb,payload", CALLS, ids=[c[0] for c in CALLS])
def test_timeout_returns_failure_and_the_next_call_can_recover(
    api, monkeypatch, method, args, verb, payload
):
    request = MagicMock(side_effect=requests.Timeout("request timed out"))
    monkeypatch.setattr(module.requests, verb, request)

    failed = getattr(api, method)(*args)
    assert not failed.success
    assert "timed out" in failed.error_message

    request.side_effect = None
    request.return_value = response(payload)
    assert getattr(api, method)(*args).success
