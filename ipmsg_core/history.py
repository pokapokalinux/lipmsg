from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
import time


@dataclass(frozen=True)
class HistoryEntry:
    id: int
    timestamp: float
    direction: str
    peer_user: str
    peer_host: str
    peer_ip: str
    packet_no: str
    body: str
    sealed: bool
    has_attachments: bool
    status: str


class HistoryStore:
    """SQLite ベースの送受信履歴ストア"""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._db_path = str(db_path)
        if self._db_path != ":memory:":
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    direction TEXT NOT NULL,
                    peer_user TEXT NOT NULL,
                    peer_host TEXT NOT NULL,
                    peer_ip TEXT NOT NULL,
                    packet_no TEXT NOT NULL,
                    body TEXT NOT NULL,
                    sealed INTEGER NOT NULL DEFAULT 0,
                    has_attachments INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'sent'
                )
                """
            )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_packet_no ON messages(packet_no)"
            )
            self._conn.commit()

    def record_message(
        self,
        *,
        direction: str,
        peer_user: str,
        peer_host: str,
        peer_ip: str,
        packet_no: str,
        body: str,
        sealed: bool = False,
        has_attachments: bool = False,
        status: str = "sent",
        timestamp: float | None = None,
    ) -> int:
        ts = time.time() if timestamp is None else timestamp
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages (
                    timestamp, direction, peer_user, peer_host, peer_ip,
                    packet_no, body, sealed, has_attachments, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    direction,
                    peer_user,
                    peer_host,
                    peer_ip,
                    packet_no,
                    body,
                    1 if sealed else 0,
                    1 if has_attachments else 0,
                    status,
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def mark_read_by_packet_no(self, packet_no: str, peer_ip: str | None = None) -> int:
        with self._lock:
            if peer_ip:
                cur = self._conn.execute(
                    "UPDATE messages SET status = 'read' WHERE packet_no = ? AND peer_ip = ?",
                    (packet_no, peer_ip),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE messages SET status = 'read' WHERE packet_no = ?",
                    (packet_no,),
                )
            self._conn.commit()
            return int(cur.rowcount)

    def list_entries(self, *, query: str = "", limit: int = 200) -> list[HistoryEntry]:
        q = query.strip()
        with self._lock:
            if q:
                like = f"%{q}%"
                rows = self._conn.execute(
                    """
                    SELECT * FROM messages
                    WHERE body LIKE ? OR peer_user LIKE ? OR peer_host LIKE ? OR peer_ip LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (like, like, like, like, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM messages ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()

        return [
            HistoryEntry(
                id=int(r["id"]),
                timestamp=float(r["timestamp"]),
                direction=str(r["direction"]),
                peer_user=str(r["peer_user"]),
                peer_host=str(r["peer_host"]),
                peer_ip=str(r["peer_ip"]),
                packet_no=str(r["packet_no"]),
                body=str(r["body"]),
                sealed=bool(r["sealed"]),
                has_attachments=bool(r["has_attachments"]),
                status=str(r["status"]),
            )
            for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()