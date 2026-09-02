from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from gateway.platform_registry import PlatformEntry, platform_registry
from adapter import MeshtasticAdapter, _normalize_node_id, _strip_markdown, _utf8_chunks

# Platform("meshtastic") is intentionally accepted only after plugin registration.
platform_registry.register(
    PlatformEntry(
        name="meshtastic",
        label="Meshtastic",
        adapter_factory=lambda cfg: MeshtasticAdapter(cfg),
        check_fn=lambda: True,
    )
)


def make_adapter(**extra):
    defaults = {
        "allow_from": ["!f11a8e29"],
        "expected_node_id": "!e786199c",
        "expected_short_name": "ak04",
        "max_payload_bytes": 180,
    }
    defaults.update(extra)
    return MeshtasticAdapter(SimpleNamespace(extra=defaults))


def test_node_id_normalization():
    assert _normalize_node_id(0xF11A8E29) == "!f11a8e29"
    assert _normalize_node_id("F11A8E29") == "!f11a8e29"
    assert _normalize_node_id("!F11A8E29") == "!f11a8e29"


def test_utf8_chunks_are_byte_safe_and_lossless():
    text = "Привет mesh " * 50
    chunks = _utf8_chunks(text, 73)
    assert len(chunks) > 1
    assert all(len(chunk.encode("utf-8")) <= 73 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()


def test_markdown_is_reduced_to_plain_text():
    assert _strip_markdown("**bold** [link](https://example.com)") == (
        "bold link (https://example.com)"
    )


def test_config_requires_allowlist():
    adapter = make_adapter(allow_from=[])
    assert adapter._dm_policy == "disabled"


def test_hotplug_connect_does_not_require_present_radio():
    async def scenario():
        adapter = make_adapter(
            device_glob="/definitely/not/present/*", reconnect_seconds=0.05
        )
        assert await adapter.connect() is True
        assert adapter.is_connected is True
        await asyncio.sleep(0.08)
        await adapter.disconnect()
        assert adapter.is_connected is False

    asyncio.run(scenario())


def test_send_reports_retryable_when_radio_absent():
    async def scenario():
        adapter = make_adapter()
        result = await adapter.send("!f11a8e29", "hello")
        assert result.success is False
        assert result.retryable is True
        assert "not connected" in (result.error or "")

    asyncio.run(scenario())


def test_packet_deduplication():
    adapter = make_adapter()
    assert adapter._remember_packet(123) is True
    assert adapter._remember_packet(123) is False
    assert adapter._remember_packet(124) is True
