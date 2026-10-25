"""Rointe API Client"""

from __future__ import annotations

from collections import namedtuple
import logging
import math
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import requests
from requests.exceptions import RequestException

from . import utils
from .device import RointeDevice, ScheduleMode
from .dto import EnergyConsumptionData
from .nexa_ws import NexaWebSocket
from .settings import (
    AUTH_ACCT_INFO_URL,
    AUTH_HOST,
    AUTH_REFRESH_ENDPOINT,
    AUTH_TIMEOUT_SECONDS,
    HTTP_TIMEOUT_SECONDS,
    AUTH_VERIFY_URL,
    ENERGY_STATS_MAX_TRIES,
    FIREBASE_APP_KEY,
    FIREBASE_DEFAULT_URL,
    FIREBASE_DEVICE_DATA_PATH_BY_ID,
    FIREBASE_DEVICE_ENERGY_PATH_BY_ID,
    FIREBASE_DEVICES_PATH_BY_ID,
    FIREBASE_GLOBAL_SETTINGS_PATH,
    FIREBASE_INSTALLATIONS_PATH,
    NEXA_FIREBASE_APP_KEY,
    NEXA_FIREBASE_AUTH_URL,
    NEXA_FIREBASE_DEFAULT_URL,
    NEXA_FIREBASE_WS_URL,
    NEXA_INSTALLATIONS_URL,
    NEXA_LOGIN_URL,
    NEXA_API_V4_STATISTICS_URL,
)
from .utils import build_update_map

ApiResponse = namedtuple("ApiResponse", ["success", "data", "error_message"])
LOGGER = logging.getLogger(__name__)


