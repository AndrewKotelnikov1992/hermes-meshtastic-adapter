# Hermes Meshtastic adapter

A user platform plugin that connects Hermes Gateway to a Meshtastic radio over USB serial.

## Features

- USB hotplug: the gateway stays running while the radio is absent.
- Automatic reconnect after unplug/replug.
- Stable `/dev/serial/by-id` glob matching.
- Optional verification of the locally attached node ID/short name.
- DM-only inbound traffic.
- Config-driven sender allowlist (node IDs recommended).
- UTF-8 byte-safe outbound chunking for LoRa payloads.
- Duplicate packet suppression across reconnects.

## Configuration

```yaml
gateway:
  platforms:
    meshtastic:
      enabled: true
      extra:
        device_glob: /dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00
        expected_node_id: "!e786199c"
        expected_short_name: ak04
        allow_from:
          - "!f11a8e29"
        channel_index: 0
        reconnect_seconds: 2
        serial_timeout_seconds: 20
        max_payload_bytes: 180
        want_ack: true
```

Use immutable node IDs in `allow_from`; short names are display metadata and are not secure identities.
