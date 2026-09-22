"""Polling coordinator for G.hn Powerline adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    GhnAuthError,
    GhnBusyError,
    GhnClient,
    GhnConnectionError,
    GhnPeer,
    parse_counters,
    parse_peers,
    parse_uptime,
)
from .const import (
    BUSY_GRACE_SECONDS,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LOGGER,
    POLL_KEYS,
)


@dataclass
class GhnRuntimeData:
    """Runtime data stored on the config entry."""

    client: GhnClient
    coordinator: GhnCoordinator


type GhnConfigEntry = ConfigEntry[GhnRuntimeData]


def _float(value: str | None) -> float | None:
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None


@dataclass
class GhnData:
    """One poll's worth of adapter state."""

    values: dict[str, str | None]
    peers: dict[str, GhnPeer]
    boot_time: datetime | None
    eth_stats: dict[str, int]
    """ETHIFDRIVER.STATS.INFO counters by field name, e.g. "ETHB Rx bytes"."""
    labels: dict[str, str]
    """Friendly names for MACs: the adapter's own title, or a peer's config entry title."""

    def get(self, key: str) -> str | None:
        """Return a raw value."""
        return self.values.get(key)

    def label(self, mac: str | None) -> str | None:
        """Return the friendly name for a MAC, or the MAC itself."""
        if not mac:
            return None
        mac = format_mac(mac)
        return self.labels.get(mac, mac)

    def int_value(self, key: str) -> int | None:
        """Return a value as an int."""
        value = self.values.get(key)
        try:
            return int(value) if value is not None else None
        except ValueError:
            return None

    def flag(self, key: str) -> bool | None:
        """Return a YES/NO value as a bool."""
        value = self.values.get(key)
        return None if value is None else value == "YES"

    @property
    def mac(self) -> str | None:
        """Return the adapter's MAC address."""
        mac = self.values.get("SYSTEM.PRODUCTION.MAC_ADDR")
        return format_mac(mac) if mac else None

    @property
    def temperature(self) -> float | None:
        """Chip temperature in °C; the firmware reports hundredths of a degree."""
        raw = _float(self.values.get("TEMPSENSORS.GENERAL.MEASURE"))
        return None if raw is None else round(raw / 100, 1)

    @property
    def memory_usage(self) -> float | None:
        """Used memory, percent."""
        free = _float(self.values.get("SYSTEM.STATS.FREE_MEMORY"))
        total = _float(self.values.get("SYSTEM.STATS.TOTAL_MEMORY"))
        if free is None or not total:
            return None
        return round((total - free) / total * 100, 1)

    @property
    def notch_count(self) -> int:
        """Number of user notches (the list is flat start,stop,depth triples)."""
        raw = self.values.get("POWERMASK.USER.NOTCHES")
        if not raw or raw == "0":
            return 0
        return len([part for part in raw.split(",") if part.strip()]) // 3


def build_data(
    values: dict[str, str | None],
    previous: GhnData | None,
    labels: dict[str, str] | None = None,
) -> GhnData:
    """Turn raw values into GhnData."""
    peers = parse_peers(values, values.get("SYSTEM.PRODUCTION.MAC_ADDR"))
    uptime = parse_uptime(values.get("SYSTEM.GENERAL.UPTIME"))
    boot_time = None
    if uptime is not None:
        boot_time = (dt_util.utcnow() - timedelta(seconds=uptime)).replace(microsecond=0)
        # now - uptime moves by a second or two between polls; only a reboot should change it.
        if (
            previous is not None
            and previous.boot_time is not None
            and abs((boot_time - previous.boot_time).total_seconds()) < 60
        ):
            boot_time = previous.boot_time
    return GhnData(
        values=values,
        peers=peers,
        boot_time=boot_time,
        eth_stats=parse_counters(values, "ETHIFDRIVER.STATS.INFO"),
        labels=labels or {},
    )


class GhnCoordinator(DataUpdateCoordinator[GhnData]):
    """Poll one adapter."""

    config_entry: GhnConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GhnConfigEntry, client: GhnClient) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_HOST]}",
            update_interval=timedelta(
                seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.client = client
        self._busy_since: datetime | None = None

    async def _async_update_data(self) -> GhnData:
        try:
            values = await self.client.async_get(POLL_KEYS)
        except GhnBusyError as err:
            now = dt_util.utcnow()
            self._busy_since = self._busy_since or now
            # Someone is logged into the adapter's web UI. Keep the last values for a while
            # rather than flapping every entity to unavailable.
            if (
                self.data is not None
                and (now - self._busy_since).total_seconds() < BUSY_GRACE_SECONDS
            ):
                LOGGER.debug("%s: web session in use, keeping previous data", self.client.host)
                return self.data
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="busy") from err
        except GhnAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except GhnConnectionError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
        self._busy_since = None
        return build_data(values, self.data, self._labels())

    @callback
    def async_update_via_device(self) -> None:
        """Show this adapter as connected via the domain master, like breakers under a hub.

        Only links to a master that is another configured adapter whose device already
        exists: pointing via_device at an unregistered device is an error in current Home
        Assistant. Runs after every poll, so it follows a master failover and fills in once
        the master's entry is added, in either order.
        """
        own = self.config_entry.unique_id
        if self.data is None or not own:
            return
        registry = dr.async_get(self.hass)
        device = registry.async_get_device(identifiers={(DOMAIN, own)})
        if device is None:
            return
        via_id = None
        master = self.data.get("NODE.GENERAL.DOMAIN_MASTER_MAC_ADDR")
        if master and (master_mac := format_mac(master)) != own:
            master_device = registry.async_get_device(identifiers={(DOMAIN, master_mac)})
            if master_device is not None:
                via_id = master_device.id
        if device.via_device_id != via_id:
            registry.async_update_device(device.id, via_device_id=via_id)

    def _labels(self) -> dict[str, str]:
        """Map every configured adapter's MAC to its entry title, this one included.

        Recomputed on each poll so renames and newly added adapters show up without a reload.
        """
        return {
            entry.unique_id: entry.title
            for entry in self.hass.config_entries.async_entries(DOMAIN)
            if entry.unique_id
        }

    @property
    def device_info(self) -> DeviceInfo:
        """Device registry entry for this adapter."""
        data = self.data
        mac = self.config_entry.unique_id or (data.mac if data else None) or ""
        return DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)} if mac else set(),
            name=self.config_entry.title,
            manufacturer=data.get("SYSTEM.PRODUCTION.DEVICE_MANUFACTURER") if data else None,
            model=data.get("SYSTEM.PRODUCTION.DEVICE_NAME") if data else None,
            hw_version=data.get("SYSTEM.PRODUCTION.HW_REVISION") if data else None,
            sw_version=(
                data.get("SYSTEM.GENERAL.FW_VERSION_ALIAS")
                or data.get("SYSTEM.GENERAL.FW_VERSION")
            )
            if data
            else None,
            serial_number=data.get("SYSTEM.PRODUCTION.SERIAL_NUMBER") if data else None,
            configuration_url=f"http://{self.config_entry.data[CONF_HOST]}/",
        )
