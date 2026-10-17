"""A simulated AIRTAP T4 (firmware rev 3) behind a fake Bleak client.

Behaviour copied from the real vent (captures in tests/test_protocol.py):
acknowledged writes only, commands sent in the first seconds after connecting
are executed but never answered (modelled as the first command), replies use
header A5 13, and status telemetry follows changes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
import pytest

from custom_components.airtap_ble import protocol as p

pytest_plugins = "pytest_homeassistant_custom_component"

ADDRESS = "A4:C1:38:00:00:01"
# Real manufacturer data: On, speed 1, 18.46 C.
ADV_ON_1 = bytes.fromhex("a4c138000001343533534303060007360000010000000000000000")


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


def reply_frame(command: int, payload: bytes, sequence: int) -> bytes:
    frame = bytearray(p.build_frame(command, payload, sequence))
    frame[1] = 0x13
    frame[6:8] = p.crc16(frame[:6])
    return bytes(frame)


class FakeVent:
    def __init__(self) -> None:
        self.params = {
            16: bytes([2]),
            17: bytes([5]),
            18: bytes([1]),
            19: bytes.fromhex("0c401242130000"),
            20: bytes(4),
            21: bytes(4),
            22: bytes(8),
            23: b"",
        }
        self.level = 1
        self.temp = 1846
        self.connections = 0
        self.received: list[bytes] = []

    @property
    def mode(self) -> int:
        return self.params[16][0]

    def telemetry(self) -> bytes:
        return (
            p.TELEMETRY_PREFIX
            + bytes([3, 12, 0, 0])
            + self.temp.to_bytes(2, "big")
            + bytes(4)
            + bytes([0x27, 0x10, 0, (self.level << 4) | self.mode])
        )

    def handle(self, frame: bytes) -> list[bytes]:
        parsed = p.parse_frame(frame)
        self.received.append(frame)
        if parsed.command == p.CMD_GET:
            body = b"".join(
                bytes([tag, len(self.params[tag])]) + self.params[tag]
                for tag in parsed.payload
            )
            return [reply_frame(p.CMD_GET, body, parsed.sequence)]
        values = p.decode_parameters(parsed.payload)
        self.params.update(values)
        self.level = 0 if self.mode == p.Mode.OFF else self.params[18][0]
        ack = b"".join(bytes([tag, 0]) for tag in values)
        return [reply_frame(p.CMD_SET, ack, parsed.sequence), self.telemetry()]

    def advertisement(self) -> bytes:
        data = bytearray(ADV_ON_1)
        data[14:16] = self.temp.to_bytes(2, "big")
        data[18] = self.level
        return bytes(data)


class FakeClient:
    def __init__(self, vent: FakeVent) -> None:
        self.vent = vent
        vent.connections += 1
        self.is_connected = True
        self._callback = None
        self._buffer = b""
        self._first = True
        chars = {
            p.WRITE_CHAR_UUID: SimpleNamespace(properties=["read", "write"]),
            p.NOTIFY_CHAR_UUID: SimpleNamespace(properties=["read", "notify"]),
        }
        self.services = SimpleNamespace(get_characteristic=chars.get)

    async def start_notify(self, uuid, callback) -> None:
        assert uuid == p.NOTIFY_CHAR_UUID
        self._callback = callback

    async def write_gatt_char(self, uuid, data, response) -> None:
        assert uuid == p.WRITE_CHAR_UUID
        if not response:
            return  # the T4 silently drops unacknowledged writes
        self._buffer += bytes(data)
        need = int.from_bytes(self._buffer[2:4], "big") + 12
        if len(self._buffer) < need:
            return
        frame, self._buffer = self._buffer[:need], self._buffer[need:]
        replies = self.vent.handle(frame)
        if self._first:
            self._first = False
            return
        loop = asyncio.get_running_loop()
        for reply in replies:
            loop.call_soon(self._callback, None, bytearray(reply))

    async def disconnect(self) -> None:
        self.is_connected = False

    async def clear_cache(self) -> None:
        pass


@pytest.fixture
def vent():
    fake = FakeVent()

    async def connect(_cls, _device, _name, **_kwargs):
        return FakeClient(fake)

    with (
        patch("custom_components.airtap_ble.device.establish_connection", connect),
        patch("custom_components.airtap_ble.device.PRIME_TIMEOUT", 0.05),
    ):
        yield fake


def service_info(manufacturer_data: bytes, rssi: int = -70):
    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

    device = BLEDevice(ADDRESS, "BLE_FAN", {})
    adv = AdvertisementData(
        local_name="BLE_FAN",
        manufacturer_data={p.MANUFACTURER_ID: manufacturer_data},
        service_data={},
        service_uuids=[],
        tx_power=None,
        rssi=rssi,
        platform_data=(),
    )
    return BluetoothServiceInfoBleak(
        name="BLE_FAN",
        address=ADDRESS,
        rssi=rssi,
        manufacturer_data=adv.manufacturer_data,
        service_data={},
        service_uuids=[],
        source="local",
        device=device,
        advertisement=adv,
        connectable=True,
        time=__import__("time").monotonic(),
        tx_power=None,
    )


def inject(hass, info) -> None:
    from homeassistant.components.bluetooth.api import _get_manager

    _get_manager(hass).scanner_adv_received(info)
