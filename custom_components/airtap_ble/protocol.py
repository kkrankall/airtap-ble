"""AC Infinity Bluetooth wire protocol, as used by the AIRTAP T-series (type 6).

Framing, CRC and parameter tags come from the AC Infinity iOS app 2.1.1
(NewBLEManager / ControlMode*Command) and match the Android-derived
documentation in hunterjm/ac-infinity-ble (MIT). This module has no Home
Assistant imports so the same code drives the integration and tools/.
"""

from __future__ import annotations

import binascii
from dataclasses import dataclass, field
from enum import IntEnum

MANUFACTURER_ID = 2306  # 0x0902, AC Infinity
SERVICE_UUID = "70d51000-2c7f-4e75-ae8a-d758951ce4e0"
WRITE_CHAR_UUID = "70d51001-2c7f-4e75-ae8a-d758951ce4e0"
NOTIFY_CHAR_UUID = "70d51002-2c7f-4e75-ae8a-d758951ce4e0"

DEVICE_TYPE_AIRTAP = 6

CMD_GET = 1
CMD_SET = 3

TAG_MODE = 16
TAG_LEVEL_OFF = 17
TAG_LEVEL_ON = 18
READ_TAGS = tuple(range(16, 24))

MAX_LEVEL = 10

TELEMETRY_PREFIX = b"\x1e\xff\x02\x09"


class Mode(IntEnum):
    """Controller work modes shared by the AIRTAP T-series and Controller 67."""

    OFF = 1
    ON = 2
    AUTO = 3
    TIMER_TO_ON = 4
    TIMER_TO_OFF = 5
    CYCLE = 6
    SCHEDULE = 7
    VPD = 8


class ProtocolError(Exception):
    """A frame failed validation or the device rejected a command."""


