"""Config flow: Bluetooth discovery or a pick list of nearby AIRTAPs."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components.bluetooth import (
    BluetoothServiceInfoBleak,
    async_discovered_service_info,
)
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback

from .const import CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES, DOMAIN
from .protocol import DEVICE_TYPE_AIRTAP, MANUFACTURER_ID, parse_advertisement


def _airtap_title(info: BluetoothServiceInfoBleak) -> str | None:
    """Title for an AIRTAP advertisement, or None for anything else."""
    raw = info.manufacturer_data.get(MANUFACTURER_ID)
    adv = parse_advertisement(raw) if raw is not None else None
    if adv is None or adv.device_type != DEVICE_TYPE_AIRTAP:
        return None
    return f"AIRTAP {adv.name}"


class AirtapConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._title: str | None = None
        self._discovered: dict[str, str] = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> AirtapOptionsFlow:
        return AirtapOptionsFlow()

    async def async_step_bluetooth(
        self, discovery_info: BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()
        if (title := _airtap_title(discovery_info)) is None:
            return self.async_abort(reason="not_supported")
        self._title = title
        self.context["title_placeholders"] = {"name": title}
        return await self.async_step_bluetooth_confirm()

    async def async_step_bluetooth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._title is not None
        if user_input is not None:
            return self.async_create_entry(
                title=self._title, data={CONF_ADDRESS: self.unique_id}
            )
        self._set_confirm_only()
        return self.async_show_form(
            step_id="bluetooth_confirm", description_placeholders={"name": self._title}
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            address = user_input[CONF_ADDRESS]
            await self.async_set_unique_id(address, raise_on_progress=False)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._discovered[address], data={CONF_ADDRESS: address}
            )

        configured = self._async_current_ids(include_ignore=False)
        for info in async_discovered_service_info(self.hass, connectable=True):
            if info.address in configured or info.address in self._discovered:
                continue
            if (title := _airtap_title(info)) is not None:
                self._discovered[info.address] = title
        if not self._discovered:
            return self.async_abort(reason="no_devices_found")
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): vol.In(
                        {addr: f"{title} ({addr})" for addr, title in self._discovered.items()}
                    )
                }
            ),
        )


class AirtapOptionsFlow(OptionsFlow):
    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = self.config_entry.options.get(
            CONF_REFRESH_MINUTES, DEFAULT_REFRESH_MINUTES
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_REFRESH_MINUTES, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=0, max=1440)
                    )
                }
            ),
        )
