"""Protocol tests against frames captured from a real AIRTAP T4 (rev 3)."""

import pytest

from custom_components.airtap_ble import protocol as p


def test_get_all_matches_capture():
    assert p.get_parameters(4).hex() == "a50000080004451c00011011121314151617713e"


def test_set_speed_matches_capture():
    frame = p.set_parameters(4, p.output_values(True, 2, 3, None))
    assert frame.hex() == "a500000600045e1d00031001021201028a71"


def test_parse_get_reply():
    reply = bytes.fromhex(
        "a513002a00044732000110010211010512010113070c4012421300001404000000"
        "001504000000001608000000000000000017008439"
    )
    frame = p.parse_frame(reply)
    assert (frame.sequence, frame.command) == (4, p.CMD_GET)
    settings = p.decode_settings(p.decode_parameters(frame.payload), version=3)
    assert (settings.mode, settings.level_on, settings.level_off) == (p.Mode.ON, 1, 5)
    assert settings.raw[19] == bytes.fromhex("0c401242130000")
    assert settings.raw[23] == b""


def test_ack():
    frame = p.parse_frame(bytes.fromhex("a51300040004daf50003100012009e74"))
    p.check_ack(frame.payload, {16, 18})
    with pytest.raises(p.ProtocolError):
        p.check_ack(bytes([16, 0, 18, 1]), {16, 18})
    with pytest.raises(p.ProtocolError):
        p.check_ack(bytes([16, 0]), {16, 18})


def test_bad_crc_rejected():
    reply = bytearray.fromhex("a51300040004daf50003100012009e74")
    reply[-1] ^= 1
    with pytest.raises(p.ProtocolError):
        p.parse_frame(reply)


@pytest.mark.parametrize(
    ("raw", "mode", "level"),
    [
        ("1eff0209030c000007460000000027100012", p.Mode.ON, 1),
        ("1eff0209030c0000075b0000000027100022", p.Mode.ON, 2),
        ("1eff0209030c000007670000000027100001", p.Mode.OFF, 0),
    ],
)
def test_telemetry(raw, mode, level):
    t = p.parse_telemetry(bytes.fromhex(raw), version=3)
    assert (t.mode, t.level) == (mode, level)
    assert 18 < t.temperature < 19


def test_advertisement():
    on = p.parse_advertisement(
        bytes.fromhex("a4c138000001343533534303060007360000010000000000000000")
    )
    assert on == p.Advertisement(
        name="453SC",
        version=3,
        device_type=p.DEVICE_TYPE_AIRTAP,
        fahrenheit=True,
        temperature=18.46,
        humidity=0.0,
        level=1,
    )
    off = p.parse_advertisement(
        bytes.fromhex("a4c138000001343533534303060007620000000000000000000000")
    )
    assert off.level == 0
    assert p.parse_advertisement(b"\x00" * 5) is None


def test_assembler_splits_and_joins():
    a = p.FrameAssembler()
    telemetry = bytes.fromhex("1eff0209030c000007460000000027100012")
    reply = bytes.fromhex("a513000300023fa3000110010237a8")
    stream = b"\x00" + telemetry + reply
    frames = a.feed(stream[:7]) + a.feed(stream[7:25]) + a.feed(stream[25:])
    assert frames == [telemetry, reply]


def test_packed_level_needs_raw():
    with pytest.raises(p.ProtocolError):
        p.output_values(True, 4, 8, None)
    assert p.output_values(True, 4, 8, 0x27)[p.TAG_LEVEL_ON] == bytes([0x47])