def crc16(data: bytes | bytearray) -> bytes:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF), big-endian."""
    return binascii.crc_hqx(bytes(data), 0xFFFF).to_bytes(2, "big")


def build_frame(command: int, payload: bytes, sequence: int) -> bytes:
    """A5 00 | len16 | seq16 | crc(header) | 00 cmd payload | crc(body)."""
    header = (
        b"\xa5\x00"
        + len(payload).to_bytes(2, "big")
        + (sequence & 0xFFFF).to_bytes(2, "big")
    )
    body = bytes((0, command)) + payload
    return header + crc16(header) + body + crc16(body)


@dataclass(frozen=True)
class Frame:
    sequence: int
    command: int
    payload: bytes


def parse_frame(data: bytes | bytearray) -> Frame:
    data = bytes(data)
    if len(data) < 12 or data[0] != 0xA5 or data[1] > 0x1F:
        raise ProtocolError("Invalid response header")
    if len(data) != int.from_bytes(data[2:4], "big") + 12:
        raise ProtocolError("Invalid response length")
    if data[6:8] != crc16(data[:6]):
        raise ProtocolError("Invalid header CRC")
    if data[-2:] != crc16(data[8:-2]):
        raise ProtocolError("Invalid body CRC")
    return Frame(
        sequence=int.from_bytes(data[4:6], "big"),
        command=int.from_bytes(data[8:10], "big"),
        payload=data[10:-2],
    )


def get_parameters(sequence: int, tags: tuple[int, ...] = READ_TAGS) -> bytes:
    return build_frame(CMD_GET, bytes(tags), sequence)


def set_parameters(sequence: int, values: dict[int, bytes]) -> bytes:
    payload = bytearray()
    for tag, value in values.items():
        payload += bytes((tag, len(value))) + value
    return build_frame(CMD_SET, bytes(payload), sequence)


def decode_parameters(payload: bytes) -> dict[int, bytes]:
    """Decode a GET response body: repeated tag, len8, value."""
    values: dict[int, bytes] = {}
    offset = 0
    while offset < len(payload):
        if offset + 2 > len(payload):
            raise ProtocolError("Truncated parameter header")
        tag, length = payload[offset], payload[offset + 1]
        offset += 2
        if offset + length > len(payload):
            raise ProtocolError("Truncated parameter value")
        values[tag] = payload[offset : offset + length]
        offset += length
    return values


def check_ack(payload: bytes, tags: set[int]) -> None:
    """A SET response is (tag, result) pairs; any non-zero result is a reject."""
    if len(payload) % 2:
        raise ProtocolError("Invalid acknowledgement length")
    results = dict(zip(payload[::2], payload[1::2], strict=True))
    if not tags <= results.keys():
        raise ProtocolError(f"Missing acknowledgement for {tags - results.keys()}")
    if rejected := {tag: res for tag, res in results.items() if res}:
        raise ProtocolError(f"Device rejected {rejected}")


def output_values(
    on: bool, level: int | None, version: int, raw_on: int | None
) -> dict[int, bytes]:
    """Mode (and on-level) parameters for manual on/off.

    Firmware revisions above 7 pack the manual on-level into the high nibble of
    tag 18 and keep another setting in the low nibble, so the current raw value
    must be read first.
    """
    values = {TAG_MODE: bytes((Mode.ON if on else Mode.OFF,))}
    if on and level is not None:
        if not 0 <= level <= MAX_LEVEL:
            raise ValueError("Level must be 0-10")
        if version > 7:
            if raw_on is None:
                raise ProtocolError("Read settings before changing the level")
            values[TAG_LEVEL_ON] = bytes(((level << 4) | (raw_on & 0x0F),))
        else:
            values[TAG_LEVEL_ON] = bytes((level,))
    return values


@dataclass(frozen=True)
class Settings:
    """Configured mode and levels from a GET of tags 16-23."""

    mode: int
    level_off: int
    level_on: int
    raw_on: int
    raw: dict[int, bytes] = field(default_factory=dict)


def decode_settings(values: dict[int, bytes], version: int) -> Settings:
    try:
        mode, level_off, raw_on = values[16][0], values[17][0], values[18][0]
    except (KeyError, IndexError) as ex:
        raise ProtocolError("Missing mode parameters") from ex
    level_on = raw_on >> 4 if version > 7 else raw_on & 0x0F
    return Settings(
        mode=mode & 0x0F,
        level_off=level_off & 0x0F,
        level_on=level_on,
        raw_on=raw_on,
        raw=dict(values),
    )


def _reading(data: bytes, offset: int, scale: int = 100) -> float | None:
    value = int.from_bytes(data[offset : offset + 2], "big", signed=True)
    return None if value == -32768 else value / scale


@dataclass(frozen=True)
class Advertisement:
    """Manufacturer data (company 2306) from a single-port AC Infinity device."""

    name: str
    version: int
    device_type: int
    fahrenheit: bool
    temperature: float | None = None  # Celsius
    humidity: float | None = None
    level: int | None = None


def parse_advertisement(data: bytes) -> Advertisement | None:
    """Parse the 17- or 27-byte manufacturer payload; None if not ours."""
    if len(data) not in (17, 27):
        return None
    try:
        name = data[6:11].decode("ascii")
    except UnicodeDecodeError:
        return None
    adv = Advertisement(
        name=name,
        version=data[11],
        device_type=data[12],
        # Bit 1 (MSB-first) of byte 13 is set for Celsius display.
        fahrenheit=not data[13] & 0x40,
    )
    if len(data) == 17:
        return adv
    return Advertisement(
        name=adv.name,
        version=adv.version,
        device_type=adv.device_type,
        fahrenheit=adv.fahrenheit,
        temperature=_reading(data, 14),
        humidity=_reading(data, 16),
        level=data[18] if data[18] <= MAX_LEVEL else None,
    )


@dataclass(frozen=True)
class Telemetry:
    """Unsolicited 1E FF 02 09 status frame."""

    fahrenheit: bool
    temperature: float | None
    humidity: float | None
    mode: int
    level: int | None
    raw: bytes


def telemetry_length(data: bytes) -> int | None:
    return 6 + data[5] if len(data) >= 6 else None


def parse_telemetry(data: bytes, version: int) -> Telemetry:
    if data[:4] != TELEMETRY_PREFIX or len(data) != telemetry_length(data):
        raise ProtocolError("Invalid telemetry framing")
    if len(data) < 18:
        raise ProtocolError("Truncated telemetry")
    # Byte 17 is level << 4 | mode on the AIRTAP T4 (rev 3, verified on
    # hardware). Revisions 5+ move the level into byte 16 (APK layout, not
    # verified on an AIRTAP).
    level = (data[16] >> 2) & 0x0F if version >= 5 else data[17] >> 4
    return Telemetry(
        fahrenheit=bool(data[6] & 0x80),
        temperature=_reading(data, 8),
        humidity=_reading(data, 10),
        mode=data[17] & 0x0F,
        level=level if level <= MAX_LEVEL else None,
        raw=bytes(data),
    )


class FrameAssembler:
    """Reassemble notification chunks into response or telemetry frames."""

    def __init__(self) -> None:
        self._buf = bytearray()

    def feed(self, chunk: bytes | bytearray) -> list[bytes]:
        self._buf += chunk
        frames: list[bytes] = []
        while self._buf:
            if self._buf[0] == 0xA5:
                if len(self._buf) < 8:
                    break
                if bytes(self._buf[6:8]) != crc16(self._buf[:6]):
                    del self._buf[0]
                    continue
                need = int.from_bytes(self._buf[2:4], "big") + 12
            elif self._buf[:2] == b"\x1e\xff":
                if len(self._buf) < 6:
                    break
                need = 6 + self._buf[5]
            else:
                # Resynchronise on the next plausible frame start.
                del self._buf[0]
                continue
            if len(self._buf) < need:
                break
            frames.append(bytes(self._buf[:need]))
            del self._buf[:need]
        return frames
