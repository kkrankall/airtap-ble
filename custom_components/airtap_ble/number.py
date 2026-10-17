"""Speed slider, so speed can be set from the device page's Controls card."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import AirtapConfigEntry
from .entity import AirtapEntity
from .protocol import MAX_LEVEL, Mode


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirtapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AirtapSpeedNumber(entry, "speed")])


class AirtapSpeedNumber(AirtapEntity, NumberEntity):
    """Actual output speed; 0 is Off, 1-10 runs the vent in manual On mode."""

    _attr_translation_key = "speed"
    _attr_native_min_value = 0
    _attr_native_max_value = MAX_LEVEL
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER

    @property
    def native_value(self) -> int | None:
        return self.device.state.output_level

    async def async_set_native_value(self, value: float) -> None:
        level = round(value)
        if level == 0:
            await self._command(self.device.set_mode(Mode.OFF))
        else:
            await self._command(self.device.set_mode(Mode.ON, level))
