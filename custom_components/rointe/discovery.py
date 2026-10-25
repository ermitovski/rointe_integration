"""Register newly discovered entities throughout a config entry's lifetime."""

from __future__ import annotations

from homeassistant.core import callback


@callback
def setup_description_entities(
    entry, coordinator, async_add_entities, descriptions, constructor
):
    """Add descriptions when their device condition first becomes true."""
    registered: set[tuple[str, str]] = set()

    @callback
    def add_entities() -> None:
        entities = []
        for device_id, device in coordinator.devices.items():
            for description in descriptions:
                key = (device_id, description.key)
                if key not in registered and description.available_fn(device):
                    entities.append(constructor(device, coordinator, description))
                    registered.add(key)
        if entities:
            async_add_entities(entities)

    add_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_entities))
