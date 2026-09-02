# Security policy

## Important security model

This plugin forwards authorized Meshtastic direct messages to Hermes Agent. Depending on the tools enabled in Hermes, a successful request may cause actions on the host computer.

- Keep `allow_from` restricted to node IDs you control.
- Do not expose a tool-enabled agent to an untrusted mesh.
- Treat node-ID allowlisting as an access-control filter, not as a complete cryptographic identity guarantee.
- Keep Meshtastic and Hermes Agent updated.

The plugin accepts direct text messages only. Broadcast/channel messages and messages from non-allowlisted node IDs are discarded.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for the repository. Do not include secrets, private channel keys, precise locations, or captured private messages in a public issue.
