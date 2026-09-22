"""Config flow tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.ghn_powerline.api import GhnAuthError, GhnBusyError, GhnConnectionError
from custom_components.ghn_powerline.const import DOMAIN

from .conftest import MAC

PROBE = "custom_components.ghn_powerline.config_flow.GhnClient.async_get"
SETUP = "custom_components.ghn_powerline.async_setup_entry"
USER_INPUT = {CONF_HOST: "192.0.2.10", CONF_PASSWORD: "secret"}


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    with (
        patch(
            PROBE,
            return_value={
                "SYSTEM.PRODUCTION.MAC_ADDR": MAC.upper(),
                "SYSTEM.PRODUCTION.DEVICE_NAME": "PLA6456",
            },
        ),
        patch(SETUP, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "PLA6456 00:00:01"
    assert result["data"] == USER_INPUT
    assert result["result"].unique_id == MAC


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (GhnBusyError("busy"), "busy"),
        (GhnAuthError("403"), "invalid_auth"),
        (GhnConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(hass: HomeAssistant, error: Exception, reason: str) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    with patch(PROBE, side_effect=error):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": reason}
