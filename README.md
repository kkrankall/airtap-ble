# AC Infinity AIRTAP (Bluetooth) for Home Assistant

Local Bluetooth control of the AC Infinity **AIRTAP T-series** register booster
fans (the Bluetooth-only models, app device type 6). Tested on an AIRTAP T4,
firmware revision 3. No cloud account, no AC Infinity app needed.

The cloud integration (`ac_infinity`) cannot see Bluetooth-only AIRTAPs; this
one uses a different domain (`airtap_ble`), so both can be installed together.

## What you get

| Entity | What it does |
| --- | --- |
| `fan.airtap_xxxxx` | On/off and 10 speeds. On/off are the vent's manual **On** and **Off** modes; turning on without a speed keeps the stored speed. |
| `select.airtap_xxxxx_mode` | Off, On, Auto, Timer to on, Timer to off, Cycle, Schedule. Auto/Timer/Cycle/Schedule use the triggers and times set in the AC Infinity app. |
| `sensor.airtap_xxxxx_temperature` | The vent's built-in temperature probe. |
| `sensor.airtap_xxxxx_speed` | Actual output level, 0–10. |
| `sensor.airtap_xxxxx_signal_strength` | RSSI (diagnostic, disabled by default). |

Temperature and speed come from the vent's advertisements, with no connection.
The mode is not advertised, so the integration connects for a few seconds when
the speed changes (mode changes almost always move it), when the mode is
unknown, and every 15 minutes (configurable under **Configure**; 0 = only on
speed changes). Commands connect on demand and the connection is dropped after
10 seconds idle, because the vent accepts only one connection at a time and
the AC Infinity app needs it too.

## Requirements

- A Bluetooth adapter or **ESPHome Bluetooth proxy** within range of the vent,
  with active connections enabled (`bluetooth_proxy: active: true`). Shelly
  and SMLIGHT proxies cannot make connections.
- Home Assistant 2026.10 or newer.

## Install

1. HACS → ⋮ → **Custom repositories** → add `https://github.com/kkrankall/airtap-ble`
   as an **Integration**, then download **AC Infinity AIRTAP (Bluetooth)**.
2. Restart Home Assistant.
3. The vent is discovered automatically once a proxy hears it. Otherwise:
   **Settings → Devices & services → Add integration → AC Infinity AIRTAP**.

## Protocol notes

Recovered from the AC Infinity iOS app 2.1.1 (`NewBLEManager`, the
`ControlMode*Command` builders and the AirTap Swift types), cross-checked with
the Android-derived [hunterjm/ac-infinity-ble](https://github.com/hunterjm/ac-infinity-ble)
(MIT), and verified on the hardware.

- **Advertisement**: manufacturer ID 2306 (0x0902), 27 bytes:
  `MAC[0:6] name[6:11] rev[11] type[12] flags[13] temp°C×100[14:16] hum[16:18] level[18]`.
  The T4 advertises as `BLE_FAN`, type 6, revision 3.
- **GATT**: service `70d51000-2c7f-4e75-ae8a-d758951ce4e0`; write `…1001`,
  notify `…1002`. The T4's write characteristic only supports
  **write-with-response**; unacknowledged writes are silently dropped.
- **Frame**: `A5 00 | len16 | seq16 | CRC16(header) | 00 cmd | payload | CRC16(00 cmd payload)`,
  CRC-16/CCITT-FALSE. Replies use `A5 13`. Commands: 1 = get, 3 = set.
- **Parameters** (tag, length, value): 16 mode (1 Off, 2 On, 3 Auto, 4 Timer
  to on, 5 Timer to off, 6 Cycle, 7 Schedule), 17 off-speed, 18 on-speed,
  19 auto triggers, 20/21 timers, 22 cycle, 23 schedule. A set is acknowledged
  with `(tag, 0)` pairs.
- **Telemetry** (`1E FF 02 09 …`, ~1/s while connected): temperature at bytes
  8–9, byte 17 = `speed << 4 | mode`.
- **Quirk**: for ~2–3 s after connecting, the T4 executes commands but sends no
  reply. The integration spends that window on a throwaway read.

## Tools

`tools/airtap_cli.py` drives a vent from a desktop with the same code:
`scan`, `read`, `on [speed]`, `off`, `mode auto`. On macOS, run it from
Terminal.app (Bluetooth permission is granted per app).

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
```

Not affiliated with AC Infinity.
