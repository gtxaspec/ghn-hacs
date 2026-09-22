"""Tests for the Home Assistant-free parsing in api.py (run with: python -m pytest tests)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_API = Path(__file__).resolve().parent.parent / "custom_components" / "ghn_powerline" / "api.py"
_spec = importlib.util.spec_from_file_location("ghn_api", _API)
api = importlib.util.module_from_spec(_spec)
# dataclasses resolves string annotations through sys.modules, so register before executing.
sys.modules[_spec.name] = api
_spec.loader.exec_module(api)

SAMPLE = (
    "<!--\n    <legal_notice>\n     Some vendor notice.\n    </legal_notice>\n-->\n\n"
    "TEMPSENSORS.GENERAL.MEASURE=6785\n"
    "SYSTEM.GENERAL.UPTIME=1 days, 2h 3m 4s\n"
    "FLUPGRADE.GENERAL.INFO=- fw: r1\n- config: r1\n"
    "NO.SUCH.KEY=Not found\n"
    "NODE.GENERAL.NODE_TYPE=END_POINT\n\n" + "\r" * 50
)


def test_parse_values() -> None:
    values = api.parse_values(SAMPLE)
    assert values["TEMPSENSORS.GENERAL.MEASURE"] == "6785"
    assert values["SYSTEM.GENERAL.UPTIME"] == "1 days, 2h 3m 4s"
    assert values["FLUPGRADE.GENERAL.INFO"] == "- fw: r1\n- config: r1"
    assert values["NO.SUCH.KEY"] is None
    assert values["NODE.GENERAL.NODE_TYPE"] == "END_POINT"
    assert "legal_notice" not in "".join(values)


def test_parse_values_ignores_non_values() -> None:
    assert api.parse_values("<html><body>Password: <input name='.PASSWORD'></body></html>") == {}


def test_parse_uptime() -> None:
    assert api.parse_uptime("1 days, 2h 3m 4s") == 93784
    assert api.parse_uptime("0 days, 0h 0m 21s") == 21
    assert api.parse_uptime("garbage") is None
    assert api.parse_uptime(None) is None


def test_parse_peers() -> None:
    values = {
        "DIDMNG.GENERAL.MACS": "00:00:00:00:00:00,02:00:00:00:00:01,02:00:00:00:00:02,02:00:00:00:00:03",
        "DIDMNG.GENERAL.DIDS": "0,1,2,3",
        "DIDMNG.GENERAL.ACTIVE": "NO,YES,YES,NO",
        "DIDMNG.GENERAL.TX_BPS": "0,0,5909,0",
        "DIDMNG.GENERAL.RX_BPS": "0,0,5956,0",
        "DIDMNG.GENERAL.AVG_ATTENUATION": "0,0,527,2000",
        "DIDMNG.GENERAL.WIRE_LENGTH": "0,0,72,0",
    }
    peers = api.parse_peers(values, own_mac="02:00:00:00:00:01")
    assert set(peers) == {"02:00:00:00:00:02", "02:00:00:00:00:03"}
    peer = peers["02:00:00:00:00:02"]
    assert peer.active is True
    assert peer.device_id == 2
    assert round(peer.tx_rate, 3) == 189.088
    assert round(peer.rx_rate, 3) == 190.592
    assert peer.attenuation == 527
    assert peer.wire_length == 72
    assert peers["02:00:00:00:00:03"].active is False


def test_parse_peers_tolerates_short_arrays() -> None:
    peers = api.parse_peers(
        {"DIDMNG.GENERAL.MACS": "02:00:00:00:00:02", "DIDMNG.GENERAL.ACTIVE": ""}, own_mac=None
    )
    peer = peers["02:00:00:00:00:02"]
    assert peer.active is False
    assert peer.tx_rate is None
    assert peer.device_id is None
