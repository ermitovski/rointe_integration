"""Night-mode start and end times in the device's local wall clock."""

from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN
from .rointe_entity import RointeRadiatorEntity


async def async_setup_entry(hass, entry, async_add_entities):
    """Discover supported night-mode time controls without reloading."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    registered = set()

    def discover():
        entities = []
        for device_id, device in coordinator.devices.items():
            for field, name in (("dont_disturb_start", "Night Start"), ("dont_disturb_end", "Night End")):
                key = (device_id, field)
                if key not in registered and device.supports_setting(field):
                    entities.append(RointeNightTime(coordinator, device, field, name))
                    registered.add(key)
        if entities:
            async_add_entities(entities)

    discover()
    entry.async_on_unload(coordinator.async_add_listener(discover))


class RointeNightTime(RointeRadiatorEntity, TimeEntity):
    """A daily time encoded as seconds after midnight; overnight is permitted."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-night"

    def __init__(self, coordinator, device, field, name):
        super().__init__(coordinator, device, name=name, unique_id=f"{device.id}_{field}")
        self.field = field

    @property
    def available(self):
        return super().available and self.device.supports_setting(self.field)

    @property
    def native_value(self):
        value = self.device._data.get(self.field) if self.device else None
        if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value < 86400:
            seconds = int(value)
            return time(seconds // 3600, seconds // 60 % 60, seconds % 60)
        return None

    async def async_set_value(self, value: time):
        if self.device is None:
            raise HomeAssistantError("Device is unavailable")
        result = await self.hass.async_add_executor_job(
            self.coordinator.rointe_api.set_optional_settings, self.device,
            {self.field: value.hour * 3600 + value.minute * 60 + value.second},
        )
        if not result.success:
            raise HomeAssistantError(result.error_message or "Unable to set night time")
        await self.coordinator.async_request_refresh()
