"""The Rointe Heaters integration."""

from __future__ import annotations

from .rointesdk import utils as sdk_utils
from .rointesdk.rointe_api import ApiResponse, RointeAPI
from homeassistant.util import dt as dt_util

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    API_TYPE_AUTO,
    CONF_API_TYPE,
    CONF_INSTALLATION,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import RointeDataUpdateCoordinator
from .device_manager import RointeDeviceManager
from .services import async_setup_services


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Rointe Heaters from a config entry."""

    sdk_utils.DEFAULT_TIME_ZONE = dt_util.DEFAULT_TIME_ZONE

    api_type = entry.options.get(CONF_API_TYPE, entry.data.get(CONF_API_TYPE))
    if not api_type:
        api_type = API_TYPE_AUTO
    rointe_api = RointeAPI(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        api_type=api_type,
    )

    async def async_close_api(event: Event | None = None) -> None:
        await hass.async_add_executor_job(rointe_api.close)

    # Home Assistant runs these callbacks on unload and failed setup attempts.
    entry.async_on_unload(async_close_api)
    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_close_api)
    )

    # Login to the Rointe API.
    login_result: ApiResponse = await hass.async_add_executor_job(
        rointe_api.initialize_authentication
    )

    if not login_result.success:
        raise ConfigEntryNotReady("Unable to connect to the Rointe API")

    rointe_device_manager = RointeDeviceManager(
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        installation_id=entry.data[CONF_INSTALLATION],
        hass=hass,
        rointe_api=rointe_api,
    )

    rointe_coordinator = RointeDataUpdateCoordinator(hass, entry, rointe_device_manager)

    await rointe_coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = rointe_coordinator

    # Set up services (only once)
    if not hass.services.has_service(DOMAIN, "activate_boost"):
        await async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and removes event handlers."""

    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
