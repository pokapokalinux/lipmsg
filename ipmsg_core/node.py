from __future__ import annotations

from dataclasses import dataclass
import getpass
import logging
from pathlib import Path
import re
import socket
import threading
import time
from typing import Callable

from .file_transfer import (
    FileAttachmentSpec,
    FileTransferManager,
    build_file_attach_string,
    parse_file_attach_string,
)
from .history import HistoryStore
from .protocol import Command, Packet, build_packet, generate_packet_no
from .transport import ReceivedDatagram, UdpTransport

logger = logging.getLogger(__name__)

DEFAULT_ABSENCE_REPLY = "ただいま離席中です。"
_BROADCAST_SPLIT_RE = re.compile(r"[\s,;]+")


def parse_broadcast_targets(raw: str | list[str] | tuple[str, ...]) -> list[str]:
    """カンマ・空白・改行などで区切られたブロードキャスト／宛先IP群を重複なく抽出する"""
    if isinstance(raw, str):
        tokens = _BROADCAST_SPLIT_RE.split(raw.strip())
    else:
        tokens = []
        for item in raw:
            tokens.extend(_BROADCAST_SPLIT_RE.split(str(item).strip()))

    targets: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        addr = token.strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        targets.append(addr)
    return targets or ["255.255.255.255"]


@dataclass
class PeerInfo:
    ip: str
    port: int
    user: str
    host: str
    group: str = ""
    absent: bool = False
    last_seen: float = 0.0


@dataclass(frozen=True)
class IncomingMessageEvent:
    sender_ip: str
    sender_port: int
    packet_no: str
    user: str
    host: str
    group: str
    body: str
    sealed: bool
    auto_reply: bool
    wants_read_notice: bool
    attachments: list[FileAttachmentSpec]


@dataclass
class NodeConfig:
    user: str = ""
    host: str = ""
    group: str = ""
    port: int = 2425
    bind_ip: str = "0.0.0.0"
    broadcast_ip: str = "255.255.255.255"
    absence_reply: str = DEFAULT_ABSENCE_REPLY
    use_utf8: bool = True
    enable_file_server: bool = True
    auto_reply_cooldown_sec: float = 15.0

    def resolved_user(self) -> str:
        return self.user.strip() or getpass.getuser() or "user"

    def resolved_host(self) -> str:
        return self.host.strip() or socket.gethostname() or "localhost"

    def broadcast_targets(self) -> list[str]:
        return parse_broadcast_targets(self.broadcast_ip)


