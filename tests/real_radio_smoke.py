"""Real-radio smoke test for the Meshtastic Hermes adapter."""

import argparse
import asyncio
from types import SimpleNamespace

from gateway.platform_registry import PlatformEntry, platform_registry

from adapter import MeshtasticAdapter

platform_registry.register(
    PlatformEntry(
        name="meshtastic",
        label="Meshtastic",
        adapter_factory=lambda cfg: MeshtasticAdapter(cfg),
        check_fn=lambda: True,
    )
)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote-node", required=True, help="DM destination, e.g. !87654321")
    parser.add_argument("--device", default="", help="Serial device; omit for auto-discovery")
    parser.add_argument("--local-node", default="", help="Optional expected local node ID")
    parser.add_argument("--local-short-name", default="", help="Optional expected short name")
    parser.add_argument("--message", default="Hermes Meshtastic adapter smoke test")
    return parser.parse_args()


async def main(args):
    cfg = SimpleNamespace(
        extra={
            "device": args.device,
            "expected_node_id": args.local_node,
            "expected_short_name": args.local_short_name,
            "allow_from": [args.remote_node],
            "reconnect_seconds": 1,
            "serial_timeout_seconds": 20,
            "max_payload_bytes": 180,
            "want_ack": True,
        }
    )
    adapter = MeshtasticAdapter(cfg)
    await adapter.connect()
    try:
        for _ in range(30):
            if adapter._interface is not None:
                break
            await asyncio.sleep(1)
        assert adapter._interface is not None, "radio did not connect"
        info = await adapter.get_chat_info(args.remote_node)
        result = await adapter.send(args.remote_node, args.message)
        print({
            "radio_path": adapter._interface_path,
            "remote": info,
            "send_success": result.success,
            "message_id": result.message_id,
            "error": result.error,
        })
        assert result.success, result.error
    finally:
        await adapter.disconnect()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
