"""Bluetooth connection and state for one AIRTAP."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, replace
import logging
import time

from bleak.backends.device import BLEDevice
from bleak.exc import BleakError
from bleak_retry_connector import (
    BleakClientWithServiceCache,
    establish_connection,
)

from . import protocol as p

_LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 20  # fits the minimum ATT MTU; the AIRTAP accepts split frames
RESPONSE_TIMEOUT = 5
PRIME_TIMEOUT = 3.5  # the T4 answers nothing for ~2-3 s after connecting
DISCONNECT_DELAY = 15
ATTEMPTS = 2


@dataclass(frozen=True)
class AirtapState:
    """What we know about the vent; None means not observed yet."""

    temperature: float | None = None  # Celsius
    level: int | None = None  # actual output 0-10
    mode: int | None = None
    level_on: int | None = None
    rssi: int | None = None
    settings: dict[int, bytes] | None = None
    last_telemetry: bytes | None = None


class AirtapError(Exception):
    """Talking to the vent failed."""


class AirtapDevice:
    """One AIRTAP T-series vent.

    State comes from advertisements (temperature, output level) and, while
    connected, from GET responses and telemetry (mode). The vent only accepts
    one connection, so connections are short and dropped after idling, which
    leaves the AC Infinity app free to connect.
    """

    def __init__(self, ble_device: BLEDevice, version: int) -> None:
        self._ble_device = ble_device
        self.version = version
        self.state = AirtapState()
        self._client: BleakClientWithServiceCache | None = None
        self._write_response = True
        self._lock = asyncio.Lock()
        self._assembler = p.FrameAssembler()
        self._sequence = 0
        self._pending: dict[int, asyncio.Future[p.Frame]] = {}
        self._disconnect_timer: asyncio.TimerHandle | None = None
        self._disconnect_task: asyncio.Task | None = None
        self._callbacks: list[Callable[[], None]] = []
        self.last_refresh: float | None = None
        self.level_at_refresh: int | None = None

    @property
    def address(self) -> str:
        return self._ble_device.address

    def set_ble_device(self, ble_device: BLEDevice) -> None:
        self._ble_device = ble_device

    def register_callback(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._callbacks.append(callback)
        return lambda: self._callbacks.remove(callback)

    def _update(self, **changes) -> None:
        new = replace(self.state, **changes)
        if new != self.state:
            self.state = new
            for callback in list(self._callbacks):
                callback()

    def update_from_advertisement(self, adv: p.Advertisement, rssi: int | None) -> None:
        self.version = adv.version
        changes: dict = {"rssi": rssi}
        if adv.temperature is not None:
            changes["temperature"] = adv.temperature
        if adv.level is not None:
            changes["level"] = adv.level
        self._update(**changes)

    # Commands -----------------------------------------------------------

    async def refresh(self) -> None:
        """Read mode and levels (tags 16-23)."""
        async with self._lock:
            await self._ensure_connected()
            await self._read_settings()

    async def set_mode(self, mode: p.Mode, level: int | None = None) -> None:
        """Switch mode; with ON, optionally set the manual speed (1-10)."""
        if level is not None and mode != p.Mode.ON:
            raise ValueError("A speed can only be set with mode ON")
        async with self._lock:
            await self._ensure_connected()
            values = {p.TAG_MODE: bytes((mode,))}
            if level is not None:
                if self.version > 7 and self.state_raw_on is None:
                    # Packed speed byte: its low nibble must be preserved.
                    await self._read_settings()
                values = p.output_values(True, level, self.version, self.state_raw_on)
            frame = await self._execute(lambda seq: p.set_parameters(seq, values))
            try:
                p.check_ack(frame.payload, set(values))
            except p.ProtocolError as ex:
                raise AirtapError(str(ex)) from ex
            changes: dict = {"mode": int(mode)}
            if level is not None:
                changes["level_on"] = level
            if mode == p.Mode.OFF:
                changes["level"] = 0
            elif mode == p.Mode.ON:
                changes["level"] = level if level is not None else self.state.level_on
            self._update(**changes)
            # Confirm with the device; telemetry then tracks the ramp.
            await self._read_settings()

    @property
    def state_raw_on(self) -> int | None:
        settings = self.state.settings
        if settings and p.TAG_LEVEL_ON in settings and settings[p.TAG_LEVEL_ON]:
            return settings[p.TAG_LEVEL_ON][0]
        return None

    async def stop(self) -> None:
        self._cancel_disconnect_timer()
        async with self._lock:
            await self._disconnect()

    # Internals ----------------------------------------------------------

    async def _read_settings(self) -> None:
        frame = await self._execute(lambda seq: p.get_parameters(seq))
        try:
            values = p.decode_parameters(frame.payload)
            settings = p.decode_settings(values, self.version)
        except p.ProtocolError as ex:
            raise AirtapError(f"Unexpected settings reply: {ex}") from ex
        self.last_refresh = time.monotonic()
        self.level_at_refresh = self.state.level
        self._update(
            mode=settings.mode, level_on=settings.level_on, settings=settings.raw
        )

    def _next_sequence(self) -> int:
        self._sequence = self._sequence % 65500 + 1
        return self._sequence

    async def _execute(self, build: Callable[[int], bytes]) -> p.Frame:
        """Send a frame and wait for its reply, resending on silence.

        Every command here is idempotent, so a resend after a lost reply is safe.
        """
        last_error: Exception | None = None
        for _ in range(ATTEMPTS):
            try:
                return await self._send(build, RESPONSE_TIMEOUT)
            except TimeoutError as ex:
                last_error = ex
                _LOGGER.debug("%s: no reply, resending", self.address)
        raise AirtapError("The vent did not answer") from last_error

    async def _send(self, build: Callable[[int], bytes], timeout: float) -> p.Frame:
        assert self._client is not None
        sequence = self._next_sequence()
        frame = build(sequence)
        future: asyncio.Future[p.Frame] = asyncio.get_running_loop().create_future()
        self._pending[sequence] = future
        try:
            _LOGGER.debug("%s: -> %s", self.address, frame.hex())
            for offset in range(0, len(frame), CHUNK_SIZE):
                await self._client.write_gatt_char(
                    p.WRITE_CHAR_UUID,
                    frame[offset : offset + CHUNK_SIZE],
                    response=self._write_response,
                )
            async with asyncio.timeout(timeout):
                return await future
        except BleakError as ex:
            raise AirtapError(f"Bluetooth write failed: {ex}") from ex
        finally:
            self._pending.pop(sequence, None)
            self._schedule_disconnect()

    async def _ensure_connected(self) -> None:
        self._cancel_disconnect_timer()
        if self._client is not None and self._client.is_connected:
            return
        _LOGGER.debug("%s: connecting", self.address)
        try:
            client = await establish_connection(
                BleakClientWithServiceCache,
                self._ble_device,
                self.address,
                disconnected_callback=self._on_disconnected,
                ble_device_callback=lambda: self._ble_device,
            )
        except (BleakError, TimeoutError) as ex:
            raise AirtapError(
                f"Could not connect (is the AC Infinity app connected?): {ex}"
            ) from ex
        char = client.services.get_characteristic(p.WRITE_CHAR_UUID)
        if char is None or client.services.get_characteristic(p.NOTIFY_CHAR_UUID) is None:
            await client.clear_cache()
            await client.disconnect()
            raise AirtapError("AC Infinity characteristics not found")
        # The AIRTAP T4 only offers acknowledged writes.
        self._write_response = "write-without-response" not in char.properties
        self._assembler = p.FrameAssembler()
        self._client = client
        try:
            await client.start_notify(p.NOTIFY_CHAR_UUID, self._on_notify)
            # For the first ~2-3 s after connecting, rev 3 firmware executes
            # commands but sends no reply. Spend that window on a harmless read;
            # when it is answered the vent is ready sooner.
            try:
                await self._send(lambda seq: p.get_parameters(seq, (p.TAG_MODE,)), PRIME_TIMEOUT)
            except TimeoutError:
                pass
        except (BleakError, AirtapError):
            await self._disconnect()
            raise

    def _on_notify(self, _char, data: bytearray) -> None:
        for frame in self._assembler.feed(data):
            if frame[0] == 0xA5:
                try:
                    reply = p.parse_frame(frame)
                except p.ProtocolError as ex:
                    _LOGGER.debug("%s: bad frame %s: %s", self.address, frame.hex(), ex)
                    continue
                _LOGGER.debug("%s: <- %s", self.address, frame.hex())
                future = self._pending.get(reply.sequence)
                if future is not None and not future.done():
                    future.set_result(reply)
            else:
                try:
                    telemetry = p.parse_telemetry(frame, self.version)
                except p.ProtocolError:
                    _LOGGER.debug("%s: unparsed telemetry %s", self.address, frame.hex())
                    continue
                changes: dict = {"mode": telemetry.mode, "last_telemetry": telemetry.raw}
                if telemetry.level is not None:
                    changes["level"] = telemetry.level
                if telemetry.temperature is not None:
                    changes["temperature"] = telemetry.temperature
                self._update(**changes)

    def _on_disconnected(self, _client) -> None:
        _LOGGER.debug("%s: disconnected", self.address)
        self._client = None
        for future in self._pending.values():
            if not future.done():
                future.set_exception(AirtapError("Disconnected"))

    def _schedule_disconnect(self) -> None:
        self._cancel_disconnect_timer()
        self._disconnect_timer = asyncio.get_running_loop().call_later(
            DISCONNECT_DELAY, self._start_idle_disconnect
        )

    def _start_idle_disconnect(self) -> None:
        self._disconnect_task = asyncio.get_running_loop().create_task(
            self._idle_disconnect()
        )

    def _cancel_disconnect_timer(self) -> None:
        if self._disconnect_timer is not None:
            self._disconnect_timer.cancel()
            self._disconnect_timer = None

    async def _idle_disconnect(self) -> None:
        self._disconnect_timer = None
        if self._lock.locked():
            return
        async with self._lock:
            await self._disconnect()

    async def _disconnect(self) -> None:
        client, self._client = self._client, None
        if client is not None and client.is_connected:
            _LOGGER.debug("%s: disconnecting", self.address)
            try:
                await client.disconnect()
            except BleakError:
                pass
