from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from .vendor_xiaoheihe.models import DirectMessage
except ImportError:  # pragma: no cover - direct Runner fallback
    from vendor_xiaoheihe.models import DirectMessage


class DirectMessageStore:
    """Private-message state isolated from comment notifications."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS dm_cursors (
                profile_id TEXT PRIMARY KEY,
                cursor TEXT NOT NULL DEFAULT '',
                baseline_ready INTEGER NOT NULL DEFAULT 0,
                last_probe_json TEXT NOT NULL DEFAULT '{}',
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS dm_messages (
                message_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL DEFAULT '',
                peer_uid TEXT NOT NULL DEFAULT '',
                sender_uid TEXT NOT NULL DEFAULT '',
                sender_name TEXT NOT NULL DEFAULT '',
                text TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL DEFAULT 0,
                incoming INTEGER NOT NULL DEFAULT 0,
                raw_json TEXT NOT NULL DEFAULT '{}',
                observed_at REAL NOT NULL,
                baseline INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_dm_messages_profile_time
                ON dm_messages(profile_id, created_at DESC, observed_at DESC);
            CREATE INDEX IF NOT EXISTS idx_dm_messages_peer_time
                ON dm_messages(profile_id, peer_uid, created_at DESC);
            """
        )
        columns = {
            str(row[1]) for row in self._conn.execute("PRAGMA table_info(dm_cursors)")
        }
        if "last_probe_json" not in columns:
            self._conn.execute(
                "ALTER TABLE dm_cursors ADD COLUMN last_probe_json TEXT NOT NULL DEFAULT '{}'"
            )
        self._conn.commit()

    def get_cursor(self, profile_id: str) -> tuple[str, bool]:
        row = self._conn.execute(
            "SELECT cursor, baseline_ready FROM dm_cursors WHERE profile_id=?",
            (str(profile_id),),
        ).fetchone()
        return (str(row["cursor"]), bool(row["baseline_ready"])) if row else ("", False)

    def set_cursor(self, profile_id: str, cursor: str, *, baseline_ready: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO dm_cursors(profile_id, cursor, baseline_ready, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
              cursor=excluded.cursor,
              baseline_ready=excluded.baseline_ready,
              updated_at=excluded.updated_at
            """,
            (str(profile_id), str(cursor or ""), int(baseline_ready), time.time()),
        )
        self._conn.commit()

    def set_probe(self, profile_id: str, probe: dict[str, Any]) -> None:
        self._conn.execute(
            """
            INSERT INTO dm_cursors(profile_id, cursor, baseline_ready, last_probe_json, updated_at)
            VALUES (?, '', 0, ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
              last_probe_json=excluded.last_probe_json,
              updated_at=excluded.updated_at
            """,
            (
                str(profile_id),
                json.dumps(probe or {}, ensure_ascii=False, default=str),
                time.time(),
            ),
        )
        self._conn.commit()

    def insert_message(self, message: DirectMessage, *, baseline: bool = False) -> bool:
        try:
            self._conn.execute(
                """
                INSERT INTO dm_messages(
                    message_id, profile_id, conversation_id, peer_uid,
                    sender_uid, sender_name, text, created_at, incoming,
                    raw_json, observed_at, baseline
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(message.message_id),
                    str(message.profile_id),
                    str(message.conversation_id or ""),
                    str(message.peer_uid or ""),
                    str(message.sender_uid or ""),
                    str(message.sender_name or ""),
                    str(message.text or ""),
                    float(message.created_at or 0),
                    int(bool(message.incoming)),
                    json.dumps(message.raw or {}, ensure_ascii=False, default=str),
                    time.time(),
                    int(bool(baseline)),
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def list_recent(self, profile_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM dm_messages
            WHERE profile_id=?
            ORDER BY COALESCE(created_at, observed_at) DESC, observed_at DESC
            LIMIT ?
            """,
            (str(profile_id), max(1, min(int(limit), 100))),
        ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["raw"] = json.loads(str(item.pop("raw_json", "{}")))
            except json.JSONDecodeError:
                item["raw"] = {}
            result.append(item)
        return result

    def summary(self, profile_id: str) -> dict[str, Any]:
        cursor, baseline_ready = self.get_cursor(profile_id)
        row = self._conn.execute(
            "SELECT COUNT(*) AS count, MAX(observed_at) AS observed_at FROM dm_messages WHERE profile_id=?",
            (str(profile_id),),
        ).fetchone()
        cursor_row = self._conn.execute(
            "SELECT last_probe_json FROM dm_cursors WHERE profile_id=?",
            (str(profile_id),),
        ).fetchone()
        try:
            last_probe = json.loads(str(cursor_row["last_probe_json"] or "{}")) if cursor_row else {}
        except json.JSONDecodeError:
            last_probe = {}
        return {
            "cursor": cursor,
            "baseline_ready": baseline_ready,
            "message_count": int(row["count"] if row else 0),
            "last_observed_at": float(row["observed_at"] or 0) if row else 0,
            "last_probe": last_probe,
        }

    def close(self) -> None:
        self._conn.close()
