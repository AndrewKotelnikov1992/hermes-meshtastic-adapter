"""Real-radio smoke test for the Meshtastic Hermes adapter."""

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


async def main():
    cfg = SimpleNamespace(
        extra={
            "device_glob": "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_*-if00",
            "expected_node_id": "!e786199c",
            "expected_short_name": "ak04",
            "allow_from": ["!f11a8e29"],
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
        info = await adapter.get_chat_info("!f11a8e29")
        result = await adapter.send(
            "!f11a8e29", "Hermes gateway adapter: реальный smoke test пройден"
        )
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
    asyncio.run(main())
