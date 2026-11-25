from __future__ import annotations

from dataclasses import dataclass
from enum import IntFlag
import time
import uuid


class Command(IntFlag):
    """IP Messenger コマンド番号（下位8ビット）およびオプションフラグ"""

    CMD_MASK = 0x000000FF

    # 基本コマンド
    NOPOST = 0x00000000
    BR_ENTRY = 0x00000001
    BR_EXIT = 0x00000002
    ANSENTRY = 0x00000003
    BR_ABSENCE = 0x00000004

    BR_ISGETLIST = 0x00000010
    BR_OKGETLIST = 0x00000011
    BR_GETLIST = 0x00000012
    BR_ANSLIST = 0x00000013
    BR_ISGETLIST2 = 0x00000018

    SENDMSG = 0x00000020
    RECVMSG = 0x00000021
    READMSG = 0x00000030
    DELMSG = 0x00000031
    ANSREADMSG = 0x00000032

    GETINFO = 0x00000040
    SENDINFO = 0x00000041

    GETABSENCEINFO = 0x00000050
    SENDABSENCEINFO = 0x00000051

    GETFILEDATA = 0x00000060
    RELEASEFILES = 0x00000061
    GETDIRFILES = 0x00000062

    GETPUBKEY = 0x00000072
    ANSPUBKEY = 0x00000073

    # オプションフラグ
    ABSENCEOPT = 0x00000100
    SENDCHECKOPT = 0x00000100
    NOGROUPOPT = 0x00000200
    BROADCASTOPT = 0x00000400
    SECRETOPT = 0x00000800
    READCHECKOPT = 0x00001000
    AUTORETOPT = 0x00002000
    RETRYOPT = 0x00004000
    PASSWORDOPT = 0x00008000
    NOADDLISTOPT = 0x00020000
    ENCRYPTOPT = 0x00040000
    FILEATTACHOPT = 0x00200000
    UTF8OPT = 0x00800000


@dataclass(frozen=True)
class Packet:
    version: str
    packet_no: str
    user: str
    host: str
    command: int
    body: str
    extra: str = ""

    @property
    def base_command(self) -> int:
        return self.command & int(Command.CMD_MASK)

    def has_flag(self, flag: Command) -> bool:
        return bool(self.command & int(flag))

    @property
    def is_utf8(self) -> bool:
        return self.has_flag(Command.UTF8OPT)

    @property
    def is_secret(self) -> bool:
        return self.base_command == int(Command.SENDMSG) and self.has_flag(Command.SECRETOPT)

    @property
    def wants_recv_ack(self) -> bool:
        return self.base_command == int(Command.SENDMSG) and self.has_flag(Command.SENDCHECKOPT)

    @property
    def wants_read_notice(self) -> bool:
        return self.base_command == int(Command.SENDMSG) and self.has_flag(Command.READCHECKOPT)

    @property
    def is_auto_reply(self) -> bool:
        return self.base_command == int(Command.SENDMSG) and self.has_flag(Command.AUTORETOPT)

    @property
    def has_file_attach(self) -> bool:
        return self.base_command == int(Command.SENDMSG) and self.has_flag(Command.FILEATTACHOPT)


def generate_packet_no() -> str:
    ms = int(time.time() * 1000) & 0x7FFFFFFF
    rand_bits = uuid.uuid4().int & 0x3FF
    return str((ms << 10) | rand_bits)


def _sanitize_field(value: str) -> str:
    return value.replace(":", "_").replace("\x00", "")


def _decode_text(raw: bytes, use_utf8: bool) -> str:
    if use_utf8:
        return raw.decode("utf-8", errors="replace")
    for enc in ("cp932", "utf-8"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _encode_text(text: str, use_utf8: bool) -> bytes:
    if use_utf8:
        return text.encode("utf-8", errors="replace")
    try:
        return text.encode("cp932")
    except UnicodeEncodeError:
        return text.encode("utf-8", errors="replace")


def build_packet(
    *,
    user: str,
    host: str,
    command: int,
    body: str = "",
    extra: str = "",
    packet_no: str | None = None,
    version: str = "1",
    use_utf8: bool = True,
) -> bytes:
    cmd_val = int(command)
    if use_utf8:
        cmd_val |= int(Command.UTF8OPT)

    pkt_no = packet_no or generate_packet_no()
    header = (
        f"{_sanitize_field(version)}:"
        f"{_sanitize_field(pkt_no)}:"
        f"{_sanitize_field(user)}:"
        f"{_sanitize_field(host)}:"
        f"{cmd_val}:"
    ).encode("ascii")

    body_bytes = _encode_text(body, use_utf8=bool(cmd_val & int(Command.UTF8OPT)))
    if extra:
        extra_bytes = _encode_text(extra, use_utf8=bool(cmd_val & int(Command.UTF8OPT)))
        return header + body_bytes + b"\x00" + extra_bytes
    return header + body_bytes


def parse_packet(raw: bytes) -> Packet:
    if not raw:
        raise ValueError("Empty packet data")

    parts = raw.split(b":", 5)
    if len(parts) < 6:
        raise ValueError("Malformed IPMsg packet header")

    version_b, pkt_no_b, user_b, host_b, cmd_b, payload_b = parts

    try:
        version = version_b.decode("ascii")
        packet_no = pkt_no_b.decode("ascii")
        user = user_b.decode("utf-8", errors="replace")
        host = host_b.decode("utf-8", errors="replace")
        command = int(cmd_b.decode("ascii").strip(), 0)
    except Exception as exc:
        raise ValueError("Invalid header fields in IPMsg packet") from exc

    use_utf8 = bool(command & int(Command.UTF8OPT))
    if b"\x00" in payload_b:
        body_b, extra_b = payload_b.split(b"\x00", 1)
    else:
        body_b, extra_b = payload_b, b""

    body = _decode_text(body_b, use_utf8=use_utf8)
    extra = _decode_text(extra_b, use_utf8=use_utf8) if extra_b else ""

    return Packet(
        version=version,
        packet_no=packet_no,
        user=user,
        host=host,
        command=command,
        body=body,
        extra=extra,
    )