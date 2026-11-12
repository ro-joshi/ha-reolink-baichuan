from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from reolink_aio.api import Host
import voluptuous as vol

from .const import DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME, default="admin"): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
    }
)


class ReolinkBaichuanConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Reolink Baichuan Native Camera."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = Host(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                port=user_input[CONF_PORT],
            )
            try:
                await host.baichuan.login()
                await host.get_host_data()
                title = f"{host.model or 'Reolink'} ({user_input[CONF_HOST]})"
                unique_id = host.mac_address or user_input[CONF_HOST]
            except Exception as err:
                _LOGGER.error("Baichuan connection failed: %s", err)
                errors["base"] = "cannot_connect"
            finally:
                try:
                    await host.logout()
                except Exception:
                    pass

            if not errors:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
