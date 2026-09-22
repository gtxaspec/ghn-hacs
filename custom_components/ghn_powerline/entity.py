"""Base entity for the G.hn Powerline integration."""

from __future__ import annotations

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import GhnCoordinator


class GhnEntity(CoordinatorEntity[GhnCoordinator]):
    """Base class for G.hn Powerline entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GhnCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.unique_id}_{key}"
        self._attr_device_info = coordinator.device_info
