"""Sensors for G.hn Powerline adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
)
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


def _eth_counter(port: str, field: str) -> Callable[[GhnData], StateType]:
    return lambda data: data.eth_stats.get(f"{port} {field}")


def _key(key: str) -> Callable[[GhnData], StateType]:
    return lambda data: data.get(key)


def _int_key(key: str) -> Callable[[GhnData], StateType]:
    return lambda data: data.int_value(key)


def _chipset(data: GhnData) -> str | None:
    asic, name = data.get("SYSTEM.GENERAL.ASIC"), data.get("SYSTEM.GENERAL.CHIPSET")
    if asic and name:
        return f"{asic} ({name})"
    return asic or name


def _linked_peers(data: GhnData) -> str:
    names = sorted(data.label(mac) or mac for mac, peer in data.peers.items() if peer.active)
    return ", ".join(names)[:255] if names else "None"


def _linked_peers_attrs(data: GhnData) -> dict[str, Any]:
    return {
        "peers": [
            {
                "name": data.label(mac),
                "mac": mac,
                "tx_rate": peer.tx_rate,
                "rx_rate": peer.rx_rate,
            }
            for mac, peer in sorted(data.peers.items())
            if peer.active
        ]
    }


@dataclass(frozen=True, kw_only=True)
class GhnSensorDescription(SensorEntityDescription):
    """Describe a device-level sensor."""

    value_fn: Callable[[GhnData], StateType | datetime]
    exists_fn: Callable[[GhnData], bool] = lambda _data: True
    attrs_fn: Callable[[GhnData], dict[str, Any]] | None = None


def _port_sensors(port: str) -> tuple[GhnSensorDescription, ...]:
    """Ethernet speed, traffic, errors and link changes for one port."""
    prefix = port.lower()
    exists = _port_enabled(port)
    traffic = [
        GhnSensorDescription(
            key=f"{prefix}_{direction}_bytes",
            translation_key=f"{prefix}_{direction}_bytes",
            device_class=SensorDeviceClass.DATA_SIZE,
            native_unit_of_measurement=UnitOfInformation.BYTES,
            suggested_unit_of_measurement=UnitOfInformation.GIGABYTES,
            suggested_display_precision=2,
            # The firmware counters are 32-bit and wrap; total_increasing treats that as a reset.
            state_class=SensorStateClass.TOTAL_INCREASING,
            value_fn=_eth_counter(port, f"{field} bytes"),
            exists_fn=exists,
        )
        for direction, field in (("tx", "Tx"), ("rx", "Rx"))
    ]
    errors = [
        GhnSensorDescription(
            key=f"{prefix}_{direction}_errors",
            translation_key=f"{prefix}_{direction}_errors",
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_eth_counter(port, f"{field} errors"),
            exists_fn=exists,
        )
        for direction, field in (("tx", "Tx"), ("rx", "Rx"))
    ]
    return (
        GhnSensorDescription(
            key=f"{prefix}_speed",
            translation_key=f"{prefix}_speed",
            device_class=SensorDeviceClass.DATA_RATE,
            native_unit_of_measurement=UnitOfDataRate.MEGABITS_PER_SECOND,
            suggested_display_precision=0,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_eth_speed(port),
            exists_fn=exists,
        ),
        GhnSensorDescription(
            key=f"{prefix}_link_changes",
            translation_key=f"{prefix}_link_changes",
            state_class=SensorStateClass.TOTAL_INCREASING,
            entity_category=EntityCategory.DIAGNOSTIC,
            value_fn=_int_key(f"ETHPHYCONF.{port}.LINK_CHANGES"),
            exists_fn=exists,
        ),
        *traffic,
        *errors,
    )


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
        key="linked_peers",
        translation_key="linked_peers",
        value_fn=_linked_peers,
        attrs_fn=_linked_peers_attrs,
    ),
    GhnSensorDescription(
        key="domain_master",
        translation_key="domain_master",
        value_fn=lambda data: data.label(data.get("NODE.GENERAL.DOMAIN_MASTER_MAC_ADDR")),
    ),
    GhnSensorDescription(
        key="firmware",
        translation_key="firmware",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_key("SYSTEM.GENERAL.FW_VERSION"),
    ),
    GhnSensorDescription(
        key="chipset",
        translation_key="chipset",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_chipset,
    ),
    GhnSensorDescription(
        key="ip_address",
        translation_key="ip_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_key("TCPIP.IPV4.IP_ADDRESS"),
    ),
    # Like a Wi-Fi network name: not the secret (the pairing password is), but no need to
    # show it everywhere either.
    GhnSensorDescription(
        key="domain_name",
        translation_key="domain_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=_key("NODE.GENERAL.DOMAIN_NAME"),
    ),
    GhnSensorDescription(
        key="domain_nodes",
        translation_key="domain_nodes",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_int_key("MASTERSELECTION.DOMAIN.NUM_NODES"),
    ),
    GhnSensorDescription(
        key="master_lost",
        translation_key="master_lost",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_int_key("MASTERSELECTION.DOMAIN.MASTER_LOST"),
    ),
    GhnSensorDescription(
        key="lost_maps",
        translation_key="lost_maps",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_int_key("MASTERSELECTION.DOMAIN.LOST_MAPS"),
    ),
    GhnSensorDescription(
        key="registrations",
        translation_key="registrations",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_int_key("MASTERSELECTION.DOMAIN.REGISTRATIONS"),
    ),
    GhnSensorDescription(
        key="dereg_cause",
        translation_key="dereg_cause",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_key("PHYMNG.DOMAIN.DEREG_CAUSE"),
    ),
    GhnSensorDescription(
        key="linkdown_cause",
        translation_key="linkdown_cause",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_key("PHYMNG.DOMAIN.LINKDOWN_CAUSE"),
    ),
    GhnSensorDescription(
        key="visible_domains",
        translation_key="visible_domains",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_int_key("NDIM.GENERAL.N_VISIBLE_DOMAINS"),
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
    *(description for port in ETH_PORTS for description in _port_sensors(port)),
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

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes, if the description defines any."""
        if self.entity_description.attrs_fn is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


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
