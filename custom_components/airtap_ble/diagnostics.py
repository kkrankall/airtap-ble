"""Diagnostics: decoded state plus the raw frames behind it."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant

from . import AirtapConfigEntry
from .protocol import MANUFACTURER_ID


def _hexify(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.hex()
    if isinstance(value, dict):
        return {str(k): _hexify(v) for k, v in value.items()}
    return value


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AirtapConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    device = coordinator.device
    info = coordinator.last_service_info
    return {
        "options": dict(entry.options),
        "firmware_revision": device.version,
        "state": _hexify(asdict(device.state)),
        "last_poll_successful": coordinator.last_poll_successful,
        "advertisement": {
            "name": info.name,
            "rssi": info.rssi,
            "source": info.source,
            "manufacturer_data": info.manufacturer_data.get(MANUFACTURER_ID, b"").hex(),
        }
        if info
        else None,
    }
