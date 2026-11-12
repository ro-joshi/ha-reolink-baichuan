from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from reolink_aio.api import Host

from .bridge import BaichuanTcpBridge

PLATFORMS: list[Platform] = [Platform.CAMERA]


@dataclass
class ReolinkBaichuanRuntimeData:
    """Runtime state for a configured Reolink Baichuan camera."""

    host: Host
    bridges: list[BaichuanTcpBridge]


type ReolinkBaichuanConfigEntry = ConfigEntry[ReolinkBaichuanRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: ReolinkBaichuanConfigEntry) -> bool:
    """Set up Reolink Baichuan camera from a config entry."""
    host = Host(
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        port=entry.data[CONF_PORT],
    )
    try:
        await host.baichuan.login()
        await host.get_host_data()
    except Exception as err:
        await host.logout()
        raise ConfigEntryNotReady(f"Unable to connect to {entry.data[CONF_HOST]}") from err

    entry.runtime_data = ReolinkBaichuanRuntimeData(host=host, bridges=[])
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ReolinkBaichuanConfigEntry) -> bool:
    """Unload a config entry and stop active TCP bridges."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        for bridge in entry.runtime_data.bridges:
            await bridge.stop()
        await entry.runtime_data.host.logout()
    return unload_ok
