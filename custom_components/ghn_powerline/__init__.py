"""The G.hn Powerline integration."""

from __future__ import annotations

import aiohttp

from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import GhnClient
from .coordinator import GhnConfigEntry, GhnCoordinator, GhnRuntimeData

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]


def create_session(hass: HomeAssistant, *, auto_cleanup: bool = True) -> aiohttp.ClientSession:
    """Create a session whose cookie jar accepts cookies from IP-address hosts.

    Home Assistant sessions share one connector, so they are detached, never closed. With
    auto_cleanup, a session made during entry setup is detached when the entry unloads;
    otherwise the caller must call detach().
    """
    return async_create_clientsession(
        hass, auto_cleanup=auto_cleanup, cookie_jar=aiohttp.CookieJar(unsafe=True)
    )


async def async_setup_entry(hass: HomeAssistant, entry: GhnConfigEntry) -> bool:
    """Set up an adapter from a config entry."""
    session = create_session(hass)
    client = GhnClient(session, entry.data[CONF_HOST], entry.data[CONF_PASSWORD])
    coordinator = GhnCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = GhnRuntimeData(client=client, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: GhnConfigEntry) -> None:
    """Reload when the options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: GhnConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
