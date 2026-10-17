"""The AIRTAP's work mode."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AirtapConfigEntry
from .const import MODE_OPTIONS
from .entity import AirtapEntity
from .protocol import Mode


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirtapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AirtapModeSelect(entry, "mode")])


class AirtapModeSelect(AirtapEntity, SelectEntity):
    """Switching modes reuses the triggers, timers and schedules set in the app."""

    _attr_translation_key = "mode"
    _attr_options = MODE_OPTIONS

    @property
    def current_option(self) -> str | None:
        mode = self.device.state.mode
        if mode is None or not 1 <= mode <= len(MODE_OPTIONS):
            return None
        return MODE_OPTIONS[mode - 1]

    async def async_select_option(self, option: str) -> None:
        await self._command(self.device.set_mode(Mode(MODE_OPTIONS.index(option) + 1)))
