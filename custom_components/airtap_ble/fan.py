"""The AIRTAP as a 10-speed fan."""

from __future__ import annotations

import math
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from . import AirtapConfigEntry
from .entity import AirtapEntity
from .protocol import MAX_LEVEL, Mode

SPEED_RANGE = (1, MAX_LEVEL)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AirtapConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AirtapFan(entry, "fan")])


class AirtapFan(AirtapEntity, FanEntity):
    """On/off and speed; on/off maps to the vent's manual On and Off modes."""

    _attr_name = None
    _attr_speed_count = MAX_LEVEL
    _attr_supported_features = (
        FanEntityFeature.SET_SPEED | FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF
    )

    @property
    def is_on(self) -> bool | None:
        """Running: not in Off mode and blowing (Auto/Timer can idle at 0)."""
        state = self.device.state
        if state.mode == Mode.OFF:
            return False
        if state.level is not None:
            return state.level > 0
        return None if state.mode is None else True

    @property
    def percentage(self) -> int | None:
        level = self.device.state.output_level
        if level is None:
            return None
        return ranged_value_to_percentage(SPEED_RANGE, level) if level else 0

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        if percentage == 0:
            await self.async_turn_off()
            return
        level = (
            math.ceil(percentage_to_ranged_value(SPEED_RANGE, percentage))
            if percentage is not None
            else None
        )
        await self._command(self.device.set_mode(Mode.ON, level))

    async def async_set_percentage(self, percentage: int) -> None:
        await self.async_turn_on(percentage)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.device.set_mode(Mode.OFF))
