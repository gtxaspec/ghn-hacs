"""Binary sensors for G.hn Powerline adapters."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ETH_PORTS
from .coordinator import GhnConfigEntry, GhnCoordinator, GhnData
from .entity import GhnEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class GhnBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a binary sensor."""

    value_fn: Callable[[GhnData], bool | None]
    exists_fn: Callable[[GhnData], bool] = lambda _data: True


def _flag(key: str) -> Callable[[GhnData], bool | None]:
    return lambda data: data.flag(key)


BINARY_SENSORS: tuple[GhnBinarySensorDescription, ...] = (
    GhnBinarySensorDescription(
        key="encryption",
        translation_key="encryption",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_flag("PAIRING.GENERAL.SECURED"),
    ),
    *(
        GhnBinarySensorDescription(
            key=f"{port.lower()}_link",
            translation_key=f"{port.lower()}_link",
            device_class=BinarySensorDeviceClass.CONNECTIVITY,
            value_fn=_flag(f"ETHPHYCONF.{port}.LINK"),
            exists_fn=lambda data, port=port: bool(data.flag(f"ETHIFDRIVER.{port}.ENABLED")),
        )
        for port in ETH_PORTS
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GhnConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        GhnBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
        if description.exists_fn(coordinator.data)
    )


class GhnBinarySensor(GhnEntity, BinarySensorEntity):
    """A G.hn binary sensor."""

    entity_description: GhnBinarySensorDescription

    def __init__(
        self, coordinator: GhnCoordinator, description: GhnBinarySensorDescription
    ) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the state."""
        return self.entity_description.value_fn(self.coordinator.data)
