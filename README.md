# G.hn Powerline for Home Assistant

A Home Assistant integration for G.hn powerline adapters built on MaxLinear chipsets. It reads each
adapter's live status (temperature, CPU, memory, role in the powerline network, and the PHY rate to
every other adapter) and offers the adapter's complete settings dump as downloadable diagnostics.

Tested on the **Zyxel PLA6456** (firmware V1.00(ABSU.7)C0, MaxLinear 5152 "Turia"). Other
MaxLinear-based adapters, such as Comtrend's PG-9182 series, use the same web API and should work.
Reports are welcome.

## How it works

The adapters' web UI sits on a key/value API: `GET /getall.html` returns every setting and status
value (about 750 on a PLA6456), `GET /get.html?KEY&KEY` returns the listed ones, and
`POST /get.html` writes. [modest/ghn-tweak](https://github.com/modest/ghn-tweak) documented it
first. The integration polls the keys it needs once a minute and turns them into entities.

**The web UI allows only one session at a time.** Each poll logs in, reads and logs out in a couple
of seconds. If you are logged in with a browser when a poll runs, the poll keeps the previous values
and tries again next time; after 10 minutes of that, the entities go unavailable. Log out of the web
UI when you are done with it, and the browser in turn may briefly see the adapter's "another active
web session" page while a poll is running.

## Installation

1. In HACS, open **Custom repositories** and add `https://github.com/gtxaspec/ghn-hacs` as an
   **Integration**.
2. Install **G.hn Powerline** and restart Home Assistant.
3. Go to **Settings > Devices & services > Add integration > G.hn Powerline** and enter the
   adapter's address and web UI password. Add each adapter separately.

The adapter must be reachable from Home Assistant on its management address (port 80). The
integration talks to it directly on your network; nothing goes to the cloud.

## Entities

| Entity | Notes |
|---|---|
| Temperature | Chip temperature, °C. The firmware reports hundredths of a degree. |
| Role | Domain master or end point. |
| Connected peers | Number of adapters this one currently has a link to. |
| *peer* TX rate / RX rate | PHY rate to and from each other adapter, Mbps, using the same conversion as the web UI. A peer that is also set up in Home Assistant is named after its entry. |
| Ethernet link | Link state of the Ethernet port. |
| Encryption | Whether the powerline network is secured. |
| CPU usage, Memory usage, Last boot, G.hn profile, Ethernet speed | Diagnostic. |
| User notches, *peer* attenuation (raw), *peer* wire length (raw) | Diagnostic, disabled by default. The firmware does not document the attenuation and wire-length units, so they are shown as reported. |
| Restart | Reboots the adapter. Its powerline links drop for about 30 seconds. |

Peer sensors are created as peers appear, and show as unavailable while a peer is not linked.

## Diagnostics

**Download diagnostics** on the device page reads every key the adapter exposes and includes it in
the file, along with the parsed peer table. The web UI password, the pairing password, the
powerline domain name and anything named like a key are redacted, as is the serial number.

## Development

`scripts/ghn_probe.py <host> <password> [KEY ...]` reads an adapter with the integration's client,
without Home Assistant. `python -m pytest tests` runs the parser tests.