class RointeAPI:
    """Rointe API Communication. Handles low level calls to the API."""

    def __init__(self, username: str, password: str, api_type: Optional[str] = None):
        """Initializes the API"""

        self.username: Optional[str] = username
        self.password: Optional[str] = password
        self._stored_username: Optional[str] = username
        self._stored_password: Optional[str] = password
        self.api_type = (api_type or "auto").lower()

        self.refresh_token = None
        self.auth_token = None
        self.auth_token_expire_date: Optional[datetime] = None
        self.local_id = None
        self.nexa_token: Optional[str] = None
        self.nexa_refresh_token: Optional[str] = None
        self.firebase_base_url = FIREBASE_DEFAULT_URL
        self.firebase_ws_url: Optional[str] = None
        self.firebase_app_key = FIREBASE_APP_KEY
        self.use_nexa_ws = False
        self._nexa_device_cache: Dict[str, Dict[str, Any]] = {}
        # (token_name, header_name) combo that last worked against the Nexa
        # installations endpoints; see _nexa_authenticated_get.
        self._nexa_auth_combo: Optional[tuple[str, str]] = None
        self._nexa_energy_cache: Dict[str, EnergyConsumptionData] = {}
        self._nexa_energy_cache_ttl = 300
        self._nexa_energy_lock = threading.Lock()
        self._auth_lock = threading.RLock()
        self._nexa_ws_lock = threading.Lock()
        self._nexa_ws: NexaWebSocket | None = None
        self._closed = False

    def initialize_authentication(self) -> ApiResponse:
        """
        Initializes the refresh token and cleans
        the original credentials.
        """

        LOGGER.debug("Initializing authentication (api_type=%s)", self.api_type)
        login_data: ApiResponse = self._login_user()

        if not login_data.success:
            LOGGER.error("Authentication failed: %s", login_data.error_message)
            self.auth_token = None
            self.refresh_token = None
            return login_data

        self.auth_token = login_data.data["auth_token"]
        self.refresh_token = login_data.data["refresh_token"]
        self.auth_token_expire_date = login_data.data["expires"]
        self.local_id = login_data.data["local_id"]
        self.nexa_token = login_data.data.get("nexa_token")
        self.nexa_refresh_token = login_data.data.get("nexa_refresh_token")
        self.firebase_base_url = login_data.data.get(
            "firebase_base_url", FIREBASE_DEFAULT_URL
        )
        self.firebase_ws_url = login_data.data.get("firebase_ws_url")
        self.firebase_app_key = login_data.data.get("firebase_app_key", FIREBASE_APP_KEY)
        self.use_nexa_ws = bool(login_data.data.get("use_nexa_ws", False))
        LOGGER.debug(
            "Authentication complete. Nexa=%s, firebase_base_url=%s",
            self.use_nexa_ws,
            self.firebase_base_url,
        )

        self._clean_credentials()

        return ApiResponse(True, None, None)

    def _clean_credentials(self) -> None:
        """Cleans authentication values"""
        self.username = None
        self.password = None

    def is_logged_in(self) -> bool:
        """Check if the login was successful."""
        return self.auth_token is not None and self.refresh_token is not None

    def _ensure_valid_auth(self) -> bool:
        """Ensure there is a valid authentication token present."""

        with self._auth_lock:
            now = datetime.now()

            if not self.auth_token or (
                self.auth_token_expire_date and self.auth_token_expire_date < now
            ):
                if not self._refresh_token():
                    LOGGER.warning("Token refresh failed, attempting full re-authentication")
                    if not self._re_authenticate():
                        return False

            return True

    def _re_authenticate(self) -> bool:
        """Attempt a full re-authentication using stored credentials.

        This is called when the refresh token has expired and we need
        to do a fresh login. Requires that _stored_username and
        _stored_password were saved during initialization.
        """

        if not self._stored_username or not self._stored_password:
            LOGGER.error(
                "Cannot re-authenticate: no stored credentials available"
            )
            return False

        LOGGER.debug("Performing full re-authentication")
        # Temporarily restore credentials for login
        self.username = self._stored_username
        self.password = self._stored_password

        login_data = self._login_user()

        if not login_data.success:
            LOGGER.error("Re-authentication failed: %s", login_data.error_message)
            self.username = None
            self.password = None
            return False

        self.auth_token = login_data.data["auth_token"]
        self.refresh_token = login_data.data["refresh_token"]
        self.auth_token_expire_date = login_data.data["expires"]
        self.local_id = login_data.data["local_id"]
        self.nexa_token = login_data.data.get("nexa_token")
        self.nexa_refresh_token = login_data.data.get("nexa_refresh_token")
        self.firebase_base_url = login_data.data.get(
            "firebase_base_url", FIREBASE_DEFAULT_URL
        )
        self.firebase_ws_url = login_data.data.get("firebase_ws_url")
        self.firebase_app_key = login_data.data.get(
            "firebase_app_key", FIREBASE_APP_KEY
        )
        self.use_nexa_ws = bool(login_data.data.get("use_nexa_ws", False))

        # Clean credentials from memory (but keep stored copies)
        self.username = None
        self.password = None

        LOGGER.info("Re-authentication successful")
        return True

    def _refresh_token(self) -> bool:
        """Refreshes authentication."""

        payload = {"grant_type": "refresh_token", "refresh_token": self.refresh_token}

        try:
            response = requests.post(
                f"{AUTH_REFRESH_ENDPOINT}?key={self.firebase_app_key}",
                data=payload,
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except RequestException:
            return False

        if not response:
            return False

        if response.status_code != 200:
            return False

        response_json = response.json()

        if not response_json or "id_token" not in response_json:
            return False

        self.auth_token = response_json["id_token"]
        self.auth_token_expire_date = datetime.now() + timedelta(
            seconds=int(response_json["expires_in"])
        )
        self.refresh_token = response_json["refresh_token"]

        return True

    def _login_user(self) -> ApiResponse:
        """Log the user in."""

        if self.api_type == "nexa":
            return self._login_user_nexa()

        payload = {
            "email": self.username,
            "password": self.password,
            "returnSecureToken": True,
        }

        try:
            response = requests.post(
                f"{AUTH_HOST}{AUTH_VERIFY_URL}?key={FIREBASE_APP_KEY}",
                data=payload,
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response.status_code != 200:
            if self.api_type == "rointe":
                if response.status_code == 400:
                    return ApiResponse(
                        False,
                        None,
                        "Authentication returned 400 (Bad Request)",
                    )
                return ApiResponse(
                    False, None, "Authentication returned: " + str(response.status_code)
                )
            LOGGER.debug(
                "Legacy auth failed (status=%s), attempting Nexa login",
                response.status_code,
            )
            nexa_response = self._login_user_nexa()
            if nexa_response.success:
                return nexa_response
            if response.status_code == 400:
                return ApiResponse(
                    False,
                    None,
                    "Authentication returned 400 (Bad Request)",
                )
            return ApiResponse(
                False, None, "Authentication returned: " + str(response.status_code)
            )

        response_json = response.json()

        if not response_json or "idToken" not in response_json:
            return ApiResponse(
                False, None, "Authentication returned invalid or empty response"
            )

        data = {
            "auth_token": response_json["idToken"],
            "expires": datetime.now()
            + timedelta(seconds=int(response_json["expiresIn"])),
            "refresh_token": response_json["refreshToken"],
            "local_id": response_json["localId"],
            "firebase_base_url": FIREBASE_DEFAULT_URL,
            "firebase_app_key": FIREBASE_APP_KEY,
            "use_nexa_ws": False,
        }

        return ApiResponse(True, data, None)

    def _login_user_nexa(self) -> ApiResponse:
        """Log the user in via Nexa API, then authenticate with Firebase."""

        LOGGER.debug("Attempting Nexa login")
        try:
            response = requests.post(
                NEXA_LOGIN_URL,
                json={
                    "email": self.username,
                    "password": self.password,
                    "push": "",
                    "migrate": False,
                },
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response.status_code != 200:
            LOGGER.error("Nexa login failed: %s", response.status_code)
            return ApiResponse(
                False, None, f"Nexa login returned {response.status_code}"
            )

        try:
            response_json = response.json()
            LOGGER.debug("Nexa login response keys: %s", list(response_json.keys()))
            if isinstance(response_json.get("data"), dict):
                LOGGER.debug(
                    "Nexa login data keys: %s", list(response_json["data"].keys())
                )
            user_id = response_json["data"]["user"]["id"]
            token_block = response_json["data"].get("token", response_json["data"])
            if isinstance(token_block, dict):
                nexa_token = token_block.get("accessToken") or token_block.get(
                    "access_token"
                )
            else:
                nexa_token = None
            if not nexa_token:
                nexa_token = response_json["data"].get("token")
            nexa_refresh_token = response_json["data"].get("refreshToken")
        except (KeyError, TypeError, ValueError):
            LOGGER.error("Nexa login response missing user id")
            return ApiResponse(False, None, "Invalid Nexa login response")

        try:
            firebase_response = requests.post(
                f"{NEXA_FIREBASE_AUTH_URL}?key={NEXA_FIREBASE_APP_KEY}",
                json={
                    "returnSecureToken": True,
                    "email": f"{user_id}@rointe.com",
                    "password": user_id,
                    "clientType": "CLIENT_TYPE_WEB",
                },
                timeout=AUTH_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if firebase_response.status_code != 200:
            LOGGER.error(
                "Nexa Firebase auth failed: %s", firebase_response.status_code
            )
            return ApiResponse(
                False,
                None,
                f"Nexa Firebase auth returned {firebase_response.status_code}",
            )

        firebase_json = firebase_response.json()
        if not firebase_json or "idToken" not in firebase_json:
            LOGGER.error("Nexa Firebase auth response missing token")
            return ApiResponse(False, None, "Nexa Firebase auth returned no token")

        LOGGER.debug("Nexa Firebase auth success")
        if nexa_token:
            LOGGER.debug("Nexa token length: %s", len(nexa_token))
        if nexa_refresh_token:
            LOGGER.debug("Nexa refresh token length: %s", len(nexa_refresh_token))
        data = {
            "auth_token": firebase_json["idToken"],
            "expires": datetime.now()
            + timedelta(seconds=int(firebase_json["expiresIn"])),
            "refresh_token": firebase_json["refreshToken"],
            "local_id": firebase_json["localId"],
            "nexa_token": nexa_token,
            "nexa_refresh_token": nexa_refresh_token,
            "firebase_base_url": NEXA_FIREBASE_DEFAULT_URL,
            "firebase_ws_url": NEXA_FIREBASE_WS_URL,
            "firebase_app_key": NEXA_FIREBASE_APP_KEY,
            "use_nexa_ws": True,
        }

        return ApiResponse(True, data, None)

    def get_local_id(self) -> ApiResponse:
        """Retrieve user local_id value."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        payload = {"idToken": self.auth_token}

        try:
            response = requests.post(
                f"{AUTH_HOST}{AUTH_ACCT_INFO_URL}?key={self.firebase_app_key}",
                data=payload,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response is None:
            return ApiResponse(
                False, None, "No response from API call to get_local_id()"
            )

        if response.status_code != 200:
            return ApiResponse(
                False, None, f"get_local_id() returned {response.status_code}"
            )

        response_json = response.json()

        return ApiResponse(True, response_json["users"][0]["localId"], None)

    def get_installation_devices(self, installation_id: str) -> ApiResponse:
        """Retrieve all devices present in an installation."""
        installation_response = self.get_installation_by_id(installation_id)

        if not installation_response.success:
            return installation_response

        data = installation_response.data
        if self.use_nexa_ws:
            LOGGER.debug(
                "Nexa installation data type=%s keys=%s",
                type(data).__name__,
                list(data.keys()) if isinstance(data, dict) else None,
            )
            zones = None
            if isinstance(data, dict) and "zones" in data:
                zones = data.get("zones")
            elif isinstance(data, dict) and "rooms" in data:
                zones = data.get("rooms")
            else:
                zones = data
            devices = self._extract_devices_any(zones)
            unique_devices = list(dict.fromkeys(devices))
            LOGGER.debug(
                "Nexa installation devices found=%s sample=%s",
                len(unique_devices),
                unique_devices[:5],
            )
            return ApiResponse(True, unique_devices, None)

        detected_devices: List[str] = []
        for zone_key in data["zones"]:
            detected_devices.extend(self._extract_devices(data["zones"].get(zone_key)))

        return ApiResponse(True, detected_devices, None)

    def _extract_devices(self, zone_data: dict[str, Any]) -> List[str]:
        """Parses a single zone block recursively."""

        if not zone_data:
            return []

        zone_devices: List[str] = []

        if devices := zone_data.get("devices"):
            zone_devices.extend(list(devices.keys()))

        if sub_zones := zone_data.get("zones"):
            for sub_zone_key in sub_zones:
                zone_devices.extend(self._extract_devices(sub_zones.get(sub_zone_key)))

        return zone_devices

    def _extract_devices_any(self, zone_data: Any) -> List[str]:
        """Parse devices from dict/list structures (Nexa)."""

        if not zone_data:
            return []

        devices: List[str] = []

        if isinstance(zone_data, list):
            for item in zone_data:
                devices.extend(self._extract_devices_any(item))
            return devices

        if isinstance(zone_data, dict):
            devices_block = (
                zone_data.get("devices")
                or zone_data.get("radiators")
                or zone_data.get("items")
            )
            if isinstance(devices_block, dict):
                for dev_id, dev_info in devices_block.items():
                    if isinstance(dev_info, dict):
                        self._cache_nexa_device(dev_id, dev_info)
                devices.extend(list(devices_block.keys()))
            elif isinstance(devices_block, list):
                for dev in devices_block:
                    if not isinstance(dev, dict):
                        continue
                    dev_id = (
                        dev.get("id")
                        or dev.get("_id")
                        or dev.get("uuid")
                        or dev.get("device_id")
                    )
                    if dev_id:
                        self._cache_nexa_device(dev_id, dev)
                        devices.append(dev_id)

            sub_zones = zone_data.get("zones") or zone_data.get("rooms")
            devices.extend(self._extract_devices_any(sub_zones))

        return devices

    def _cache_nexa_device(self, device_id: str, dev: Dict[str, Any]) -> None:
        """Cache Nexa device metadata for later use."""

        if not device_id or device_id in self._nexa_device_cache:
            return
        cached = {
            "id": device_id,
            "name": dev.get("name"),
            "serialNumber": dev.get("serialNumber"),
        }
        self._nexa_device_cache[device_id] = cached

    def get_installation_by_id(self, installation_id: str) -> ApiResponse:
        """Retrieve a specific installation by ID."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        if self.use_nexa_ws:
            nexa_response = self._get_installation_by_id_nexa(installation_id)
            return nexa_response

        args = {
            "auth": self.auth_token,
            "orderBy": '"userid"',
            "equalTo": f'"{self.local_id}"',
        }

        url = f"{self.firebase_base_url}{FIREBASE_INSTALLATIONS_PATH}"

        try:
            response = requests.get(url, params=args, timeout=HTTP_TIMEOUT_SECONDS)
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response is None:
            return ApiResponse(
                False, None, "No response from API in get_installation_by_id()"
            )

        if response.status_code != 200:
            return ApiResponse(
                False, None, f"get_installation_by_id() returned {response.status_code}"
            )

        reponse_json = response.json()

        if len(reponse_json) == 0 or installation_id not in reponse_json:
            return ApiResponse(False, None, "No Rointe installation found.")

        return ApiResponse(True, reponse_json[installation_id], None)

    def get_latest_firmware(self) -> ApiResponse:
        """Retrieves the latest firmware available for each device type.

        Note: This endpoint doesn't exist for Nexa users, so we return empty for them.
        """

        # Nexa doesn't have a global_settings endpoint - skip firmware check
        if self.use_nexa_ws:
            LOGGER.debug("Skipping firmware check for Nexa (endpoint not available)")
            return ApiResponse(True, {}, None)

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        url = f"{self.firebase_base_url}{FIREBASE_GLOBAL_SETTINGS_PATH}"
        args = {"auth": self.auth_token}

        try:
            response = requests.get(
                url,
                params=args,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response is None:
            return ApiResponse(
                False, None, "No response from API in get_latest_firmware()"
            )

        if response.status_code != 200:
            return ApiResponse(
                False, None, f"get_latest_firmware() returned {response.status_code}"
            )

        data = response.json()

        if len(data) == 0:
            return ApiResponse(False, None, "Global Settings is empty.")

        return ApiResponse(True, build_update_map(data), None)

    def get_installations(self) -> ApiResponse:
        """Retrieve the client's installations."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        if self.use_nexa_ws:
            nexa_response = self._get_installations_nexa()
            return nexa_response

        args = {
            "auth": self.auth_token,
            "orderBy": '"userid"',
            "equalTo": f'"{self.local_id}"',
        }
        url = f"{self.firebase_base_url}{FIREBASE_INSTALLATIONS_PATH}"

        try:
            response = requests.get(url, params=args, timeout=HTTP_TIMEOUT_SECONDS)
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response is None:
            return ApiResponse(
                False, None, "No response from API in get_installations()"
            )

        if response.status_code != 200:
            return ApiResponse(
                False, None, f"get_installations() returned {response.status_code}"
            )

        reponse_json = response.json()

        if len(reponse_json) == 0:
            return ApiResponse(False, None, "No Rointe installations found.")

        installations = {}

        for key in reponse_json.keys():
            installations[key] = reponse_json[key]["location"]

        return ApiResponse(True, installations, None)

    def get_device(self, device_id: str) -> ApiResponse:
        """Retrieve device data."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        args = {"auth": self.auth_token}
        if self.use_nexa_ws:
            LOGGER.debug("Nexa get_device id=%s", device_id)
            meta = self._nexa_device_cache.get(device_id, {})
            serial = meta.get("serialNumber")
            paths = []
            if serial:
                paths.extend((f"/devices/{serial}/data", f"/devices/{serial}"))
            paths.extend((f"/devices/{device_id}/data", f"/devices/{device_id}"))
            for path in paths:
                ws_data = self._read_via_websocket(path)
                if ws_data is None:
                    continue
                if isinstance(ws_data, dict):
                    if "data" in ws_data:
                        return ApiResponse(True, ws_data, None)
                    if meta:
                        LOGGER.debug(
                            "Nexa device cache hit for %s: %s",
                            device_id,
                            list(meta.keys()),
                        )
                    data_block = dict(ws_data)
                    if meta.get("name") and "name" not in data_block:
                        data_block["name"] = meta.get("name")
                    result = {"data": data_block}
                    if meta.get("serialNumber"):
                        result["serialnumber"] = meta.get("serialNumber")
                    firmware_data = None
                    if serial:
                        firmware_data = self._read_via_websocket(
                            f"/devices/{serial}/firmware"
                        )
                    if isinstance(firmware_data, dict):
                        if (
                            "firmware_version_device" not in firmware_data
                            and "firmware_version" in firmware_data
                        ):
                            firmware_data["firmware_version_device"] = firmware_data[
                                "firmware_version"
                            ]
                        result["firmware"] = firmware_data
                    return ApiResponse(True, result, None)

            LOGGER.error("Nexa get_device websocket read failed for %s", device_id)
            return ApiResponse(False, None, "WebSocket read failed")

        try:
            response = requests.get(
                "{}{}".format(
                    self.firebase_base_url,
                    FIREBASE_DEVICES_PATH_BY_ID.format(device_id),
                ),
                params=args,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response is None:
            return ApiResponse(False, None, "No response from API in get_device()")

        if response.status_code != 200:
            if self.use_nexa_ws:
                LOGGER.error(
                    "Nexa get_device failed: status=%s response=%s",
                    response.status_code,
                    response.text[:200],
                )
            return ApiResponse(
                False, None, f"get_device() returned {response.status_code}"
            )

        return ApiResponse(True, response.json(), None)

    def get_latest_energy_stats(
        self, device_id: str, installation_id: Optional[str] = None
    ) -> ApiResponse:
        """Retrieve the latest energy consumption values."""

        if self.use_nexa_ws:
            return self._get_nexa_energy_stats(device_id)

        result: ApiResponse
        now = datetime.now()

        # Attempt to retrieve the latest value. If not found, go back one hour. Max 5 tries.
        attempts = ENERGY_STATS_MAX_TRIES
        target_date = now.replace(
            minute=0, second=0, microsecond=0
        )  # Strip minutes, seconds and microseconds.

        while attempts > 0:
            result = self._retrieve_hour_energy_stats(
                device_id, target_date
            )

            if result.error_message == "No energy stats found.":
                # Try again.
                attempts = attempts - 1
                target_date = target_date - timedelta(hours=1)
            else:
                # It's either a success or an error message, return the ApiResponse.
                return result

        return ApiResponse(False, None, "Max tries exceeded.")

    def _retrieve_hour_energy_stats(
        self, device_id: str, target_date: datetime
    ) -> ApiResponse:
        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        # Sample URL /history_statistics/device_id/daily/2022/01/21/energy/010000.json
        args = {"auth": self.auth_token}
        url = "{}{}{}/energy/{}0000.json".format(
            self.firebase_base_url,
            FIREBASE_DEVICE_ENERGY_PATH_BY_ID.format(device_id),
            target_date.strftime("%Y/%m/%d"),
            target_date.strftime("%H"),
        )

        try:
            response = requests.get(url, params=args, timeout=HTTP_TIMEOUT_SECONDS)
        except RequestException as e:
            return ApiResponse(False, None, f"Network error {e}")

        if response is None:
            return ApiResponse(
                False, None, "No response from API in _retrieve_hour_energy_stats()"
            )

        if response.status_code != 200:
            return ApiResponse(
                False,
                None,
                f"_retrieve_hour_energy_stats() returned {response.status_code}",
            )

        response_json = response.json()

        if not response_json or len(response_json) == 0:
            return ApiResponse(False, None, "No energy stats found.")

        data = EnergyConsumptionData(
            created=datetime.now(),
            start=target_date,
            end=target_date + timedelta(hours=1),
            kwh=float(response_json["kw_h"]),
            effective_power=float(response_json["effective_power"]),
        )

        return ApiResponse(True, data, None)

    def set_device_temp(self, device: RointeDevice, new_temp: float) -> ApiResponse:
        """Set the device target temperature."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        device_id = device.id
        if self.use_nexa_ws:
            device_key = self._get_nexa_device_key(device)
            body = {"temp": new_temp, "mode": 0, "power": 2, "status": "none"}
            LOGGER.debug("Nexa set temp: %s -> %s", device_key, body)
            return self._write_via_websocket(device_key, body)

        args = {"auth": self.auth_token}
        body = {"temp": new_temp, "mode": "manual", "power": True}

        url = "{}{}".format(
            self.firebase_base_url, FIREBASE_DEVICE_DATA_PATH_BY_ID.format(device_id)
        )

        return self._send_patch_request(url, args, body)

    def set_device_preset(self, device: RointeDevice, preset_mode: str) -> ApiResponse:
        """Set the preset."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        device_id = device.id
        if self.use_nexa_ws:
            device_key = self._get_nexa_device_key(device)
            if preset_mode == "comfort":
                target_temp = device.comfort_temp
            elif preset_mode == "eco":
                target_temp = device.eco_temp
            elif preset_mode == "ice":
                target_temp = device.ice_temp
            else:
                return ApiResponse(False, None, f"Invalid preset {preset_mode}.")

            body = {"temp": target_temp, "mode": 0, "power": 2, "status": "none"}
            LOGGER.debug("Nexa set preset: %s -> %s", device_key, body)
            return self._write_via_websocket(device_key, body)

        args = {"auth": self.auth_token}
        body: Dict[str, Any] = {}

        url = "{}{}".format(
            self.firebase_base_url,
            FIREBASE_DEVICE_DATA_PATH_BY_ID.format(device_id),
        )

        if preset_mode == "comfort":
            body = {
                "power": True,
                "mode": "manual",
                "temp": device.comfort_temp,
                "status": "comfort",
            }

        elif preset_mode == "eco":
            body = {
                "power": True,
                "mode": "manual",
                "temp": device.eco_temp,
                "status": "eco",
            }
        elif preset_mode == "ice":
            body = {
                "power": True,
                "mode": "manual",
                "temp": device.ice_temp,
                "status": "ice",
            }

        return self._send_patch_request(url, args, body)

    def set_device_mode(self, device: RointeDevice, hvac_mode: str) -> ApiResponse:
        """Set the HVAC mode."""

        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")

        device_id = device.id
        LOGGER.debug("set_device_mode called with hvac_mode=%s (type=%s)", hvac_mode, type(hvac_mode).__name__)
        if self.use_nexa_ws:
            device_key = self._get_nexa_device_key(device)
            LOGGER.debug("Using Nexa WS, device_key=%s", device_key)
            if hvac_mode == "off":
                body = {"power": 1, "mode": 0, "status": "off"}
                LOGGER.debug("Nexa set hvac off: %s -> %s", device_key, body)
                return self._write_via_websocket(device_key, body)
            if hvac_mode == "heat":
                body = {
                    "temp": device.comfort_temp,
                    "mode": 0,
                    "power": 2,
                    "status": "none",
                }
                LOGGER.debug("Nexa set hvac heat: %s -> %s", device_key, body)
                return self._write_via_websocket(device_key, body)
            if hvac_mode == "auto":
                # Set to auto mode (mode=1) - don't send temp, let radiator follow its schedule
                body = {
                    "mode": 1,
                    "power": 2,
                    "status": "none",
                }
                LOGGER.debug("Nexa set hvac auto: %s -> %s", device_key, body)
                return self._write_via_websocket(device_key, body)
            return ApiResponse(False, None, f"Invalid HVAC Mode {hvac_mode}.")

        args = {"auth": self.auth_token}
        body: Dict[str, Any] = {}

        url = "{}{}".format(
            self.firebase_base_url,
            FIREBASE_DEVICE_DATA_PATH_BY_ID.format(device_id),
        )

        if hvac_mode == "off":
            # This depends if the device is in Auto or Manual modes.
            if device.mode == "auto":
                body = {"power": False, "mode": "auto", "status": "off"}
                return self._send_patch_request(url, args, body)
            else:
                # When turning the device off, we need to set the temperature first.
                set_mode_response = self._send_patch_request(url, args, {"temp": 20})

                if not set_mode_response.success:
                    return set_mode_response

                # Then we can turn the device off.
                body = {"power": False, "mode": "manual", "status": "off"}
                return self._send_patch_request(url, args, body)

        elif hvac_mode == "heat":
            set_mode_response = self._send_patch_request(
                url, args, {"temp": device.comfort_temp}
            )

            if not set_mode_response.success:
                return set_mode_response

            body = {"mode": "manual", "power": True, "status": "none"}
            return self._send_patch_request(url, args, body)

        elif hvac_mode == "auto":
            current_mode: ScheduleMode = device.get_current_schedule_mode()

            # When changing modes we need to send the proper
            # temperature also.
            if current_mode == ScheduleMode.COMFORT:
                body = {"temp": device.comfort_temp}
            elif current_mode == ScheduleMode.ECO:
                body = {"temp": device.eco_temp}
            elif device.ice_mode:
                body = {"temp": device.ice_temp}
            else:
                body = {"temp": 20}

            set_mode_response = self._send_patch_request(url, args, body)

            if not set_mode_response.success:
                return set_mode_response

            # and then set AUTO mode.
            request_mode_status = self._send_patch_request(
                url, args, {"mode": "auto", "power": True}
            )

            return request_mode_status
        else:
            return ApiResponse(False, None, f"Invalid HVAC Mode {hvac_mode}.")

    def _send_patch_request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        body: Optional[Dict] = None,
    ) -> ApiResponse:
        """Send a patch request."""

        if not body:
            body = {}

        body["last_sync_datetime_app"] = round(datetime.now().timestamp() * 1000)

        try:
            response = requests.patch(
                url,
                params=params,
                json=body,
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except RequestException as e:
            return ApiResponse(False, None, f"Communications error {e}")

        if not response:
            return ApiResponse(False, None, None)

        if response.status_code != 200:
            return ApiResponse(False, None, None)

        return ApiResponse(True, None, None)

    def _nexa_authenticated_get(
        self, url: str
    ) -> tuple[Optional[requests.Response], Optional[str]]:
        """GET a Nexa installations endpoint, negotiating the auth header format.

        The Nexa API's expected auth header format isn't documented, so the first
        call probes token/header combinations until one returns something other
        than 401. Re-probing all combinations on every coordinator refresh (every
        ROINTE_API_REFRESH_INTERVAL) triggers the API's rate limiting, observed as
        HTTP 418, so once a combination returns 200 it is cached on the instance
        and reused directly. A fresh 401 with the cached combo forces re-probing.
        """

        tokens_to_try = []
        if self.nexa_token:
            tokens_to_try.append(("nexa", self.nexa_token))
        if self.nexa_refresh_token:
            tokens_to_try.append(("nexa_refresh", self.nexa_refresh_token))
        if self.auth_token:
            tokens_to_try.append(("firebase", self.auth_token))

        if not tokens_to_try:
            return None, "Nexa token missing"

        header_templates: Dict[str, Any] = {
            # Nexa API uses the 'token' header (not Authorization)
            "token-header": lambda t: {"token": t},
            "bearer": lambda t: {"Authorization": f"Bearer {t}"},
            "token": lambda t: {"Authorization": t},
            "x-access-token": lambda t: {"x-access-token": t},
            "bearer+x-access-token": lambda t: {
                "Authorization": f"Bearer {t}",
                "x-access-token": t,
            },
            "token+x-access-token": lambda t: {"Authorization": t, "x-access-token": t},
        }
        tokens_by_name = dict(tokens_to_try)

        if self._nexa_auth_combo:
            cached_token_name, cached_header_name = self._nexa_auth_combo
            token_value = tokens_by_name.get(cached_token_name)
            if token_value is not None:
                headers = {"Accept": "application/json"}
                headers.update(header_templates[cached_header_name](token_value))
                try:
                    response = requests.get(
                        url, headers=headers, timeout=AUTH_TIMEOUT_SECONDS
                    )
                except RequestException as e:
                    return None, f"Network error {e}"
                if response.status_code != 401:
                    return response, None
                # Cached combo is no longer accepted - re-probe below.
                self._nexa_auth_combo = None

        response = None
        for token_name, token_value in tokens_to_try:
            for header_name, header_builder in header_templates.items():
                headers = {"Accept": "application/json"}
                headers.update(header_builder(token_value))
                try:
                    response = requests.get(
                        url, headers=headers, timeout=AUTH_TIMEOUT_SECONDS
                    )
                except RequestException as e:
                    return None, f"Network error {e}"

                if response.status_code != 401:
                    LOGGER.debug(
                        "Nexa auth negotiation succeeded using %s token with %s headers",
                        token_name,
                        header_name,
                    )
                    if response.status_code == 200:
                        self._nexa_auth_combo = (token_name, header_name)
                    return response, None

                LOGGER.debug(
                    "Nexa auth negotiation got 401 using %s token with %s headers",
                    token_name,
                    header_name,
                )

        return None, "No response from Nexa API"

    def _get_installations_nexa(self) -> ApiResponse:
        """Retrieve installations from Nexa API."""

        response, error = self._nexa_authenticated_get(NEXA_INSTALLATIONS_URL)

        if error:
            return ApiResponse(False, None, error)

        if response is None:
            return ApiResponse(
                False, None, "No response from Nexa API in get_installations()"
            )

        if response.status_code != 200:
            LOGGER.error(
                "Nexa get_installations failed: status=%s", response.status_code
            )
            LOGGER.debug(
                "Nexa get_installations response: %s", response.text[:200]
            )
            return ApiResponse(
                False,
                None,
                f"Nexa get_installations returned {response.status_code}",
            )

        try:
            response_json = response.json()
        except ValueError:
            return ApiResponse(False, None, "Nexa get_installations invalid JSON")

        data = response_json.get("data", response_json)
        installations: Dict[str, str] = {}

        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                install_id = self._nexa_item_id(item)
                if not install_id:
                    continue
                name = (
                    item.get("location")
                    or item.get("name")
                    or item.get("address")
                    or install_id
                )
                installations[install_id] = name
        elif isinstance(data, dict):
            for key, item in data.items():
                if isinstance(item, dict):
                    name = item.get("location") or item.get("name") or key
                else:
                    name = str(item)
                installations[key] = name
        else:
            return ApiResponse(False, None, "Nexa get_installations unknown format")

        if not installations:
            return ApiResponse(False, None, "Nexa get_installations empty result")

        return ApiResponse(True, installations, None)

    @staticmethod
    def _nexa_item_id(item: Dict[str, Any]) -> Optional[str]:
        """Extract an installation id from a raw Nexa API list item."""
        return (
            item.get("id")
            or item.get("_id")
            or item.get("uuid")
            or item.get("installation_id")
        )

    def _find_nexa_installation(
        self, data: Any, installation_id: str
    ) -> Optional[Dict[str, Any]]:
        """Find one installation's raw data by id within a Nexa installations list."""

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and self._nexa_item_id(item) == installation_id:
                    return item
            return None

        if isinstance(data, dict):
            item = data.get(installation_id)
            return item if isinstance(item, dict) else None

        return None

    def _get_installation_by_id_nexa(self, installation_id: str) -> ApiResponse:
        """Retrieve a Nexa installation by ID.

        The Nexa API has no per-installation detail endpoint: GET
        .../installations/<id> returns HTTP 418 with body {"message": "Not
        Found"} for any id. The full installation object (including
        zones/devices) is only available from the installations list, so fetch
        that and pick out the matching entry.
        """

        response, error = self._nexa_authenticated_get(NEXA_INSTALLATIONS_URL)

        if error:
            return ApiResponse(False, None, error)

        if response is None:
            return ApiResponse(
                False, None, "No response from Nexa API in get_installation_by_id()"
            )

        if response.status_code != 200:
            LOGGER.error(
                "Nexa get_installation_by_id failed: status=%s", response.status_code
            )
            LOGGER.debug(
                "Nexa get_installation_by_id response: %s", response.text[:200]
            )
            return ApiResponse(
                False,
                None,
                f"Nexa get_installation_by_id returned {response.status_code}",
            )

        try:
            response_json = response.json()
        except ValueError:
            return ApiResponse(False, None, "Nexa get_installation_by_id invalid JSON")

        data = response_json.get("data", response_json)
        installation = self._find_nexa_installation(data, installation_id)

        if installation is None:
            return ApiResponse(
                False,
                None,
                f"Nexa installation {installation_id} not found in installations list",
            )

        return ApiResponse(True, installation, None)

    def _get_nexa_energy_stats(self, device_id: str) -> ApiResponse:
        """Read daily device totals from the v4 API, independently of state polls."""
        # Cache per device and local date. A previous day's sample must never
        # survive midnight, even when it was fetched only seconds ago.
        with self._nexa_energy_lock:
            now = utils.now()
            cached = self._nexa_energy_cache.get(device_id)
            if (
                cached is not None
                and cached.start.date() == now.date()
                and 0
                <= (now - cached.created).total_seconds()
                < self._nexa_energy_cache_ttl
            ):
                return ApiResponse(True, cached, None)
            serial = self._nexa_device_cache.get(device_id, {}).get("serialNumber")
            if not serial:
                return ApiResponse(
                    False, None, "No device serial number for energy statistics."
                )
            if not self.nexa_token:
                return ApiResponse(False, None, "No Nexa token for energy statistics.")
            params = {
                "groupingType": 1,
                "groupingValue": now.strftime("%Y-%m-%d"),
                "filterType": 3,
                "filterValue": serial,
            }
            for attempt in range(2):
                try:
                    response = requests.get(
                        NEXA_API_V4_STATISTICS_URL,
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "token": self.nexa_token,
                        },
                        timeout=AUTH_TIMEOUT_SECONDS,
                    )
                except RequestException as err:
                    return ApiResponse(
                        False, None, f"Energy statistics network error: {err}"
                    )
                if response.status_code == 401 and attempt == 0:
                    if self._re_authenticate():
                        continue
                break
            if response.status_code != 200:
                return ApiResponse(
                    False, None, f"Energy statistics returned {response.status_code}"
                )
            try:
                payload = response.json()
                data = (
                    payload.get("data", payload) if isinstance(payload, dict) else None
                )
                if not isinstance(data, dict):
                    raise ValueError("Invalid statistics response")

                def values(field):
                    entries = data.get(field)
                    if entries is None:
                        return []
                    if not isinstance(entries, list):
                        raise ValueError(f"Invalid {field} statistics")
                    result = []
                    for item in entries:
                        if not isinstance(item, dict):
                            raise ValueError(f"Invalid {field} entry")
                        if item.get("value") is not None:
                            value = float(item["value"])
                            if not math.isfinite(value):
                                raise ValueError(
                                    "Statistics must contain finite numbers"
                                )
                            result.append((str(item.get("time", "")), value))
                    return result

                consumption = values("consumption")
                if not consumption:
                    return ApiResponse(False, None, "No energy statistics found.")

                def total(field):
                    samples = values(field)
                    return round(sum(v for _, v in samples), 4) if samples else None

                def latest(field):
                    samples = values(field)
                    return max(samples)[1] if samples else None

                result = EnergyConsumptionData(
                    created=now,
                    start=now.replace(hour=0, minute=0, second=0, microsecond=0),
                    end=now,
                    kwh=round(sum(v for _, v in consumption), 4),
                    effective_power=0,
                    cost=total("cost"),
                    saving=total("saving"),
                    temperature=latest("temperature"),
                    temperature_probe=latest("temperatureProbe"),
                    daily=True,
                )
            except (ValueError, TypeError, OverflowError) as err:
                return ApiResponse(False, None, f"Invalid energy statistics: {err}")
            self._nexa_energy_cache[device_id] = result
            return ApiResponse(True, result, None)

    def set_device_settings(
        self, device: RointeDevice, data: Dict[str, Any]
    ) -> ApiResponse:
        """Apply settings through the device's configured API backend."""
        if self.use_nexa_ws:
            return self._write_via_websocket(self._get_nexa_device_key(device), data)
        if not self._ensure_valid_auth():
            return ApiResponse(False, None, "Invalid authentication.")
        if any(key.startswith("boost_") for key in data):
            return ApiResponse(False, None, "Boost is only supported for Nexa devices.")
        body = dict(data)
        if "schedule" in body:
            try:
                changes = self._validate_schedule(body["schedule"])
            except ValueError as err:
                return ApiResponse(False, None, str(err))
            schedule = device.schedule
            body["schedule"] = [
                changes.get(
                    str(day),
                    schedule[day]
                    if isinstance(schedule, list) and day < len(schedule)
                    else schedule.get(str(day), "O" * 24)
                    if isinstance(schedule, dict)
                    else "O" * 24,
                )
                for day in range(7)
            ]
        url = f"{self.firebase_base_url}{FIREBASE_DEVICE_DATA_PATH_BY_ID.format(device.id)}"
        return self._send_patch_request(url, {"auth": self.auth_token}, body)

    @staticmethod
    def _validate_schedule(schedule: Any) -> Dict[str, str]:
        """Validate partial weekly schedules before sending them to a radiator."""
        if not isinstance(schedule, dict) or not schedule:
            raise ValueError("Schedule must be a non-empty dictionary.")
        result = {}
        for day, hours in schedule.items():
            key = str(day)
            if key not in {str(i) for i in range(7)}:
                raise ValueError("Schedule days must be between 0 and 6.")
            if not isinstance(hours, str) or len(hours) != 24:
                raise ValueError(f"Schedule for day {day} must contain 24 hours.")
            if any(char not in "CEO" for char in hours.upper()):
                raise ValueError("Schedule hours must be C, E or O.")
            result[key] = hours.upper()
        return result

    def _write_via_websocket(
        self, device_id: str, data: Dict[str, Any], _retry: bool = True
    ) -> ApiResponse:
        """Write an acknowledged, atomic settings update to Firebase (Nexa)."""
        supported = {
            "temp",
            "comfort",
            "eco",
            "ice",
            "status",
            "mode",
            "power",
            "schedule",
            "boost_active",
            "boost_countdown",
            "windows_open_mode",
            "silence_mode",
            "block_local",
            "block_remote",
            "pir_mode",
            "dont_disturb_mode",
            "ice_mode",
            "user_mode",
            "um_min_temp",
            "um_max_temp",
        }
        if not data or set(data) - supported:
            return ApiResponse(False, None, "Empty update or unsupported setting.")
        payload = dict(data)
        try:
            if "schedule" in payload:
                # Nested paths preserve days omitted from a partial schedule.
                for day, hours in self._validate_schedule(
                    payload.pop("schedule")
                ).items():
                    payload[f"schedule/{day}"] = hours
            for key in ("temp", "comfort", "eco", "ice", "um_min_temp", "um_max_temp"):
                if key in payload:
                    value = float(payload[key])
                    if not math.isfinite(value):
                        raise ValueError("Temperature must be finite.")
                    payload[key] = value
            for key in ("mode", "power", "boost_countdown"):
                if key in payload:
                    payload[key] = int(payload[key])
        except (TypeError, ValueError, OverflowError) as err:
            return ApiResponse(False, None, f"Invalid setting: {err}")

        nexa_ws = self._get_nexa_ws()
        if nexa_ws is None:
            return ApiResponse(False, None, "Nexa transport closed or unavailable.")

        payload["last_sync_datetime_app"] = round(datetime.now().timestamp() * 1000)
        response = nexa_ws.request(
            "m", {"p": f"/devices/{device_id}/data", "d": payload}
        )
        status = response.get("s") if response is not None else None
        if _retry and (response is None or status == "permission_denied"):
            self._force_nexa_reauth()
            return self._write_via_websocket(device_id, data, _retry=False)
        if status != "ok":
            return ApiResponse(False, None, f"Write rejected or unacknowledged: {status}")
        return ApiResponse(True, response, None)

    def _get_nexa_device_key(self, device: RointeDevice) -> str:
        """Resolve the Nexa device key for Firebase paths."""

        if device.serialnumber:
            return device.serialnumber
        cached = self._nexa_device_cache.get(device.id, {})
        if cached.get("serialNumber"):
            return cached["serialNumber"]
        return device.id

    def _get_nexa_ws(self) -> NexaWebSocket | None:
        """Create one transport even when the first device polls overlap."""
        with self._nexa_ws_lock:
            if self._closed or not self.firebase_ws_url:
                return None
            if self._nexa_ws is None:
                self._nexa_ws = NexaWebSocket(self.firebase_ws_url, self._nexa_ws_token)
            return self._nexa_ws

    def _nexa_ws_token(self) -> str | None:
        """Supply the latest Firebase token to each connection check."""
        with self._auth_lock:
            if not self._ensure_valid_auth():
                return None
            return self.auth_token

    def _force_nexa_reauth(self) -> None:
        with self._auth_lock:
            self.auth_token_expire_date = datetime.now() - timedelta(seconds=1)
        with self._nexa_ws_lock:
            nexa_ws = self._nexa_ws
        if nexa_ws is not None:
            nexa_ws.invalidate_auth()

    def close(self) -> None:
        """Prevent new transports and stop any current connection permanently."""
        with self._nexa_ws_lock:
            self._closed = True
            nexa_ws = self._nexa_ws
        if nexa_ws is not None:
            nexa_ws.close()

    def _read_via_websocket(self, path: str, _retry: bool = True) -> Any:
        """Read an acknowledged Firebase snapshot through the shared transport."""
        nexa_ws = self._get_nexa_ws()
        if nexa_ws is None:
            return None
        response = nexa_ws.request("g", {"p": path})
        status = response.get("s") if response is not None else None
        data = response.get("d") if response is not None else None
        permission_denied = status == "permission_denied" or (
            isinstance(data, str) and "permission denied" in data.lower()
        )
        if _retry and (response is None or permission_denied):
            self._force_nexa_reauth()
            return self._read_via_websocket(path, _retry=False)
        if status != "ok" or permission_denied:
            LOGGER.warning("Nexa WebSocket read failed (status=%s)", status)
            return None
        return data
