"""Advertisement-driven coordinator that polls the mode when needed."""

from __future__ import annotations

import logging

from bleak.exc import BleakError

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import (
    BluetoothChange,
    BluetoothScanningMode,
    BluetoothServiceInfoBleak,
)
from homeassistant.components.bluetooth.active_update_coordinator import (
    ActiveBluetoothDataUpdateCoordinator,
)
from homeassistant.core import CoreState, HomeAssistant, callback

from .device import AirtapDevice, AirtapError
from .protocol import MANUFACTURER_ID, parse_advertisement

_LOGGER = logging.getLogger(__name__)

RETRY_AFTER_FAILURE = 60


class AirtapCoordinator(ActiveBluetoothDataUpdateCoordinator[None]):
    """Feeds advertisements into the device and reads its mode on demand.

    Advertisements carry temperature and output level but not the mode, so
    the coordinator connects briefly when the level changes (mode changes
    usually move it), when the mode is unknown, or every refresh interval.
    """

    def __init__(
        self, hass: HomeAssistant, device: AirtapDevice, refresh_seconds: float
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            address=device.address,
            mode=BluetoothScanningMode.PASSIVE,
            needs_poll_method=self._needs_poll,
            poll_method=self._async_poll_device,
            connectable=True,
        )
        self.device = device
        self.refresh_seconds = refresh_seconds
        self.last_service_info: BluetoothServiceInfoBleak | None = None

    @callback
    def _needs_poll(
        self, service_info: BluetoothServiceInfoBleak, seconds_since_last_poll: float | None
    ) -> bool:
        if self.hass.state is not CoreState.running:
            return False
        if not bluetooth.async_ble_device_from_address(
            self.hass, service_info.device.address, connectable=True
        ):
            return False
        if seconds_since_last_poll is None:
            return True
        if not self.last_poll_successful and seconds_since_last_poll < RETRY_AFTER_FAILURE:
            return False
        state = self.device.state
        if state.mode is None or state.level != self.device.level_at_refresh:
            return True
        return bool(self.refresh_seconds) and seconds_since_last_poll >= self.refresh_seconds

    async def _async_poll_device(self, service_info: BluetoothServiceInfoBleak) -> None:
        self.device.set_ble_device(service_info.device)
        try:
            await self.device.refresh()
        except AirtapError as ex:
            # Expected while the AC Infinity app holds the connection; the base
            # class logs BleakError once, without a traceback.
            raise BleakError(str(ex)) from ex

    @callback
    def _async_handle_bluetooth_event(
        self, service_info: BluetoothServiceInfoBleak, change: BluetoothChange
    ) -> None:
        self.last_service_info = service_info
        raw = service_info.manufacturer_data.get(MANUFACTURER_ID)
        if raw is not None and (adv := parse_advertisement(raw)) is not None:
            self.device.update_from_advertisement(adv, service_info.rssi)
        if service_info.connectable:
            self.device.set_ble_device(service_info.device)
        super()._async_handle_bluetooth_event(service_info, change)
