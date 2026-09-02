# Hermes Meshtastic adapter

[![CI](https://github.com/AndrewKotelnikov1992/hermes-meshtastic-adapter/actions/workflows/ci.yml/badge.svg)](https://github.com/AndrewKotelnikov1992/hermes-meshtastic-adapter/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

An experimental [Hermes Agent](https://github.com/NousResearch/hermes-agent) platform plugin that turns a Meshtastic radio into a direct-message channel for Hermes.

```text
remote Meshtastic node ⇄ LoRa mesh ⇄ USB radio ⇄ Hermes Gateway
```

> **Status:** experimental v0.1. The adapter has been exercised on Linux with two Heltec V4 nodes and Meshtastic 2.8. It should work with other devices that expose the Meshtastic serial API, but broader hardware testing is welcome.

## Features

- USB hotplug: Hermes stays running while the radio is absent.
- Automatic reconnect after unplug/replug, even if the tty name changes.
- Serial auto-discovery on Linux, macOS, and Windows.
- Optional explicit device path or glob.
- Optional verification of the locally attached node ID and short name.
- Tries every serial candidate when multiple devices are connected.
- Inbound direct text messages only; channel broadcasts are ignored.
- Default-deny sender allowlist using Meshtastic node IDs.
- UTF-8 byte-safe outbound chunking for LoRa payloads.
- Duplicate packet suppression across reconnects.

## Requirements

- Hermes Agent with user-plugin support
- Python 3.11 or newer
- `meshtastic>=2.7,<3`
- A Meshtastic device accessible through its serial API

Linux users must have permission to open the serial device, commonly through the `dialout` group. Log out and back in after changing group membership.

## Installation

Install the plugin:

```bash
hermes plugins install https://github.com/AndrewKotelnikov1992/hermes-meshtastic-adapter.git
hermes plugins enable meshtastic-platform
```

Install the Meshtastic Python package into the same Python environment as Hermes. For an `uv tool` installation:

```bash
uv tool inject hermes-agent 'meshtastic>=2.7,<3'
```

For the standard Hermes source installation:

```bash
~/.hermes/hermes-agent/venv/bin/python -m pip install 'meshtastic>=2.7,<3'
```

Verify discovery:

```bash
hermes plugins list
```

## Find node IDs

With the USB radio connected:

```bash
meshtastic --info
```

Node IDs look like `!12345678`. Use node IDs for access control; short names are display metadata and are not unique.

## Configuration

Add the platform to `~/.hermes/config.yaml`:

```yaml
gateway:
  platforms:
    meshtastic:
      enabled: true
      extra:
        # Optional. Omit both for automatic serial discovery.
        # device: /dev/serial/by-id/usb-your-radio
        # device_glob: /dev/ttyACM*

        # Optional safeguards when several radios may be connected.
        # expected_node_id: "!12345678"
        # expected_short_name: BASE

        # Required: only these remote nodes can invoke Hermes.
        allow_from:
          - "!87654321"

        channel_index: 0
        reconnect_seconds: 2
        serial_timeout_seconds: 20
        max_payload_bytes: 180
        want_ack: true
```

Restart the gateway after enabling or changing the plugin:

```bash
hermes gateway restart
```

The adapter may start with no radio attached. It scans periodically and connects when a suitable radio appears.

## Device discovery

When `device` and `device_glob` are omitted, discovery checks:

- `/dev/serial/by-id/*`, `/dev/ttyACM*`, and `/dev/ttyUSB*` on Linux;
- `/dev/cu.usbmodem*` and `/dev/cu.usbserial*` on macOS;
- ports returned by pyserial, including Windows `COM` ports.

Stable `/dev/serial/by-id` paths are preferred on Linux. If multiple candidates exist, the adapter opens each until `expected_node_id` / `expected_short_name` matches. Without an expected identity, the first Meshtastic-compatible radio is selected.

## Hotplug behavior

- Starting Hermes without a radio is supported.
- Removing the radio does not stop or restart Hermes Gateway.
- A serial disconnect or disappearing device path closes the old interface.
- Reconnection creates a fresh Meshtastic interface and restores event handling.
- A send attempted while the radio is absent returns a retryable error.

A persistent outbox and confirmed routing-ACK tracking are planned; see [ROADMAP.md](ROADMAP.md).

## Hardware smoke test

The hardware test is intentionally excluded from normal CI and requires explicit node IDs:

```bash
PYTHONPATH=. python tests/real_radio_smoke.py \
  --remote-node '!87654321' \
  --device /dev/serial/by-id/usb-your-radio \
  --local-node '!12345678'
```

Do not commit private channel keys or personal node IDs.

## Security

Every accepted message can invoke an AI agent that may have tools on the host. Keep `allow_from` narrow and do not expose a tool-enabled Hermes instance to an untrusted mesh.

A node-ID allowlist is an access-control filter, not by itself a complete cryptographic identity guarantee. See [SECURITY.md](SECURITY.md).

## Current limitations

- USB serial only; TCP and BLE are not implemented.
- Direct text messages only; no public channel handling or attachments.
- `want_ack` requests a Meshtastic routing ACK, but the adapter currently reports successful local queueing rather than waiting for the ACK.
- Replies are not persisted across a disconnect that occurs while Hermes is generating a response.
- Linux is the only platform tested on real hardware so far.

See [ROADMAP.md](ROADMAP.md) for planned work.

## Development

Install Hermes Agent and the development dependencies, then run:

```bash
pytest -q
ruff check .
```

Contributions and hardware reports are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE) © 2026 Kotelnikov Andrew
