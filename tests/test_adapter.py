from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        "allow_from": ["!87654321"],
        "expected_node_id": "!12345678",
        "max_payload_bytes": 180,
    }
    defaults.update(extra)
    return MeshtasticAdapter(SimpleNamespace(extra=defaults))


def test_node_id_normalization():
    assert _normalize_node_id(0x87654321) == "!87654321"
    assert _normalize_node_id("87654321") == "!87654321"
    assert _normalize_node_id("!87654321") == "!87654321"


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
        result = await adapter.send("!87654321", "hello")
        assert result.success is False
        assert result.retryable is True
        assert "not connected" in (result.error or "")

    asyncio.run(scenario())


def test_packet_deduplication():
    adapter = make_adapter()
    assert adapter._remember_packet(123) is True
    assert adapter._remember_packet(123) is False
    assert adapter._remember_packet(124) is True


def test_explicit_device_is_used_when_present(tmp_path):
    device = tmp_path / "radio"
    device.touch()
    adapter = make_adapter(device=str(device))
    assert list(adapter._candidate_paths()) == [str(device)]


def test_all_candidates_are_tried_until_identity_matches(monkeypatch):
    async def scenario():
        adapter = make_adapter()
        monkeypatch.setattr(adapter, "_candidate_paths", lambda: iter(["first", "second"]))
        attempted = []

        async def fake_open(path):
            attempted.append(path)
            return path == "second"

        monkeypatch.setattr(adapter, "_open_radio", fake_open)
        assert await adapter._connect_first_available() is True
        assert attempted == ["first", "second"]

    asyncio.run(scenario())


def test_auto_discovery_ignores_non_usb_linux_serial_ports(monkeypatch, tmp_path):
    usb_radio = tmp_path / "usb-radio"
    onboard_uart = tmp_path / "ttyS0"
    usb_radio.touch()
    onboard_uart.touch()

    monkeypatch.setattr("adapter.glob.glob", lambda _pattern: [])
    monkeypatch.setattr(
        "serial.tools.list_ports.comports",
        lambda: [
            SimpleNamespace(device=str(onboard_uart), vid=None),
            SimpleNamespace(device=str(usb_radio), vid=0x303A),
        ],
    )

    adapter = make_adapter()
    assert list(adapter._candidate_paths()) == [str(usb_radio)]


def test_inbound_filter_accepts_only_allowlisted_direct_messages():
    async def scenario():
        adapter = make_adapter()
        interface = SimpleNamespace(
            myInfo=SimpleNamespace(my_node_num=0x12345678),
            nodes={},
        )
        adapter._interface = interface
        adapter._loop = asyncio.get_running_loop()
        adapter._dispatch_packet = AsyncMock()

        valid = {
            "id": 1,
            "fromId": "!87654321",
            "to": 0x12345678,
            "decoded": {"text": "hello"},
        }
        adapter._on_text(packet=valid, interface=interface)
        await asyncio.sleep(0.01)
        adapter._dispatch_packet.assert_awaited_once()

        adapter._dispatch_packet.reset_mock()
        adapter._on_text(packet={**valid, "id": 2, "to": 0xFFFFFFFF}, interface=interface)
        adapter._on_text(packet={**valid, "id": 3, "fromId": "!11111111"}, interface=interface)
        adapter._on_text(packet=valid, interface=interface)  # duplicate packet id
        await asyncio.sleep(0)
        adapter._dispatch_packet.assert_not_awaited()

    asyncio.run(scenario())


def test_windows_com_port_does_not_require_filesystem_path(monkeypatch):
    adapter = make_adapter()
    connected = threading.Event()
    connected.set()
    adapter._interface = SimpleNamespace(isConnected=connected)
    adapter._interface_path = "COM42"
    monkeypatch.setattr("adapter.os.name", "nt")
    assert adapter._radio_is_connected() is True


def test_malformed_candidate_is_closed_and_next_candidate_is_tried(monkeypatch):
    async def scenario():
        adapter = make_adapter(expected_node_id="", expected_short_name="")
        closed = []

        malformed = SimpleNamespace(close=lambda: closed.append("malformed"))
        valid = SimpleNamespace(
            myInfo=SimpleNamespace(my_node_num=0x12345678),
            nodes={"!12345678": {"user": {"shortName": "BASE"}}},
            close=lambda: closed.append("valid"),
        )

        def fake_create(path):
            return malformed if path == "first" else valid

        monkeypatch.setattr(adapter, "_candidate_paths", lambda: iter(["first", "second"]))
        monkeypatch.setattr(adapter, "_create_interface", fake_create)
        assert await adapter._connect_first_available() is True
        assert adapter._interface is valid
        assert closed == ["malformed"]
        await adapter._close_radio()

    asyncio.run(scenario())


def test_cancelled_slow_open_is_drained_and_closed(monkeypatch):
    async def scenario():
        adapter = make_adapter()
        started = threading.Event()
        release = threading.Event()
        closed = threading.Event()
        late_interface = SimpleNamespace(close=closed.set)

        def slow_create(_path):
            started.set()
            release.wait(timeout=2)
            return late_interface

        monkeypatch.setattr(adapter, "_create_interface", slow_create)
        open_coroutine = asyncio.create_task(adapter._open_radio("slow"))
        assert await asyncio.to_thread(started.wait, 1)
        open_coroutine.cancel()
        try:
            await open_coroutine
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("radio open was not cancelled")

        adapter._stopping = True
        release.set()
        await adapter.disconnect()
        assert closed.is_set()
        assert not adapter._opening_tasks
        assert not adapter._cleanup_tasks

    asyncio.run(scenario())
