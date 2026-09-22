"""Sensors for G.hn Powerline adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfDataRate, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.typing import StateType

from .api import GhnPeer
from .const import DOMAIN, ETH_PORTS, PROFILE_NAMES
from .coordinator import GhnConfigEntry, GhnCoordinator, GhnData
from .entity import GhnEntity

PARALLEL_UPDATES = 0

ROLE_OPTIONS = ["domain_master", "end_point"]


def _int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None


def _role(data: GhnData) -> str | None:
    value = (data.get("NODE.GENERAL.NODE_TYPE") or "").lower()
    return value if value in ROLE_OPTIONS else None


def _profile(data: GhnData) -> str | None:
    mode = _int(data.get("PHYMNG.GENERAL.RUNNING_PHYMODE_ID"))
    if mode is None:
        return None
    return PROFILE_NAMES.get(mode, f"Profile {mode}")


def _eth_speed(port: str) -> Callable[[GhnData], StateType]:
    def value(data: GhnData) -> StateType:
        # The firmware keeps reporting the last negotiated speed after the link drops.
        if not data.flag(f"ETHPHYCONF.{port}.LINK"):
            return None
        return _int(data.get(f"ETHPHYCONF.{port}.SPEED"))

    return value


def _port_enabled(port: str) -> Callable[[GhnData], bool]:
    return lambda data: bool(data.flag(f"ETHIFDRIVER.{port}.ENABLED"))


@dataclass(frozen=True, kw_only=True)
class GhnSensorDescription(SensorEntityDescription):
    """Describe a device-level sensor."""

    value_fn: Callable[[GhnData], StateType | datetime]
    exists_fn: Callable[[GhnData], bool] = lambda _data: True


SENSORS: tuple[GhnSensorDescription, ...] = (
    GhnSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.temperature,
    ),
    GhnSensorDescription(
        key="role",
        translation_key="role",
        device_class=SensorDeviceClass.ENUM,
        options=ROLE_OPTIONS,
        value_fn=_role,
    ),
    GhnSensorDescription(
        key="connected_peers",
        translation_key="connected_peers",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: sum(1 for peer in data.peers.values() if peer.active),
    ),
    GhnSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: _int(data.get("SYSTEM.STATS.CPU_USAGE")),
    ),
    GhnSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.memory_usage,
    ),
    GhnSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.boot_time,
    ),
    GhnSensorDescription(
        key="profile",
        translation_key="profile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_profile,
    ),
    GhnSensorDescription(
        key="user_notches",
        translation_key="user_notches",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.notch_count,
    ),
    *(
        GhnSensorDescription(
            key=f"{port.lower()}_speed",
            translation_key=f"{port.lower()}_speed",
            device_class=SensorDeviceClass.DATA_RATE,
            native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
            suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_eth_speed(port),
            exists_fn=_port_enabled(port),
        )
        for port in ETH_PORTS
    ),
)


@dataclass(frozen=True, kw_only=True)
class GhnPeerSensorDescription(SensorEntityDescription):
    """Describe a sensor for one remote node."""

    value_fn: Callable[[GhnPeer], StateType]


PEER_SENSORS: tuple[GhnPeerSensorDescription, ...] = (
    GhnPeerSensorDescription(
        key="tx_rate",
        translation_key="peer_tx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda peer: peer.tx_rate,
    ),
    GhnPeerSensorDescription(
        key="rx_rate",
        translation_key="peer_rx_rate",
        device_class=SensorDeviceClass.DATA_RATE,
        native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda peer: peer.rx_rate,
    ),
    # Raw firmware numbers: the web UI never shows them, so there is no known unit.
    GhnPeerSensorDescription(
        key="attenuation",
        translation_key="peer_attenuation",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda peer: peer.attenuation,
    ),
    GhnPeerSensorDescription(
        key="wire_length",
        translation_key="peer_wire_length",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda peer: peer.wire_length,
    ),
)


def _peer_label(hass: HomeAssistant, mac: str) -> str:
    """Name a peer after its own config entry when that adapter is also set up."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.unique_id == format_mac(mac):
            return entry.title
    return mac


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GhnConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors, adding peer sensors as new peers appear."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        GhnSensor(coordinator, description)
        for description in SENSORS
        if description.exists_fn(coordinator.data)
    )

    known: set[str] = set()

    @callback
    def _add_new_peers() -> None:
        if coordinator.data is None:
            return
        new = [mac for mac in coordinator.data.peers if mac not in known]
        if not new:
            return
        known.update(new)
        async_add_entities(
            GhnPeerSensor(coordinator, description, mac, _peer_label(hass, mac))
            for mac in new
            for description in PEER_SENSORS
        )

    _add_new_peers()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_peers))


class GhnSensor(GhnEntity, SensorEntity):
    """A device-level sensor."""

    entity_description: GhnSensorDescription

    def __init__(self, coordinator: GhnCoordinator, description: GhnSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the value."""
        return self.entity_description.value_fn(self.coordinator.data)


class GhnPeerSensor(GhnEntity, SensorEntity):
    """A sensor for one remote node."""

    entity_description: GhnPeerSensorDescription

    def __init__(
        self,
        coordinator: GhnCoordinator,
        description: GhnPeerSensorDescription,
        peer_mac: str,
        peer_label: str,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, f"peer_{format_mac(peer_mac)}_{description.key}")
        self.entity_description = description
        self._peer_mac = peer_mac
        self._attr_translation_placeholders = {"peer": peer_label}

    @property
    def _peer(self) -> GhnPeer | None:
        return self.coordinator.data.peers.get(self._peer_mac)

    @property
    def available(self) -> bool:
        """Unavailable while the peer is not linked."""
        peer = self._peer
        return super().available and peer is not None and peer.active

    @property
    def native_value(self) -> StateType:
        """Return the value."""
        peer = self._peer
        return self.entity_description.value_fn(peer) if peer else None
