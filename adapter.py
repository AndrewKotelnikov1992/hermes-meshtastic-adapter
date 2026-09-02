"""Hotplug-capable Meshtastic platform adapter for Hermes Gateway."""

from __future__ import annotations

import asyncio
import contextlib
import glob
import logging
import os
import re
import threading
import time
from collections import deque
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from gateway.config import Platform
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

DEFAULT_DEVICE_GLOBS = (
    "/dev/serial/by-id/*",
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
    "/dev/cu.usbmodem*",
    "/dev/cu.usbserial*",
)


def _normalize_node_id(value: Any) -> str:
    """Return a canonical lower-case Meshtastic node id (`!xxxxxxxx`)."""
    if isinstance(value, int):
        return f"!{value:08x}"
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if text.startswith("!"):
        return text
    if re.fullmatch(r"[0-9a-f]{8}", text):
        return f"!{text}"
    return text


def _utf8_chunks(text: str, max_bytes: int) -> list[str]:
    """Split text without breaking UTF-8 characters, preferring whitespace."""
    text = text.strip()
    if not text:
        return []
    if max_bytes < 32:
        raise ValueError("max_payload_bytes must be at least 32")

    chunks: list[str] = []
    rest = text
    while rest:
        if len(rest.encode("utf-8")) <= max_bytes:
            chunks.append(rest)
            break

        lo, hi, best = 1, len(rest), 0
        while lo <= hi:
            mid = (lo + hi) // 2
            if len(rest[:mid].encode("utf-8")) <= max_bytes:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        if best <= 0:
            raise ValueError("max_payload_bytes cannot fit one character")

        split_at = best
        whitespace = max(rest.rfind(" ", 0, best), rest.rfind("\n", 0, best))
        if whitespace >= best // 2:
            split_at = whitespace
        chunk = rest[:split_at].strip()
        if not chunk:  # Long leading whitespace or an unusual boundary.
            chunk = rest[:best]
            split_at = best
        chunks.append(chunk)
        rest = rest[split_at:].lstrip()
    return chunks


def _strip_markdown(text: str) -> str:
    """Reduce common Markdown to compact plain text suitable for Meshtastic."""
    text = re.sub(r"```(?:\w+)?\n?", "", text)
    text = text.replace("```", "")
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\2", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    return text.strip()


def _node_user(interface: Any, node_id: str) -> dict[str, Any]:
    nodes = getattr(interface, "nodes", {}) or {}
    node = nodes.get(node_id) or nodes.get(node_id.lower()) or {}
    user = node.get("user", {}) if isinstance(node, dict) else {}
    return user if isinstance(user, dict) else {}


