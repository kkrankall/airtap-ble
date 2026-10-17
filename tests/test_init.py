"""Discovery, setup and control against the simulated AIRTAP."""

from datetime import timedelta
from unittest.mock import patch

from homeassistant.components.fan import (
    ATTR_PERCENTAGE,
    DOMAIN as FAN_DOMAIN,
    SERVICE_SET_PERCENTAGE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.components.select import (
    ATTR_OPTION,
    DOMAIN as SELECT_DOMAIN,
    SERVICE_SELECT_OPTION,
)
from homeassistant.config_entries import SOURCE_BLUETOOTH, SOURCE_USER
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
import pytest
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.airtap_ble import protocol as p
from custom_components.airtap_ble.const import CONF_REFRESH_MINUTES, DOMAIN

from .conftest import ADDRESS, ADV_ON_1, inject, service_info

FAN = "fan.airtap_453sc"
MODE = "select.airtap_453sc_mode"
TEMP = "sensor.airtap_453sc_temperature"
SPEED = "sensor.airtap_453sc_speed"


@pytest.fixture
async def setup(hass: HomeAssistant, enable_bluetooth, vent):
    inject(hass, service_info(ADV_ON_1))
    await hass.async_block_till_done()
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=ADDRESS, title="AIRTAP 453SC", data={"address": ADDRESS}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    # The next advert triggers the first mode poll.
    inject(hass, service_info(vent.advertisement()))
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_bluetooth_discovery(hass: HomeAssistant, enable_bluetooth):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(ADV_ON_1)
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "bluetooth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AIRTAP 453SC"
    assert result["result"].unique_id == ADDRESS


async def test_discovery_ignores_other_ac_infinity(hass: HomeAssistant, enable_bluetooth):
    controller = bytearray(ADV_ON_1)
    controller[12] = 11  # Controller 69 Pro
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_BLUETOOTH}, data=service_info(bytes(controller))
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "not_supported"


async def test_user_flow(hass: HomeAssistant, enable_bluetooth):
    inject(hass, service_info(ADV_ON_1))
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"address": ADDRESS}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "AIRTAP 453SC"


async def test_user_flow_nothing_heard(hass: HomeAssistant, enable_bluetooth):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_devices_found"


async def test_state_from_advert_and_poll(hass: HomeAssistant, setup, vent):
    assert hass.states.get(FAN).state == STATE_ON
    assert hass.states.get(FAN).attributes[ATTR_PERCENTAGE] == 10
    assert hass.states.get(MODE).state == "on"
    assert float(hass.states.get(TEMP).state) == pytest.approx(18.46)
    assert hass.states.get(SPEED).state == "1"
    # One connection: the throwaway prime read, then the real read.
    assert vent.connections == 1
    assert [p.parse_frame(f).payload for f in vent.received] == [
        bytes([16]),
        bytes(p.READ_TAGS),
    ]


async def test_fan_control(hass: HomeAssistant, setup, vent):
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_SET_PERCENTAGE, {ATTR_ENTITY_ID: FAN, ATTR_PERCENTAGE: 50},
        blocking=True,
    )
    assert vent.params[16] == bytes([p.Mode.ON]) and vent.params[18] == bytes([5])
    assert hass.states.get(FAN).attributes[ATTR_PERCENTAGE] == 50
    assert hass.states.get(SPEED).state == "5"

    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
    )
    assert vent.mode == p.Mode.OFF
    assert hass.states.get(FAN).state == STATE_OFF
    assert hass.states.get(MODE).state == "off"

    # Plain turn-on keeps the stored speed.
    await hass.services.async_call(
        FAN_DOMAIN, SERVICE_TURN_ON, {ATTR_ENTITY_ID: FAN}, blocking=True
    )
    assert vent.mode == p.Mode.ON and vent.params[18] == bytes([5])
    assert hass.states.get(FAN).state == STATE_ON


async def test_mode_select(hass: HomeAssistant, setup, vent):
    await hass.services.async_call(
        SELECT_DOMAIN, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: MODE, ATTR_OPTION: "auto"},
        blocking=True,
    )
    assert vent.mode == p.Mode.AUTO
    assert hass.states.get(MODE).state == "auto"


async def test_level_change_triggers_poll(hass: HomeAssistant, setup, vent):
    before = len(vent.received)
    # Speed changed elsewhere (app or buttons): the next advert shows it.
    vent.params[18] = bytes([3])
    vent.level = 3
    inject(hass, service_info(vent.advertisement()))
    # Polls are debounced with a 10 s cooldown after the setup poll.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(vent.received) > before
    assert hass.states.get(SPEED).state == "3"


async def test_busy_vent_poll_fails_quietly(
    hass: HomeAssistant, setup, vent, caplog: pytest.LogCaptureFixture
):
    async def busy(*_args, **_kwargs):
        raise TimeoutError("connection slot taken")

    await setup.runtime_data.device.stop()  # drop the idle connection from setup
    vent.level = 4
    with patch("custom_components.airtap_ble.device.establish_connection", busy):
        inject(hass, service_info(vent.advertisement()))
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
        await hass.async_block_till_done(wait_background_tasks=True)
    assert setup.runtime_data.last_poll_successful is False
    assert "Bluetooth error whilst polling" in caplog.text
    assert "Traceback" not in caplog.text
    # Advert data still flows while the vent cannot be reached.
    assert hass.states.get(SPEED).state == "4"


async def test_command_error(hass: HomeAssistant, setup):
    async def busy(*_args, **_kwargs):
        raise TimeoutError("connection slot taken")

    await setup.runtime_data.device.stop()
    with (
        patch("custom_components.airtap_ble.device.establish_connection", busy),
        pytest.raises(HomeAssistantError, match="is the AC Infinity app connected"),
    ):
        await hass.services.async_call(
            FAN_DOMAIN, SERVICE_TURN_OFF, {ATTR_ENTITY_ID: FAN}, blocking=True
        )


async def test_options(hass: HomeAssistant, setup):
    result = await hass.config_entries.options.async_init(setup.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_REFRESH_MINUTES: 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert setup.runtime_data.refresh_seconds == 0


async def test_unload(hass: HomeAssistant, setup):
    assert await hass.config_entries.async_unload(setup.entry_id)
    await hass.async_block_till_done()
