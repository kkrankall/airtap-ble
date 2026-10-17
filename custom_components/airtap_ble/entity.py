"""Base entity for the AIRTAP."""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from . import AirtapConfigEntry
from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import AirtapCoordinator
from .device import AirtapError


class AirtapEntity(PassiveBluetoothCoordinatorEntity[AirtapCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: AirtapConfigEntry, key: str) -> None:
        coordinator = entry.runtime_data
        super().__init__(coordinator)
        self.device = coordinator.device
        self._attr_unique_id = f"{coordinator.address}_{key}"
        self._attr_device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=entry.title,
            sw_version=f"rev {self.device.version}",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Telemetry and command results update the device between adverts.
        self.async_on_remove(self.device.register_callback(self.async_write_ha_state))

    async def _command(self, call: Awaitable[None]) -> None:
        try:
            await call
        except AirtapError as ex:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"error": str(ex)},
            ) from ex
