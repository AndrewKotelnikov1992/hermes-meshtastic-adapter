# Roadmap

The adapter is intentionally small and currently focuses on direct messages over a USB serial radio.

## v0.1.x — reliability and release hardening

- [ ] Wait for routing ACKs and distinguish queued, acknowledged, timeout, and routing-error states.
- [ ] Add a bounded outbox so replies survive temporary USB disconnects.
- [ ] Add per-node rate limits and maximum inbound message size.
- [ ] Add fake-interface tests for receive filtering and the complete disconnect/reconnect lifecycle.
- [ ] Test against multiple Meshtastic and Hermes Agent releases.
- [ ] Add a guided setup/discovery command.

## v0.2 — more transports and platforms

- [ ] TCP transport through `TCPInterface`.
- [ ] BLE transport through `BLEInterface` where background BLE is reliable.
- [ ] Hardware testing on Heltec, RAK WisBlock, T-Beam/T-Deck, and Nordic-based boards.
- [ ] Automated Windows and macOS serial smoke tests.

## Security

- [ ] Investigate pinning the sender's Meshtastic public key in addition to its node ID.
- [ ] Add configurable concurrency and LLM-cost controls.
- [ ] Document threat models for local/private meshes and untrusted public meshes.

Contributions are welcome. Please keep new transports behind configuration and preserve default-deny inbound access.
