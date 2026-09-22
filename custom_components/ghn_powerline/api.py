"""Async client for the web API of MaxLinear G.hn powerline adapters.

MaxLinear-based adapters (Zyxel PLA6456, Comtrend PG-9182 and others) expose their whole
configuration and status as KEY=VALUE text: GET /getall.html returns every key,
GET /get.html?KEY&KEY returns the listed keys, and POST /get.html writes KEY=VALUE pairs.
The web server allows ONE session at a time, so every call logs in, does its work and
logs out again, and calls to the same adapter are serialized.

This module has no Home Assistant dependency so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

import aiohttp

_KV_RE = re.compile(r"^([A-Z][A-Z0-9_]*(?:\.[A-Z0-9_]+)+)=(.*)$")
_TAG_RE = re.compile(r"<[^>]*>")
_UPTIME_RE = re.compile(r"(\d+)\s*days?,\s*(\d+)h\s*(\d+)m\s*(\d+)s")
_BUSY_MARKER = "active session"
_NULL_MAC = "00:00:00:00:00:00"

# The web UI shows peer PHY rates as Math.floor(value * 32 / 1000) Mbps.
_RATE_UNIT_KBPS = 32


class GhnError(Exception):
    """Base error for the G.hn client."""


class GhnConnectionError(GhnError):
    """The adapter could not be reached or answered with an error."""


class GhnAuthError(GhnError):
    """The adapter rejected the password."""


class GhnBusyError(GhnError):
    """Another web session is logged in, and the adapter allows only one."""


@dataclass(frozen=True, slots=True)
class GhnPeer:
    """One remote node as seen by this adapter (DIDMNG.GENERAL.* arrays)."""

    mac: str
    device_id: int | None
    active: bool
    tx_rate: float | None
    """PHY rate from this adapter to the peer, Mbps."""
    rx_rate: float | None
    """PHY rate from the peer to this adapter, Mbps."""
    attenuation: int | None
    """Raw DIDMNG average attenuation; the firmware does not document its unit."""
    wire_length: int | None
    """Raw DIDMNG estimated wire length; the firmware does not document its unit."""


def parse_values(text: str) -> dict[str, str | None]:
    """Parse a get.html/getall.html body into a dict.

    The body is an HTML comment (a legal notice), then one KEY=VALUE per line, then a run of
    carriage returns. Some values span several lines; those lines are joined with newlines.
    Keys the firmware does not know come back as "Not found" and map to None.
    """
    if "-->" in text:
        text = text.split("-->", 1)[1]
    values: dict[str, str | None] = {}
    current: str | None = None
    for raw_line in _TAG_RE.sub("", text).split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        match = _KV_RE.match(line)
        if match:
            current = match.group(1)
            value = match.group(2).strip()
            values[current] = None if value == "Not found" else value
        elif current is not None and values.get(current) is not None:
            values[current] = f"{values[current]}\n{line}"
    return values


def parse_uptime(value: str | None) -> int | None:
    """Convert "1 days, 2h 3m 4s" to seconds."""
    if not value:
        return None
    match = _UPTIME_RE.search(value)
    if not match:
        return None
    days, hours, minutes, seconds = (int(part) for part in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


def _split(values: Mapping[str, str | None], key: str) -> list[str]:
    value = values.get(key)
    return [part.strip() for part in value.split(",")] if value else []


def _int_at(parts: list[str], index: int) -> int | None:
    try:
        return int(parts[index])
    except (IndexError, ValueError):
        return None


def parse_peers(values: Mapping[str, str | None], own_mac: str | None) -> dict[str, GhnPeer]:
    """Build the peer table from the aligned DIDMNG.GENERAL.* arrays, keyed by MAC."""
    macs = _split(values, "DIDMNG.GENERAL.MACS")
    dids = _split(values, "DIDMNG.GENERAL.DIDS")
    active = _split(values, "DIDMNG.GENERAL.ACTIVE")
    tx_bps = _split(values, "DIDMNG.GENERAL.TX_BPS")
    rx_bps = _split(values, "DIDMNG.GENERAL.RX_BPS")
    attenuation = _split(values, "DIDMNG.GENERAL.AVG_ATTENUATION")
    wire_length = _split(values, "DIDMNG.GENERAL.WIRE_LENGTH")
    own = own_mac.lower() if own_mac else None

    peers: dict[str, GhnPeer] = {}
    for index, raw_mac in enumerate(macs):
        mac = raw_mac.lower()
        if not mac or mac == _NULL_MAC or mac == own:
            continue
        tx = _int_at(tx_bps, index)
        rx = _int_at(rx_bps, index)
        peers[mac] = GhnPeer(
            mac=mac,
            device_id=_int_at(dids, index),
            active=index < len(active) and active[index] == "YES",
            tx_rate=None if tx is None else tx * _RATE_UNIT_KBPS / 1000,
            rx_rate=None if rx is None else rx * _RATE_UNIT_KBPS / 1000,
            attenuation=_int_at(attenuation, index),
            wire_length=_int_at(wire_length, index),
        )
    return peers


class GhnClient:
    """Client for one adapter's web API.

    Adapters are usually reached by IP address, and aiohttp's default cookie jar drops cookies
    from IP hosts, so the session passed in must use aiohttp.CookieJar(unsafe=True).
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        password: str,
        *,
        timeout: float = 15,
    ) -> None:
        """Initialize the client."""
        self.host = host
        self._session = session
        self._password = password
        self._base = f"http://{host}"
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._lock = asyncio.Lock()

    async def async_get(self, keys: Iterable[str]) -> dict[str, str | None]:
        """Read the listed keys."""
        query = "&".join(keys)
        return await self._run(lambda: self._fetch_values(f"/get.html?{query}"))

    async def async_get_all(self) -> dict[str, str | None]:
        """Read every key the firmware exposes (roughly 750 on a PLA6456)."""
        return await self._run(lambda: self._fetch_values("/getall.html"))

    async def async_set(self, values: Mapping[str, str]) -> None:
        """Write KEY=VALUE pairs through the same endpoint the web UI's CFL layer uses."""
        data = {"CSRFTOKEN": "", "REDIRECT": "/get.html", **values}
        await self._run(lambda: self._post("/get.html", data))

    async def async_reboot(self) -> None:
        """Reboot the adapter, as the hidden advanced.html "Hardware Reset" button does."""
        data = {
            "CSRFTOKEN": "",
            "REDIRECT": "reset.html",
            "SYSTEM.GENERAL.HW_RESET": "1",
        }
        # The adapter drops off the network right away, so the logout that follows fails.
        await self._run(lambda: self._post("/advanced.html", data), logout=False)

    async def _run(self, operation, *, logout: bool = True):
        async with self._lock:
            self._session.cookie_jar.clear()
            try:
                # No session was granted when this raises GhnBusyError, so skip the logout.
                await self._login()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise GhnConnectionError(f"{self.host}: {err}") from err
            try:
                return await operation()
            except (aiohttp.ClientError, TimeoutError) as err:
                raise GhnConnectionError(f"{self.host}: {err}") from err
            finally:
                if logout:
                    await self._logout()

    async def _login(self) -> None:
        # GET / issues the session cookie; the password POST without it silently fails.
        async with self._session.get(f"{self._base}/", timeout=self._timeout) as resp:
            await resp.read()
        async with self._session.post(
            f"{self._base}/",
            data={".PASSWORD": self._password},
            timeout=self._timeout,
        ) as resp:
            body = await resp.text(errors="replace")
        if _BUSY_MARKER in body:
            raise GhnBusyError(f"{self.host}: another web session is logged in")
        # A wrong password gets the same redirect as a good one; it only shows up as an
        # HTTP 403 on the next request.

    async def _logout(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}/logout.html",
                data={"CSRFTOKEN": "", "REDIRECT": "/", "LOG_OUT_OK": "Log Out"},
                timeout=self._timeout,
            ) as resp:
                await resp.read()
        except (aiohttp.ClientError, TimeoutError):
            pass

    async def _fetch_values(self, path: str) -> dict[str, str | None]:
        async with self._session.get(f"{self._base}{path}", timeout=self._timeout) as resp:
            body = await resp.text(errors="replace")
            status = resp.status
        self._check(status, body)
        values = parse_values(body)
        if not values:
            raise GhnConnectionError(f"{self.host}: no values in the response to {path}")
        return values

    async def _post(self, path: str, data: Mapping[str, str]) -> None:
        async with self._session.post(
            f"{self._base}{path}", data=dict(data), timeout=self._timeout
        ) as resp:
            body = await resp.text(errors="replace")
            status = resp.status
        self._check(status, body)

    def _check(self, status: int, body: str) -> None:
        if _BUSY_MARKER in body:
            raise GhnBusyError(f"{self.host}: another web session is logged in")
        if status in (401, 403):
            raise GhnAuthError(f"{self.host}: password rejected (HTTP {status})")
        if status >= 400:
            raise GhnConnectionError(f"{self.host}: HTTP {status}")
