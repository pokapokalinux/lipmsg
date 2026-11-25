from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import logging
import socket
import threading
import time
from typing import Callable

from .protocol import Command, Packet, parse_packet

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReceivedDatagram:
    packet: Packet
    sender_ip: str
    sender_port: int
    raw: bytes


@dataclass
class _PendingRetry:
    key: tuple[str, int, str]
    datagram: bytes
    target_ip: str
    target_port: int
    next_retry_at: float
    attempts_left: int
    retry_interval: float


class UdpTransport:
    """UDP送受信・RECVMSG ACK・再送タイマー・重複排除を担当するトランスポート"""

    def __init__(
        self,
        *,
        bind_ip: str = "0.0.0.0",
        port: int = 2425,
        broadcast_ip: str = "255.255.255.255",
        on_packet: Callable[[ReceivedDatagram], None] | None = None,
        retry_interval: float = 1.5,
        max_retries: int = 3,
        seen_cache_size: int = 2048,
    ) -> None:
        self.bind_ip = bind_ip
        self.port = port
        self.broadcast_ip = broadcast_ip
        self._on_packet = on_packet
        self._retry_interval = retry_interval
        self._max_retries = max_retries
        self._seen_cache_size = seen_cache_size

        self._sock: socket.socket | None = None
        self._stop_event = threading.Event()
        self._recv_thread: threading.Thread | None = None
        self._retry_thread: threading.Thread | None = None

        self._lock = threading.Lock()
        self._pending: dict[tuple[str, int, str], _PendingRetry] = {}
        self._seen_packets: OrderedDict[tuple[str, str], None] = OrderedDict()

    def start(self) -> None:
        if self._sock is not None:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        sock.settimeout(0.25)
        sock.bind((self.bind_ip, self.port))

        self._stop_event.clear()
        self._sock = sock

        self._recv_thread = threading.Thread(
            target=self._recv_loop, name="ipmsg-udp-recv", daemon=True
        )
        self._retry_thread = threading.Thread(
            target=self._retry_loop, name="ipmsg-udp-retry", daemon=True
        )
        self._recv_thread.start()
        self._retry_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

        for t in (self._recv_thread, self._retry_thread):
            if t is not None and t.is_alive():
                t.join(timeout=1.5)
        self._recv_thread = None
        self._retry_thread = None

        with self._lock:
            self._pending.clear()

    def send_to(
        self,
        raw: bytes,
        target_ip: str,
        target_port: int | None = None,
        *,
        packet_no: str | None = None,
        wait_ack: bool = False,
    ) -> None:
        port = target_port or self.port
        sock = self._sock
        if sock is None:
            raise RuntimeError("UdpTransport is not started")

        sock.sendto(raw, (target_ip, port))

        if wait_ack and packet_no:
            key = (target_ip, port, packet_no)
            item = _PendingRetry(
                key=key,
                datagram=raw,
                target_ip=target_ip,
                target_port=port,
                next_retry_at=time.monotonic() + self._retry_interval,
                attempts_left=self._max_retries,
                retry_interval=self._retry_interval,
            )
            with self._lock:
                self._pending[key] = item

    def broadcast(self, raw: bytes, broadcast_ip: str | None = None) -> None:
        dest = broadcast_ip or self.broadcast_ip
        self.send_to(raw, dest, self.port, wait_ack=False)

    def acknowledge_packet_no(self, sender_ip: str, sender_port: int, ack_packet_no: str) -> None:
        key = (sender_ip, sender_port, ack_packet_no)
        with self._lock:
            self._pending.pop(key, None)

    def _is_duplicate_and_record(self, sender_ip: str, packet_no: str) -> bool:
        dedup_key = (sender_ip, packet_no)
        with self._lock:
            if dedup_key in self._seen_packets:
                self._seen_packets.move_to_end(dedup_key)
                return True
            self._seen_packets[dedup_key] = None
            while len(self._seen_packets) > self._seen_cache_size:
                self._seen_packets.popitem(last=False)
        return False

    def _recv_loop(self) -> None:
        while not self._stop_event.is_set():
            sock = self._sock
            if sock is None:
                break
            try:
                raw, addr = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                logger.debug("Socket receive error", exc_info=True)
                continue

            sender_ip, sender_port = addr[0], int(addr[1])
            try:
                pkt = parse_packet(raw)
            except ValueError as exc:
                logger.debug("Ignored invalid packet from %s:%d: %s", sender_ip, sender_port, exc)
                continue

            if pkt.base_command == int(Command.RECVMSG):
                self.acknowledge_packet_no(sender_ip, sender_port, pkt.body.strip())
                continue

            if self._is_duplicate_and_record(sender_ip, pkt.packet_no):
                continue

            if self._on_packet is not None:
                self._on_packet(
                    ReceivedDatagram(
                        packet=pkt,
                        sender_ip=sender_ip,
                        sender_port=sender_port,
                        raw=raw,
                    )
                )

    def _retry_loop(self) -> None:
        while not self._stop_event.is_set():
            time.sleep(0.25)
            now = time.monotonic()
            to_resend: list[_PendingRetry] = []

            with self._lock:
                expired_keys: list[tuple[str, int, str]] = []
                for key, item in self._pending.items():
                    if now >= item.next_retry_at:
                        if item.attempts_left > 0:
                            item.attempts_left -= 1
                            item.next_retry_at = now + item.retry_interval
                            to_resend.append(item)
                        else:
                            expired_keys.append(key)
                for key in expired_keys:
                    self._pending.pop(key, None)

            sock = self._sock
            if sock is None:
                continue
            for item in to_resend:
                try:
                    sock.sendto(item.datagram, (item.target_ip, item.target_port))
                except OSError:
                    logger.debug("Retry send failed to %s:%d", item.target_ip, item.target_port)