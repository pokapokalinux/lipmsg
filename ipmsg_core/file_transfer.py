from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import socket
import threading

_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


@dataclass(frozen=True)
class FileAttachmentSpec:
    file_id: int
    name: str
    size: int
    mtime: int = 0
    attr: int = 1
    local_path: Path | None = None


def sanitize_filename(name: str) -> str:
    base = Path(name).name.strip()
    cleaned = _INVALID_FILENAME_CHARS.sub("_", base).strip(". ")
    return cleaned or "attachment.bin"


def build_file_attach_spec(file_id: int, path: str | Path) -> FileAttachmentSpec:
    p = Path(path)
    st = p.stat()
    return FileAttachmentSpec(
        file_id=file_id,
        name=sanitize_filename(p.name),
        size=int(st.st_size),
        mtime=int(st.st_mtime),
        attr=1,
        local_path=p.resolve(),
    )


def build_file_attach_string(specs: list[FileAttachmentSpec]) -> str:
    entries: list[str] = []
    for spec in specs:
        safe_name = sanitize_filename(spec.name).replace(":", "_")
        size_hex = format(max(0, spec.size), "x")
        mtime_hex = format(max(0, spec.mtime), "x")
        attr_hex = format(max(0, spec.attr), "x")
        entries.append(f"{spec.file_id}:{safe_name}:{size_hex}:{mtime_hex}:{attr_hex}:")
    return "\a".join(entries)


def parse_file_attach_string(raw: str) -> list[FileAttachmentSpec]:
    if not raw.strip():
        return []
    result: list[FileAttachmentSpec] = []
    for chunk in raw.split("\a"):
        part = chunk.strip()
        if not part:
            continue
        fields = part.split(":")
        if len(fields) < 3:
            continue
        try:
            file_id = int(fields[0], 0)
            name = sanitize_filename(fields[1])
            size = int(fields[2], 16) if fields[2] else 0
            mtime = int(fields[3], 16) if len(fields) > 3 and fields[3] else 0
            attr = int(fields[4], 16) if len(fields) > 4 and fields[4] else 1
        except ValueError:
            continue
        result.append(
            FileAttachmentSpec(
                file_id=file_id,
                name=name,
                size=size,
                mtime=mtime,
                attr=attr,
            )
        )
    return result


class FileTransferManager:
    """送信ファイル登録とTCPダウンロード要求の管理"""

    def __init__(self, *, port: int = 2425, bind_ip: str = "0.0.0.0") -> None:
        self.port = port
        self.bind_ip = bind_ip
        self._lock = threading.Lock()
        self._offers: dict[tuple[str, str, int], FileAttachmentSpec] = {}
        self._server_sock: socket.socket | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def register_files(
        self,
        *,
        sender_user: str,
        packet_no: str,
        paths: list[str | Path],
    ) -> list[FileAttachmentSpec]:
        specs: list[FileAttachmentSpec] = []
        with self._lock:
            for idx, p in enumerate(paths):
                spec = build_file_attach_spec(idx, p)
                specs.append(spec)
                self._offers[(sender_user, packet_no, spec.file_id)] = spec
        return specs

    def start_server(self) -> None:
        if self._server_sock is not None:
            return
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.3)
        sock.bind((self.bind_ip, self.port))
        sock.listen(16)

        self._stop_event.clear()
        self._server_sock = sock
        self._thread = threading.Thread(
            target=self._accept_loop, name="ipmsg-tcp-filesrv", daemon=True
        )
        self._thread.start()

    def stop_server(self) -> None:
        self._stop_event.set()
        sock = self._server_sock
        self._server_sock = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        self._thread = None

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            sock = self._server_sock
            if sock is None:
                break
            try:
                conn, _addr = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                if self._stop_event.is_set():
                    break
                continue
            threading.Thread(
                target=self._handle_client, args=(conn,), daemon=True
            ).start()

    def _handle_client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(5.0)
            buf = bytearray()
            while b"\n" not in buf and len(buf) < 4096:
                chunk = conn.recv(1024)
                if not chunk:
                    break
                buf.extend(chunk)

            line = buf.decode("utf-8", errors="replace").strip()
            parts = line.split(":")
            if len(parts) < 4 or parts[0] != "GET":
                return

            packet_no = parts[1]
            try:
                file_id = int(parts[2], 0)
                offset = int(parts[3], 16)
            except ValueError:
                return

            spec = self._find_offer(packet_no, file_id)
            if spec is None or spec.local_path is None or not spec.local_path.is_file():
                return

            with spec.local_path.open("rb") as fp:
                fp.seek(max(0, offset))
                while True:
                    data = fp.read(65536)
                    if not data:
                        break
                    conn.sendall(data)

    def _find_offer(self, packet_no: str, file_id: int) -> FileAttachmentSpec | None:
        with self._lock:
            for (_u, pkt_no, fid), spec in self._offers.items():
                if pkt_no == packet_no and fid == file_id:
                    return spec
        return None

    def download_file(
        self,
        *,
        host_ip: str,
        packet_no: str,
        file_id: int,
        dest_path: str | Path,
        offset: int = 0,
        timeout: float = 10.0,
    ) -> Path:
        out_path = Path(dest_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = out_path.with_name(f".{out_path.name}.part.{os.getpid()}")

        req = f"GET:{packet_no}:{file_id}:{format(max(0, offset), 'x')}\n".encode("utf-8")
        try:
            with socket.create_connection((host_ip, self.port), timeout=timeout) as sock:
                sock.settimeout(timeout)
                sock.sendall(req)
                mode = "ab" if offset > 0 and tmp_path.exists() else "wb"
                with tmp_path.open(mode) as fp:
                    while True:
                        chunk = sock.recv(65536)
                        if not chunk:
                            break
                        fp.write(chunk)
            os.replace(tmp_path, out_path)
        except Exception:
            if tmp_path.exists() and offset == 0:
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise
        return out_path