class MeshtasticAdapter(BasePlatformAdapter):
    """Meshtastic serial adapter whose lifecycle is independent of USB presence."""

    # The adapter validates `allow_from` before forwarding each packet. This lets
    # the gateway trust the intake decision without an env-var allowlist.
    enforces_own_access_policy = True

    def __init__(self, config, **kwargs):
        del kwargs
        super().__init__(config=config, platform=Platform("meshtastic"))
        extra = getattr(config, "extra", {}) or {}

        self.device = str(extra.get("device") or "").strip()
        self.device_glob = str(extra.get("device_glob") or "").strip()
        self.expected_node_id = _normalize_node_id(extra.get("expected_node_id"))
        self.expected_short_name = str(extra.get("expected_short_name") or "").strip()
        raw_allow = extra.get("allow_from") or []
        if isinstance(raw_allow, str):
            raw_allow = [item.strip() for item in raw_allow.split(",")]
        self.allowed_nodes = {
            _normalize_node_id(item) for item in raw_allow if _normalize_node_id(item)
        }
        self._dm_policy = "allowlist" if self.allowed_nodes else "disabled"

        self.channel_index = int(extra.get("channel_index", 0))
        self.reconnect_seconds = max(0.5, float(extra.get("reconnect_seconds", 2)))
        self.serial_timeout_seconds = max(
            5, int(extra.get("serial_timeout_seconds", 20))
        )
        self.max_payload_bytes = int(extra.get("max_payload_bytes", 180))
        if self.max_payload_bytes < 32 or self.max_payload_bytes > 220:
            raise ValueError("max_payload_bytes must be between 32 and 220")
        self.want_ack = bool(extra.get("want_ack", True))

        self._loop: asyncio.AbstractEventLoop | None = None
        self._supervisor_task: asyncio.Task | None = None
        self._radio_lost: asyncio.Event | None = None
        self._interface: Any = None
        self._interface_path = ""
        self._interface_lock = threading.RLock()
        self._opening_tasks: set[asyncio.Task] = set()
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._stopping = False
        self._subscribed = False
        self._seen_packet_ids: set[str] = set()
        self._seen_packet_order: deque[str] = deque(maxlen=512)

    @property
    def name(self) -> str:
        return "Meshtastic"

    async def connect(self, *, is_reconnect: bool = False) -> bool:
        del is_reconnect
        if not self.allowed_nodes:
            self._set_fatal_error(
                "meshtastic_allowlist_missing",
                "gateway.platforms.meshtastic.extra.allow_from must contain at least one node ID",
                retryable=False,
            )
            return False

        self._loop = asyncio.get_running_loop()
        self._radio_lost = asyncio.Event()
        self._stopping = False
        self._subscribe()
        self._supervisor_task = asyncio.create_task(
            self._hotplug_supervisor(), name="meshtastic-hotplug"
        )
        # The platform adapter is operational even while USB is absent. Hardware
        # presence is managed internally so unplugging never restarts Hermes.
        self._mark_connected()
        logger.info(
            "Meshtastic: hotplug supervisor started (device=%s, glob=%s, allow_from=%s)",
            self.device or "auto",
            self.device_glob or "auto",
            sorted(self.allowed_nodes),
        )
        return True

    async def disconnect(self) -> None:
        self._stopping = True
        self._mark_disconnected()
        if self._radio_lost:
            self._radio_lost.set()
        task = self._supervisor_task
        self._supervisor_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # SerialInterface construction is blocking and runs in a worker thread.
        # Drain any open that completed after supervisor cancellation, then wait
        # for its close operation before allowing the event loop to shut down.
        if self._opening_tasks:
            await asyncio.gather(*tuple(self._opening_tasks), return_exceptions=True)
            await asyncio.sleep(0)  # Let completion callbacks schedule closes.
        if self._cleanup_tasks:
            await asyncio.gather(*tuple(self._cleanup_tasks), return_exceptions=True)
        await self._close_radio()
        self._unsubscribe()
        logger.info("Meshtastic: adapter stopped")

    def _subscribe(self) -> None:
        if self._subscribed:
            return
        from pubsub import pub

        pub.subscribe(self._on_text, "meshtastic.receive.text")
        pub.subscribe(self._on_connection_lost, "meshtastic.connection.lost")
        self._subscribed = True

    def _unsubscribe(self) -> None:
        if not self._subscribed:
            return
        from pubsub import pub

        with contextlib.suppress(Exception):
            pub.unsubscribe(self._on_text, "meshtastic.receive.text")
        with contextlib.suppress(Exception):
            pub.unsubscribe(self._on_connection_lost, "meshtastic.connection.lost")
        self._subscribed = False

    def _candidate_paths(self) -> Iterable[str]:
        """Yield unique serial candidates, preferring stable by-id paths."""
        candidates: list[str] = []
        if self.device:
            candidates.append(self.device)
        elif self.device_glob:
            candidates.extend(sorted(glob.glob(self.device_glob)))
        else:
            for pattern in DEFAULT_DEVICE_GLOBS:
                candidates.extend(sorted(glob.glob(pattern)))
            try:
                from serial.tools import list_ports

                candidates.extend(
                    port.device
                    for port in list_ports.comports()
                    if os.name == "nt"
                    or getattr(port, "vid", None) is not None
                    or "usb" in port.device.lower()
                )
            except Exception as exc:
                logger.debug("Meshtastic: serial port enumeration failed: %s", exc)

        seen: set[str] = set()
        for path in candidates:
            if not path:
                continue
            # Resolve Linux/macOS symlinks so /dev/serial/by-id and /dev/ttyACM
            # aliases do not cause the same radio to be opened twice. Keep COM
            # names intact on Windows, where Path.exists() is not meaningful.
            identity = os.path.realpath(path) if os.name != "nt" else path.upper()
            if identity in seen:
                continue
            seen.add(identity)
            if os.name == "nt" or Path(path).exists():
                yield path

    async def _connect_first_available(self) -> bool:
        """Try every candidate once, rather than retrying a wrong first port."""
        for path in self._candidate_paths():
            if self._stopping:
                return False
            if await self._open_radio(path):
                return True
        return False

    async def _hotplug_supervisor(self) -> None:
        while not self._stopping:
            try:
                if self._interface is None:
                    connected = await self._connect_first_available()
                    if not connected:
                        await asyncio.sleep(self.reconnect_seconds)
                    continue

                assert self._radio_lost is not None
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._radio_lost.wait(), timeout=self.reconnect_seconds
                    )

                connected = self._radio_is_connected()
                if self._radio_lost.is_set() or not connected:
                    self._radio_lost.clear()
                    await self._close_radio()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("Meshtastic: hotplug supervisor error: %s", exc)
                await self._close_radio()
                await asyncio.sleep(self.reconnect_seconds)

    def _radio_is_connected(self) -> bool:
        interface = self._interface
        path_present = os.name == "nt" or Path(self._interface_path).exists()
        return bool(
            interface is not None
            and getattr(interface, "isConnected", None) is not None
            and interface.isConnected.is_set()
            and path_present
        )

    async def _open_radio(self, path: str) -> bool:
        logger.info("Meshtastic: detected %s; connecting", path)
        open_task = asyncio.create_task(
            asyncio.to_thread(self._create_interface, path),
            name=f"meshtastic-open:{path}",
        )
        self._opening_tasks.add(open_task)
        try:
            # Shield the worker so cancellation cannot abandon a serial interface
            # that finishes opening after adapter shutdown.
            interface = await asyncio.shield(open_task)
        except asyncio.CancelledError:
            open_task.add_done_callback(self._close_late_interface)
            raise
        except Exception as exc:
            self._opening_tasks.discard(open_task)
            logger.warning("Meshtastic: connection to %s failed: %s", path, exc)
            return False
        self._opening_tasks.discard(open_task)

        if self._stopping:
            await asyncio.to_thread(self._safe_close, interface)
            return False

        try:
            my_info = getattr(interface, "myInfo", None)
            if my_info is None:
                raise ValueError("device did not provide Meshtastic node information")
            local_num = int(my_info.my_node_num)
            local_id = _normalize_node_id(local_num)
            local_user = _node_user(interface, local_id)
            short_name = str(local_user.get("shortName") or "")

            mismatch = ""
            if self.expected_node_id and local_id != self.expected_node_id:
                mismatch = f"node id {local_id}, expected {self.expected_node_id}"
            elif self.expected_short_name and short_name != self.expected_short_name:
                mismatch = (
                    f"short name {short_name!r}, expected {self.expected_short_name!r}"
                )
        except Exception as exc:
            logger.warning("Meshtastic: rejecting non-Meshtastic port %s: %s", path, exc)
            await asyncio.to_thread(self._safe_close, interface)
            return False
        if mismatch:
            logger.warning("Meshtastic: rejecting %s: %s", path, mismatch)
            await asyncio.to_thread(self._safe_close, interface)
            return False

        with self._interface_lock:
            self._interface = interface
            self._interface_path = path
        if self._radio_lost:
            self._radio_lost.clear()
        logger.info(
            "Meshtastic: radio connected: %s (%s, %s)", short_name, local_id, path
        )
        return True

    def _close_late_interface(self, task: asyncio.Task) -> None:
        """Close a radio whose blocking open completed after cancellation."""
        self._opening_tasks.discard(task)
        if task.cancelled():
            return
        try:
            interface = task.result()
        except Exception:
            return
        cleanup = asyncio.create_task(asyncio.to_thread(self._safe_close, interface))
        self._cleanup_tasks.add(cleanup)
        cleanup.add_done_callback(self._cleanup_tasks.discard)

    def _create_interface(self, path: str):
        from meshtastic.serial_interface import SerialInterface

        return SerialInterface(
            devPath=path,
            noNodes=False,
            timeout=self.serial_timeout_seconds,
        )

    @staticmethod
    def _safe_close(interface: Any) -> None:
        with contextlib.suppress(Exception):
            interface.close()

    async def _close_radio(self) -> None:
        with self._interface_lock:
            interface = self._interface
            path = self._interface_path
            self._interface = None
            self._interface_path = ""
        if interface is not None:
            await asyncio.to_thread(self._safe_close, interface)
            logger.info("Meshtastic: radio disconnected: %s", path)

    def _on_connection_lost(self, interface=None, **kwargs) -> None:
        del kwargs
        with self._interface_lock:
            ours = interface is not None and interface is self._interface
        if ours and self._loop and self._radio_lost:
            self._loop.call_soon_threadsafe(self._radio_lost.set)

    def _remember_packet(self, packet_id: Any) -> bool:
        """Return False for duplicate packet IDs."""
        key = str(packet_id or "")
        if not key:
            return True
        if key in self._seen_packet_ids:
            return False
        if len(self._seen_packet_order) == self._seen_packet_order.maxlen:
            oldest = self._seen_packet_order[0]
            self._seen_packet_ids.discard(oldest)
        self._seen_packet_order.append(key)
        self._seen_packet_ids.add(key)
        return True

    def _on_text(self, packet=None, interface=None, **kwargs) -> None:
        del kwargs
        if not isinstance(packet, dict):
            return
        with self._interface_lock:
            if interface is None or interface is not self._interface:
                return
            local_num = int(interface.myInfo.my_node_num)

        # Only direct messages addressed to the USB-attached radio enter Hermes.
        try:
            destination = int(packet.get("to"))
        except (TypeError, ValueError):
            return
        if destination != local_num:
            return

        sender_id = _normalize_node_id(packet.get("fromId") or packet.get("from"))
        if sender_id not in self.allowed_nodes:
            logger.warning("Meshtastic: ignored DM from unauthorized node %s", sender_id)
            return
        if not self._remember_packet(packet.get("id")):
            return

        decoded = packet.get("decoded") or {}
        text = decoded.get("text") if isinstance(decoded, dict) else None
        if not isinstance(text, str) or not text.strip():
            return

        if not self._loop:
            return
        future = asyncio.run_coroutine_threadsafe(
            self._dispatch_packet(packet, interface, sender_id, text.strip()),
            self._loop,
        )
        future.add_done_callback(self._log_dispatch_failure)

    @staticmethod
    def _log_dispatch_failure(future) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            exc = future.exception()
            if exc:
                logger.error("Meshtastic: inbound dispatch failed: %s", exc)

    async def _dispatch_packet(
        self, packet: dict[str, Any], interface: Any, sender_id: str, text: str
    ) -> None:
        user = _node_user(interface, sender_id)
        short_name = str(user.get("shortName") or sender_id)
        long_name = str(user.get("longName") or short_name)
        source = self.build_source(
            chat_id=sender_id,
            chat_name=short_name,
            chat_type="dm",
            user_id=sender_id,
            user_name=long_name,
        )
        rx_time = packet.get("rxTime")
        try:
            timestamp = datetime.fromtimestamp(float(rx_time)) if rx_time else datetime.now()
        except (TypeError, ValueError, OSError):
            timestamp = datetime.now()
        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            raw_message=packet,
            message_id=str(packet.get("id") or int(time.time() * 1000)),
            timestamp=timestamp,
            metadata={
                "snr": packet.get("rxSnr"),
                "rssi": packet.get("rxRssi"),
                "hop_start": packet.get("hopStart"),
                "hop_limit": packet.get("hopLimit"),
            },
        )
        await self.handle_message(event)

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SendResult:
        del reply_to, metadata
        destination = _normalize_node_id(chat_id)
        if destination not in self.allowed_nodes:
            return SendResult(success=False, error="Destination is not in allow_from")

        plain = _strip_markdown(content)
        try:
            # Reserve room for a multipart marker such as "[12/12] ".
            chunks = _utf8_chunks(plain, self.max_payload_bytes - 12)
        except ValueError as exc:
            return SendResult(success=False, error=str(exc))
        if not chunks:
            return SendResult(success=False, error="Message is empty")

        message_ids: list[str] = []
        for index, chunk in enumerate(chunks):
            if len(chunks) > 1:
                prefix = f"[{index + 1}/{len(chunks)}] "
                # Re-split conservatively if the multipart prefix consumes room.
                room = self.max_payload_bytes - len(prefix.encode("utf-8"))
                parts = _utf8_chunks(chunk, room)
                if len(parts) != 1:
                    return SendResult(
                        success=False,
                        error="Internal chunking error",
                    )
                chunk = prefix + parts[0]
            try:
                packet = await asyncio.to_thread(
                    self._send_one, destination, chunk
                )
                packet_id = getattr(packet, "id", None)
                message_ids.append(str(packet_id or int(time.time() * 1000)))
            except Exception as exc:
                logger.warning("Meshtastic: send to %s failed: %s", destination, exc)
                if self._radio_lost:
                    self._radio_lost.set()
                return SendResult(
                    success=False,
                    error=f"Meshtastic radio unavailable: {exc}",
                    retryable=True,
                )
            if index + 1 < len(chunks):
                await asyncio.sleep(0.8)

        return SendResult(
            success=True,
            message_id=message_ids[-1],
            continuation_message_ids=tuple(message_ids[:-1]),
        )

    def _send_one(self, destination: str, text: str):
        with self._interface_lock:
            interface = self._interface
            if interface is None or not interface.isConnected.is_set():
                raise ConnectionError("USB radio is not connected")
            return interface.sendText(
                text,
                destinationId=destination,
                wantAck=self.want_ack,
                channelIndex=self.channel_index,
            )

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        del chat_id, metadata

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        node_id = _normalize_node_id(chat_id)
        with self._interface_lock:
            user = _node_user(self._interface, node_id) if self._interface else {}
        return {
            "name": str(user.get("shortName") or node_id),
            "type": "dm",
        }


def check_requirements() -> bool:
    try:
        import meshtastic.serial_interface  # noqa: F401
        from pubsub import pub  # noqa: F401
    except ImportError:
        return False
    return True


def validate_config(config) -> bool:
    extra = getattr(config, "extra", {}) or {}
    raw_allow = extra.get("allow_from") or []
    if isinstance(raw_allow, str):
        raw_allow = [item.strip() for item in raw_allow.split(",")]
    return bool(raw_allow)


def is_connected(config) -> bool:
    return validate_config(config) and check_requirements()


def register(ctx) -> None:
    ctx.register_platform(
        name="meshtastic",
        label="Meshtastic",
        adapter_factory=lambda cfg: MeshtasticAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=[],
        install_hint="Install the Python package: pip install meshtastic",
        max_message_length=720,
        emoji="📻",
        pii_safe=True,
        allow_update_command=False,
        platform_hint=(
            "You are chatting over Meshtastic LoRa direct messages. Use concise "
            "plain text, avoid Markdown tables and long code blocks, and keep the "
            "answer as short as practical. Long responses are split into small "
            "radio packets automatically."
        ),
    )
