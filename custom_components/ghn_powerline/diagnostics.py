"""Diagnostics for the G.hn Powerline integration.

Includes a fresh read of every key the adapter exposes, which is the whole reason to use
this over the entities.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import REDACTED, async_redact_data

from .api import GhnError
from .const import TO_REDACT, TO_REDACT_SUBSTRINGS
from .coordinator import GhnConfigEntry


def _redact_values(values: dict[str, str | None]) -> dict[str, str | None]:
    return {
        key: REDACTED
        if key in TO_REDACT or any(part in key for part in TO_REDACT_SUBSTRINGS)
        else value
        for key, value in values.items()
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: GhnConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    data = runtime.coordinator.data
    try:
        all_keys: dict[str, Any] = _redact_values(await runtime.client.async_get_all())
    except GhnError as err:
        all_keys = {"error": f"{type(err).__name__}: {err}"}

    return {
        "entry": {
            "title": entry.title,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "polled": _redact_values(data.values) if data else None,
        "peers": [asdict(peer) for peer in data.peers.values()] if data else [],
        "boot_time": data.boot_time.isoformat() if data and data.boot_time else None,
        "all_keys": all_keys,
    }
