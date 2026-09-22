"""Config flow for the G.hn Powerline integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from . import create_session
from .api import GhnAuthError, GhnBusyError, GhnClient, GhnConnectionError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import GhnConfigEntry

_PASSWORD_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR,
    }
)
STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR})


async def _probe(hass: HomeAssistant, host: str, password: str) -> tuple[str, str]:
    """Log in and return (MAC, model). Raises the client's errors."""
    session = create_session(hass, auto_cleanup=False)
    try:
        values = await GhnClient(session, host, password).async_get(
            ["SYSTEM.PRODUCTION.MAC_ADDR", "SYSTEM.PRODUCTION.DEVICE_NAME"]
        )
    finally:
        session.detach()
    mac = values.get("SYSTEM.PRODUCTION.MAC_ADDR")
    if not mac:
        raise GhnConnectionError(f"{host}: no MAC address in the response")
    return format_mac(mac), values.get("SYSTEM.PRODUCTION.DEVICE_NAME") or "G.hn adapter"


async def _probe_errors(
    hass: HomeAssistant, host: str, password: str
) -> tuple[tuple[str, str] | None, dict[str, str]]:
    try:
        return await _probe(hass, host, password), {}
    except GhnBusyError:
        return None, {"base": "busy"}
    except GhnAuthError:
        return None, {"base": "invalid_auth"}
    except GhnConnectionError:
        return None, {"base": "cannot_connect"}
    except Exception:
        LOGGER.exception("Unexpected error talking to %s", host)
        return None, {"base": "unknown"}


class GhnConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: GhnConfigEntry) -> GhnOptionsFlow:
        """Return the options flow."""
        return GhnOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add an adapter by host and password."""
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await _probe_errors(
                self.hass, user_input[CONF_HOST], user_input[CONF_PASSWORD]
            )
            if info is not None:
                mac, model = info
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured(updates={CONF_HOST: user_input[CONF_HOST]})
                return self.async_create_entry(title=f"{model} {mac[-8:]}", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(STEP_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the adapter rejected the password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await _probe_errors(
                self.hass, entry.data[CONF_HOST], user_input[CONF_PASSWORD]
            )
            if info is not None:
                await self.async_set_unique_id(info[0])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"host": entry.data[CONF_HOST]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the host or password of an existing adapter."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            info, errors = await _probe_errors(
                self.hass, user_input[CONF_HOST], user_input[CONF_PASSWORD]
            )
            if info is not None:
                await self.async_set_unique_id(info[0])
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input or {CONF_HOST: entry.data[CONF_HOST]}
            ),
            errors=errors,
        )


class GhnOptionsFlow(OptionsFlow):
    """Handle options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Set the polling interval."""
        if user_input is not None:
            return self.async_create_entry(
                data={CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL])}
            )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=self.config_entry.options.get(
                        CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                    ),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL,
                        max=MAX_SCAN_INTERVAL,
                        step=10,
                        unit_of_measurement="s",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