class IpmsgNode:
    """プレゼンス・不在/自動応答・メッセージ送受信・ファイル転送を統合するコアノード"""

    def __init__(
        self,
        *,
        config: NodeConfig | None = None,
        history: HistoryStore | None = None,
        on_peers_changed: Callable[[list[PeerInfo]], None] | None = None,
        on_message_received: Callable[[IncomingMessageEvent], None] | None = None,
        on_absence_changed: Callable[[bool], None] | None = None,
    ) -> None:
        self.config = config or NodeConfig()
        self.history = history
        self._on_peers_changed = on_peers_changed
        self._on_message_received = on_message_received
        self._on_absence_changed = on_absence_changed

        self._lock = threading.Lock()
        self._peers: dict[str, PeerInfo] = {}
        self._absent = False
        self._last_auto_reply_at: dict[str, float] = {}

        self._transport = UdpTransport(
            bind_ip=self.config.bind_ip,
            port=self.config.port,
            broadcast_ip=self.config.broadcast_targets()[0],
            on_packet=self._handle_datagram,
        )
        self._files = FileTransferManager(
            port=self.config.port,
            bind_ip=self.config.bind_ip,
        )

    @property
    def is_absent(self) -> bool:
        with self._lock:
            return self._absent

    def start(self) -> None:
        self._transport.start()
        if self.config.enable_file_server:
            try:
                self._files.start_server()
            except OSError as exc:
                logger.warning("TCP file transfer server failed to start: %s", exc)
        self.announce_presence()

    def stop(self) -> None:
        try:
            self.announce_exit()
        except Exception:
            logger.debug("Failed to announce exit", exc_info=True)
        self._files.stop_server()
        self._transport.stop()

    def get_peers(self) -> list[PeerInfo]:
        with self._lock:
            return sorted(
                self._peers.values(),
                key=lambda p: (p.user.lower(), p.host.lower(), p.ip),
            )

    def set_absence(self, absent: bool) -> None:
        with self._lock:
            changed = self._absent != absent
            self._absent = absent
        if changed and self._on_absence_changed is not None:
            self._on_absence_changed(absent)
        self.announce_absence()

    def _broadcast_raw(self, raw: bytes) -> None:
        for target in self.config.broadcast_targets():
            try:
                self._transport.broadcast(raw, broadcast_ip=target)
            except OSError as exc:
                logger.debug("Broadcast failed to %s: %s", target, exc)

    def announce_presence(self) -> None:
        flags = int(Command.BR_ENTRY)
        if self.is_absent:
            flags |= int(Command.ABSENCEOPT)
        raw = build_packet(
            user=self.config.resolved_user(),
            host=self.config.resolved_host(),
            command=flags,
            body=self.config.resolved_user(),
            extra=self.config.group,
            use_utf8=self.config.use_utf8,
        )
        self._broadcast_raw(raw)

    def announce_absence(self) -> None:
        flags = int(Command.BR_ABSENCE)
        if self.is_absent:
            flags |= int(Command.ABSENCEOPT)
        raw = build_packet(
            user=self.config.resolved_user(),
            host=self.config.resolved_host(),
            command=flags,
            body=self.config.absence_reply if self.is_absent else "",
            extra=self.config.group,
            use_utf8=self.config.use_utf8,
        )
        self._broadcast_raw(raw)

    def announce_exit(self) -> None:
        raw = build_packet(
            user=self.config.resolved_user(),
            host=self.config.resolved_host(),
            command=int(Command.BR_EXIT),
            body="",
            extra=self.config.group,
            use_utf8=self.config.use_utf8,
        )
        self._broadcast_raw(raw)

    def send_message(
        self,
        *,
        target: PeerInfo,
        body: str,
        sealed: bool = False,
        file_paths: list[str | Path] | None = None,
        wait_ack: bool = True,
        send_read_notice_req: bool = True,
    ) -> str:
        pkt_no = generate_packet_no()
        flags = int(Command.SENDMSG)
        if wait_ack:
            flags |= int(Command.SENDCHECKOPT)
        if sealed:
            flags |= int(Command.SECRETOPT)
        if send_read_notice_req and sealed:
            flags |= int(Command.READCHECKOPT)

        extra = ""
        attachments: list[FileAttachmentSpec] = []
        if file_paths:
            attachments = self._files.register_files(
                sender_user=self.config.resolved_user(),
                packet_no=pkt_no,
                paths=file_paths,
            )
            if attachments:
                flags |= int(Command.FILEATTACHOPT)
                extra = build_file_attach_string(attachments)

        raw = build_packet(
            user=self.config.resolved_user(),
            host=self.config.resolved_host(),
            command=flags,
            body=body,
            extra=extra,
            packet_no=pkt_no,
            use_utf8=self.config.use_utf8,
        )
        self._transport.send_to(
            raw,
            target.ip,
            target.port,
            packet_no=pkt_no,
            wait_ack=wait_ack,
        )

        if self.history is not None:
            self.history.record_message(
                direction="sent",
                peer_user=target.user,
                peer_host=target.host,
                peer_ip=target.ip,
                packet_no=pkt_no,
                body=body,
                sealed=sealed,
                has_attachments=bool(attachments),
                status="sent",
            )

        return pkt_no

    def send_read_notice(self, *, target_ip: str, target_port: int, packet_no: str) -> None:
        raw = build_packet(
            user=self.config.resolved_user(),
            host=self.config.resolved_host(),
            command=int(Command.READMSG),
            body=packet_no,
            use_utf8=self.config.use_utf8,
        )
        self._transport.send_to(raw, target_ip, target_port, wait_ack=False)

    def download_attachment(
        self,
        *,
        host_ip: str,
        packet_no: str,
        file_id: int,
        dest_path: str | Path,
        offset: int = 0,
    ) -> Path:
        return self._files.download_file(
            host_ip=host_ip,
            packet_no=packet_no,
            file_id=file_id,
            dest_path=dest_path,
            offset=offset,
        )

    def _handle_datagram(self, dg: ReceivedDatagram) -> None:
        pkt = dg.packet
        base_cmd = pkt.base_command

        if base_cmd == int(Command.BR_ENTRY):
            self._upsert_peer(dg.sender_ip, dg.sender_port, pkt)
            reply_flags = int(Command.ANSENTRY)
            if self.is_absent:
                reply_flags |= int(Command.ABSENCEOPT)
            reply_raw = build_packet(
                user=self.config.resolved_user(),
                host=self.config.resolved_host(),
                command=reply_flags,
                body=self.config.resolved_user(),
                extra=self.config.group,
                use_utf8=self.config.use_utf8,
            )
            self._transport.send_to(reply_raw, dg.sender_ip, dg.sender_port, wait_ack=False)
            return

        if base_cmd == int(Command.ANSENTRY):
            self._upsert_peer(dg.sender_ip, dg.sender_port, pkt)
            return

        if base_cmd == int(Command.BR_ABSENCE):
            self._upsert_peer(dg.sender_ip, dg.sender_port, pkt)
            return

        if base_cmd == int(Command.BR_EXIT):
            self._remove_peer(dg.sender_ip)
            return

        if base_cmd == int(Command.READMSG):
            if self.history is not None:
                self.history.mark_read_by_packet_no(pkt.body.strip(), peer_ip=dg.sender_ip)
            return

        if base_cmd == int(Command.SENDMSG):
            self._upsert_peer(dg.sender_ip, dg.sender_port, pkt, touch_only=True)

            if pkt.wants_recv_ack:
                ack_raw = build_packet(
                    user=self.config.resolved_user(),
                    host=self.config.resolved_host(),
                    command=int(Command.RECVMSG),
                    body=pkt.packet_no,
                    use_utf8=self.config.use_utf8,
                )
                self._transport.send_to(ack_raw, dg.sender_ip, dg.sender_port, wait_ack=False)

            attachments = parse_file_attach_string(pkt.extra) if pkt.has_file_attach else []
            peer_group = self._get_peer_group(dg.sender_ip)

            if self.history is not None:
                self.history.record_message(
                    direction="received",
                    peer_user=pkt.user,
                    peer_host=pkt.host,
                    peer_ip=dg.sender_ip,
                    packet_no=pkt.packet_no,
                    body=pkt.body,
                    sealed=pkt.is_secret,
                    has_attachments=bool(attachments),
                    status="auto_reply" if pkt.is_auto_reply else "received",
                )

            if self._on_message_received is not None:
                self._on_message_received(
                    IncomingMessageEvent(
                        sender_ip=dg.sender_ip,
                        sender_port=dg.sender_port,
                        packet_no=pkt.packet_no,
                        user=pkt.user,
                        host=pkt.host,
                        group=peer_group,
                        body=pkt.body,
                        sealed=pkt.is_secret,
                        auto_reply=pkt.is_auto_reply,
                        wants_read_notice=pkt.wants_read_notice,
                        attachments=attachments,
                    )
                )

            if self.is_absent and not pkt.is_auto_reply and self.config.absence_reply:
                self._maybe_send_auto_reply(dg.sender_ip, dg.sender_port)

    def _maybe_send_auto_reply(self, target_ip: str, target_port: int) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last_auto_reply_at.get(target_ip, 0.0)
            if now - last < self.config.auto_reply_cooldown_sec:
                return
            self._last_auto_reply_at[target_ip] = now

        flags = int(Command.SENDMSG) | int(Command.AUTORETOPT)
        raw = build_packet(
            user=self.config.resolved_user(),
            host=self.config.resolved_host(),
            command=flags,
            body=self.config.absence_reply,
            use_utf8=self.config.use_utf8,
        )
        self._transport.send_to(raw, target_ip, target_port, wait_ack=False)

    def _get_peer_group(self, ip: str) -> str:
        with self._lock:
            peer = self._peers.get(ip)
            return peer.group if peer else ""

    def _upsert_peer(
        self,
        ip: str,
        port: int,
        pkt: Packet,
        *,
        touch_only: bool = False,
    ) -> None:
        with self._lock:
            existing = self._peers.get(ip)
            if touch_only and existing is not None:
                existing.last_seen = time.monotonic()
                return

            user = pkt.user or (existing.user if existing else ip)
            host = pkt.host or (existing.host if existing else "")
            group = pkt.extra if pkt.extra else (existing.group if existing else "")
            absent = (
                bool(pkt.command & int(Command.ABSENCEOPT))
                if pkt.base_command in (int(Command.BR_ENTRY), int(Command.ANSENTRY), int(Command.BR_ABSENCE))
                else (existing.absent if existing else False)
            )
            self._peers[ip] = PeerInfo(
                ip=ip,
                port=port,
                user=user,
                host=host,
                group=group,
                absent=absent,
                last_seen=time.monotonic(),
            )
            snapshot = self._sorted_peers_locked()

        if self._on_peers_changed is not None:
            self._on_peers_changed(snapshot)

    def _remove_peer(self, ip: str) -> None:
        with self._lock:
            removed = self._peers.pop(ip, None)
            if removed is None:
                return
            snapshot = self._sorted_peers_locked()

        if self._on_peers_changed is not None:
            self._on_peers_changed(snapshot)

    def _sorted_peers_locked(self) -> list[PeerInfo]:
        return sorted(
            self._peers.values(),
            key=lambda p: (p.user.lower(), p.host.lower(), p.ip),
        )