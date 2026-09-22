"""Setup, entity and coordinator tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ghn_powerline.api import GhnBusyError
from custom_components.ghn_powerline.const import DOMAIN

from .conftest import MAC, PEER, POLL_VALUES

GET = "custom_components.ghn_powerline.api.GhnClient.async_get"


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Rack",
        unique_id=MAC,
        data={CONF_HOST: "192.0.2.10", CONF_PASSWORD: "secret"},
    )


def _state(hass: HomeAssistant, platform: str, key: str):
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{MAC}_{key}")
    assert entity_id, f"no {platform} entity for {key}"
    return hass.states.get(entity_id)


async def test_setup_creates_entities(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(GET, return_value=dict(POLL_VALUES)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    assert _state(hass, "sensor", "temperature").state == "71.3"
    assert _state(hass, "sensor", "role").state == "domain_master"
    assert _state(hass, "sensor", "connected_peers").state == "1"
    assert _state(hass, "sensor", "cpu_usage").state == "21"
    assert _state(hass, "sensor", "profile").state == "PLC 100 MHz MIMO Boost"
    assert _state(hass, "sensor", "ethb_speed").state == "1000"
    assert float(_state(hass, "sensor", f"peer_{PEER}_tx_rate").state) == 189.088
    assert float(_state(hass, "sensor", f"peer_{PEER}_rx_rate").state) == 190.592
    assert _state(hass, "binary_sensor", "encryption").state == STATE_ON
    assert _state(hass, "binary_sensor", "ethb_link").state == STATE_ON

    registry = er.async_get(hass)
    # ETHA is disabled on this adapter, so it gets no entities.
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{MAC}_etha_link") is None
    peer_entry = registry.async_get(
        registry.async_get_entity_id("sensor", DOMAIN, f"{MAC}_peer_{PEER}_tx_rate")
    )
    assert peer_entry.disabled_by is None

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_busy_poll_keeps_previous_values(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(GET, return_value=dict(POLL_VALUES)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    with patch(GET, side_effect=GhnBusyError("busy")):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await hass.async_block_till_done()

    assert _state(hass, "sensor", "temperature").state == "71.3"
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_new_peer_gets_entities(hass: HomeAssistant) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    with patch(GET, return_value=dict(POLL_VALUES)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    new_peer = "02:00:00:00:00:03"
    updated = dict(POLL_VALUES)
    updated["DIDMNG.GENERAL.DIDS"] = "0,1,2,3"
    updated["DIDMNG.GENERAL.MACS"] += f",{new_peer}"
    updated["DIDMNG.GENERAL.ACTIVE"] += ",YES"
    updated["DIDMNG.GENERAL.TX_BPS"] += ",1000"
    updated["DIDMNG.GENERAL.RX_BPS"] += ",2000"
    updated["DIDMNG.GENERAL.AVG_ATTENUATION"] += ",400"
    updated["DIDMNG.GENERAL.WIRE_LENGTH"] += ",20"
    with patch(GET, return_value=updated):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
        await hass.async_block_till_done()

    assert float(_state(hass, "sensor", f"peer_{new_peer}_tx_rate").state) == 32.0
    assert _state(hass, "sensor", "connected_peers").state == "2"
    assert await hass.config_entries.async_unload(entry.entry_id)
