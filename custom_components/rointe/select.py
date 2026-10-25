"""Validated display-timeout choices from the Nexa app."""

from homeassistant.components.select import SelectEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .rointe_entity import RointeRadiatorEntity

OPTIONS = {"Always on": 0, "10 seconds": 10, "30 seconds": 30}


async def async_setup_entry(hass, entry, async_add_entities):
    """Discover display timeout only when the device reports support."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    registered = set()

    def discover():
        entities = []
        for device_id, device in coordinator.devices.items():
            if device_id not in registered and device.supports_setting("backlight_time"):
                entities.append(RointeDisplayTimeout(coordinator, device))
                registered.add(device_id)
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class RointeDisplayTimeout(RointeRadiatorEntity, SelectEntity):
    """Choose a supported timeout rather than sending arbitrary seconds."""

    _attr_name = "Display Timeout"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:monitor"
    _attr_options = list(OPTIONS)

    def __init__(self, coordinator, device):
        super().__init__(coordinator, device, unique_id=f"{device.id}_display_timeout")

    @property
    def available(self):
        return super().available and self.device.supports_setting("backlight_time")

    @property
    def current_option(self):
        value = self.device._data.get("backlight_time") if self.device else None
        return next((name for name, seconds in OPTIONS.items() if seconds == value), None)

    async def async_select_option(self, option):
        if option not in OPTIONS or self.device is None:
            raise HomeAssistantError("Invalid timeout or unavailable device")
        result = await self.hass.async_add_executor_job(
            self.coordinator.rointe_api.set_optional_settings, self.device,
            {"backlight_time": OPTIONS[option]},
        )
        if not result.success:
            raise HomeAssistantError(result.error_message or "Unable to set display timeout")
        await self.coordinator.async_request_refresh()
