"""Buttons for G.hn Powerline adapters."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GhnAuthError, GhnBusyError, GhnConnectionError
from .const import DOMAIN
from .coordinator import GhnConfigEntry, GhnCoordinator
from .entity import GhnEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: GhnConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities([GhnRestartButton(entry.runtime_data.coordinator)])


class GhnRestartButton(GhnEntity, ButtonEntity):
    """Reboot the adapter. Its powerline links drop for about 30 seconds."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: GhnCoordinator) -> None:
        """Initialize the button."""
        super().__init__(coordinator, "restart")

    async def async_press(self) -> None:
        """Reboot."""
        try:
            await self.coordinator.client.async_reboot()
        except GhnBusyError as err:
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="busy") from err
        except GhnAuthError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except GhnConnectionError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err)},
            ) from err
