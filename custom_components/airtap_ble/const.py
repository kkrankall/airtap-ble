"""Constants for the AC Infinity AIRTAP (Bluetooth) integration."""

DOMAIN = "airtap_ble"
MANUFACTURER = "AC Infinity"
MODEL = "AIRTAP T-series"

CONF_REFRESH_MINUTES = "refresh_minutes"
DEFAULT_REFRESH_MINUTES = 15

# Modes exposed by the Mode select, in device order (tag 16 values 1-7).
MODE_OPTIONS = [
    "off",
    "on",
    "auto",
    "timer_to_on",
    "timer_to_off",
    "cycle",
    "schedule",
]
