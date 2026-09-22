#!/usr/bin/env python3
"""Read an adapter with the integration's client, outside Home Assistant.

Usage: ghn_probe.py <host> <password> [KEY ...]
With no keys it reads everything (getall.html) and prints the parsed peer table.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import aiohttp

_API = Path(__file__).resolve().parent.parent / "custom_components" / "ghn_powerline" / "api.py"
_spec = importlib.util.spec_from_file_location("ghn_api", _API)
api = importlib.util.module_from_spec(_spec)
# dataclasses resolves string annotations through sys.modules, so register before executing.
sys.modules[_spec.name] = api
_spec.loader.exec_module(api)


async def main(host: str, password: str, keys: list[str]) -> int:
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True)) as session:
        client = api.GhnClient(session, host, password)
        try:
            values = await (client.async_get(keys) if keys else client.async_get_all())
        except api.GhnError as err:
            print(f"{type(err).__name__}: {err}")
            return 1
    for key in sorted(values):
        print(f"{key}={values[key]}")
    if not keys:
        print(f"\n{len(values)} keys")
        own = values.get("SYSTEM.PRODUCTION.MAC_ADDR")
        for peer in api.parse_peers(values, own).values():
            print(f"peer {peer}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3:])))
