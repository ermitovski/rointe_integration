"""Firmware updates are exposed only for APIs that support checking them."""

from functools import partial
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from homeassistant.helpers import entity_registry as er

from custom_components.rointe.const import DOMAIN
from custom_components.rointe.coordinator import RointeDataUpdateCoordinator
from custom_components.rointe.update import async_setup_entry


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_entities", [False, True])
async def test_nexa_skips_updates_and_removes_only_its_firmware_entries(
    monkeypatch, existing_entities
):
    coordinator = SimpleNamespace(
        rointe_api=SimpleNamespace(use_nexa_ws=True),
        add_entities_for_seen_keys=MagicMock(),
        async_add_listener=MagicMock(),
    )
    entry = SimpleNamespace(entry_id="nexa", async_on_unload=MagicMock())
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: coordinator}})
    registry = MagicMock()
    entries = [
        SimpleNamespace(
            entity_id=entity_id, platform=platform, unique_id=unique_id,
            config_entry_id=config_entry_id, domain=entity_id.split(".", 1)[0],
        )
        for entity_id, platform, unique_id, config_entry_id in [
            ("update.renamed", DOMAIN, "one-fw_update_available", "nexa"),
            ("update.two", DOMAIN, "two-fw_update_available", "nexa"),
            ("sensor.temperature", DOMAIN, "one-temperature", "nexa"),
            ("update.other_kind", DOMAIN, "other", "nexa"),
            ("update.other_integration", "other", "one-fw_update_available", "nexa"),
            ("update.legacy", DOMAIN, "legacy-fw_update_available", "legacy"),
        ]
    ] if existing_entities else []
    registry.entities.get_entries_for_config_entry_id.side_effect = lambda entry_id: [
        entity for entity in entries if entity.config_entry_id == entry_id
    ]
    monkeypatch.setattr(er, "async_get", lambda _: registry)
    add_entities = MagicMock()

    await async_setup_entry(hass, entry, add_entities)

    assert registry.async_remove.call_args_list == (
        [call("update.renamed"), call("update.two")] if existing_entities else []
    )
    registry.entities.get_entries_for_config_entry_id.assert_called_once_with("nexa")
    add_entities.assert_not_called()
    coordinator.add_entities_for_seen_keys.assert_not_called()
    coordinator.async_add_listener.assert_not_called()
    entry.async_on_unload.assert_not_called()


@pytest.mark.asyncio
async def test_legacy_keeps_updates_and_discovers_new_devices(monkeypatch):
    def device(device_id):
        return SimpleNamespace(
            id=device_id, name=device_id,
            firmware_version="1.0", latest_firmware_version="1.1",
        )

    coordinator = SimpleNamespace(
        rointe_api=SimpleNamespace(use_nexa_ws=False),
        data={"one": device("one")},
        _registered_devices={"update": set()},
        unregistered_keys={"update": {}},
        async_add_listener=MagicMock(),
    )
    coordinator.add_entities_for_seen_keys = partial(
        RointeDataUpdateCoordinator.add_entities_for_seen_keys, coordinator
    )
    entry = SimpleNamespace(entry_id="legacy", async_on_unload=MagicMock())
    hass = SimpleNamespace(data={DOMAIN: {entry.entry_id: coordinator}})
    registry_get = MagicMock()
    monkeypatch.setattr(er, "async_get", registry_get)
    added = []

    await async_setup_entry(hass, entry, added.extend)

    assert len(added) == 1
    assert added[0].installed_version == "1.0"
    assert added[0].latest_version == "1.1"
    listener = coordinator.async_add_listener.call_args.args[0]
    coordinator.data["two"] = device("two")
    listener()
    listener()
    assert [entity.unique_id for entity in added] == [
        "one-fw_update_available", "two-fw_update_available",
    ]
    entry.async_on_unload.assert_called_once_with(
        coordinator.async_add_listener.return_value
    )
    registry_get.assert_not_called()
