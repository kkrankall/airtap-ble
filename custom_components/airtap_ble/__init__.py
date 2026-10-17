"""AC Infinity AIRTAP vents over Bluetooth."""

from __future__ import annotations

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import Event, HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES
from .coordinator import AirtapCoordinator
from .device import AirtapDevice
from .protocol import MANUFACTURER_ID, parse_advertisement

PLATFORMS = [Platform.FAN, Platform.SELECT, Platform.SENSOR]

type AirtapConfigEntry = ConfigEntry[AirtapCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: AirtapConfigEntry) -> bool:
    address: str = entry.unique_id
    ble_device = bluetooth.async_ble_device_from_address(hass, address, connectable=True)
    if ble_device is None:
        raise ConfigEntryNotReady(
            f"AIRTAP {address} is not in range of a connectable Bluetooth adapter or proxy"
        )
    device = AirtapDevice(ble_device, version=3)
    if (info := bluetooth.async_last_service_info(hass, address, connectable=True)) and (
        raw := info.manufacturer_data.get(MANUFACTURER_ID)
    ):
        if adv := parse_advertisement(raw):
            device.update_from_advertisement(adv, info.rssi)

    refresh = entry.options.get(CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES) * 60
    coordinator = AirtapCoordinator(hass, device, refresh)
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(coordinator.async_start())
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    async def _async_stop(_event: Event) -> None:
        await device.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _async_stop)
    )
    return True


async def _async_options_updated(hass: HomeAssistant, entry: AirtapConfigEntry) -> None:
    entry.runtime_data.refresh_seconds = (
        entry.options.get(CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES) * 60
    )


async def async_unload_entry(hass: HomeAssistant, entry: AirtapConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.device.stop()
    return unload_ok
