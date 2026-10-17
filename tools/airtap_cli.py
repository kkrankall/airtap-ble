"""Scan for or control an AIRTAP from a desktop, using the integration's code.

    python tools/airtap_cli.py scan [seconds]
    python tools/airtap_cli.py read [address]
    python tools/airtap_cli.py on [address] [speed 1-10]
    python tools/airtap_cli.py off [address]
    python tools/airtap_cli.py mode [address] auto|cycle|schedule|...

The address defaults to the first AIRTAP heard. On macOS addresses are
CoreBluetooth UUIDs and the terminal app needs Bluetooth permission. The vent
accepts one connection at a time, so close the AC Infinity app first.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib
import sys

from bleak import BleakScanner

sys.path.insert(0, str(pathlib.Path(__file__).parents[1]))

from custom_components.airtap_ble import protocol as p  # noqa: E402
from custom_components.airtap_ble.device import AirtapDevice  # noqa: E402


async def scan(seconds: float) -> None:
    seen: set[tuple[str, bytes]] = set()

    def cb(device, adv):
        raw = adv.manufacturer_data.get(p.MANUFACTURER_ID)
        if raw is not None and (device.address, raw) not in seen:
            seen.add((device.address, raw))
            print(device.address, adv.local_name, adv.rssi, raw.hex(), p.parse_advertisement(raw))

    async with BleakScanner(cb):
        await asyncio.sleep(seconds)


async def connect(address: str | None) -> AirtapDevice:
    found: asyncio.Future = asyncio.get_running_loop().create_future()

    def cb(device, adv):
        raw = adv.manufacturer_data.get(p.MANUFACTURER_ID)
        if raw is None or found.done() or address not in (None, device.address):
            return
        parsed = p.parse_advertisement(raw)
        if parsed is not None and parsed.device_type == p.DEVICE_TYPE_AIRTAP:
            found.set_result((device, parsed, adv.rssi))

    async with BleakScanner(cb):
        ble_device, adv, rssi = await asyncio.wait_for(found, 90)
    vent = AirtapDevice(ble_device, adv.version)
    vent.update_from_advertisement(adv, rssi)
    return vent


async def main(args: list[str]) -> None:
    action = args[0] if args else "scan"
    if action == "scan":
        await scan(float(args[1]) if len(args) > 1 else 20)
        return
    address = args[1] if len(args) > 1 and args[1] != "auto" else None
    vent = await connect(address)
    try:
        if action == "read":
            await vent.refresh()
        elif action == "on":
            await vent.set_mode(p.Mode.ON, int(args[2]) if len(args) > 2 else None)
        elif action == "off":
            await vent.set_mode(p.Mode.OFF)
        elif action == "mode":
            await vent.set_mode(p.Mode[args[2].upper()])
        else:
            print(__doc__)
            return
        await asyncio.sleep(2)  # let telemetry report the new output level
        state = vent.state
        mode = p.Mode(state.mode).name if state.mode in iter(p.Mode) else state.mode
        print(f"mode={mode} speed={state.level} on-speed={state.level_on} "
              f"temperature={state.temperature} C")
    finally:
        await vent.stop()


if "-v" in sys.argv:
    sys.argv.remove("-v")
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger("bleak").setLevel(logging.WARNING)
asyncio.run(main(sys.argv[1:]))
