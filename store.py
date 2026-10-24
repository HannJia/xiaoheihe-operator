from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

try:
    from .models import OutgoingAction, OutgoingStatus
except ImportError:  # pragma: no cover - direct Runner fallback
    from models import OutgoingAction, OutgoingStatus


class OperatorStore:
    """小黑盒插件自己的 SQLite 状态，不触碰 MaiBot 主库。"""

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
            CREATE TABLE IF NOT EXISTS cursors (
                profile_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                cursor TEXT NOT NULL DEFAULT '',
                baseline_ready INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (profile_id, kind)
            );
            CREATE TABLE IF NOT EXISTS incoming_events (
                event_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at REAL NOT NULL,
                claimed_at REAL
            );
            CREATE TABLE IF NOT EXISTS outgoing_actions (
                action_key TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                content TEXT NOT NULL,
                status TEXT NOT NULL,
                external_id TEXT NOT NULL DEFAULT '',
                request_id TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_outgoing_status ON outgoing_actions(status);
            CREATE INDEX IF NOT EXISTS idx_outgoing_created ON outgoing_actions(created_at);
            """
        )
        self._conn.commit()

    def get_cursor(self, profile_id: str, kind: str) -> tuple[str, bool]:
        row = self._conn.execute(
            "SELECT cursor, baseline_ready FROM cursors WHERE profile_id=? AND kind=?",
            (profile_id, kind),
        ).fetchone()
        return (str(row["cursor"]), bool(row["baseline_ready"])) if row else ("", False)

    def set_cursor(self, profile_id: str, kind: str, cursor: str, *, baseline_ready: bool) -> None:
        self._conn.execute(
            """
            INSERT INTO cursors(profile_id, kind, cursor, baseline_ready, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(profile_id, kind) DO UPDATE SET
              cursor=excluded.cursor,
              baseline_ready=excluded.baseline_ready,
              updated_at=excluded.updated_at
            """,
            (profile_id, kind, str(cursor), int(baseline_ready), time.time()),
        )
        self._conn.commit()

    def claim_incoming(self, event_key: str, kind: str, payload: dict[str, Any]) -> bool:
        now = time.time()
        try:
            self._conn.execute(
                "INSERT INTO incoming_events(event_key, kind, payload_json, created_at, claimed_at) VALUES (?, ?, ?, ?, ?)",
                (event_key, kind, json.dumps(payload, ensure_ascii=False), now, now),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def begin_outgoing(self, action: OutgoingAction) -> bool:
        now = time.time()
        try:
            self._conn.execute(
                """
                INSERT INTO outgoing_actions(
                  action_key, kind, target_id, content, status, external_id,
                  request_id, error, metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    action.action_key,
                    action.kind,
                    action.target_id,
                    action.content,
                    action.status.value,
                    action.external_id,
                    action.request_id,
                    action.error,
                    json.dumps(action.metadata, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            existing = self._conn.execute(
                "SELECT status, external_id FROM outgoing_actions WHERE action_key=?",
                (action.action_key,),
            ).fetchone()
            if existing and existing[0] == OutgoingStatus.FAILED.value and not str(existing[1] or ""):
                self._conn.execute(
                    "UPDATE outgoing_actions SET status=?, error='', updated_at=? WHERE action_key=?",
                    (action.status.value, now, action.action_key),
                )
                self._conn.commit()
                return True
            return False

    def update_outgoing(
        self,
        action_key: str,
        status: OutgoingStatus,
        *,
        external_id: str = "",
        request_id: str = "",
        error: str = "",
    ) -> None:
        self._conn.execute(
            """
            UPDATE outgoing_actions
            SET status=?, external_id=COALESCE(NULLIF(?, ''), external_id),
                request_id=COALESCE(NULLIF(?, ''), request_id), error=?, updated_at=?
            WHERE action_key=?
            """,
            (status.value, external_id, request_id, error[:500], time.time(), action_key),
        )
        self._conn.commit()

    def count_since(self, kind: str, since: float) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM outgoing_actions WHERE kind=? AND created_at>=? AND status NOT IN (?, ?)",
            (kind, since, OutgoingStatus.SUPPRESSED.value, OutgoingStatus.FAILED.value),
        ).fetchone()
        return int(row["count"] if row else 0)

    def last_outgoing_at(self, kind: str | None = None) -> float | None:
        """Return the most recent non-terminal outgoing timestamp."""
        if kind:
            row = self._conn.execute(
                """
                SELECT created_at FROM outgoing_actions
                WHERE kind=? AND status NOT IN (?, ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (kind, OutgoingStatus.SUPPRESSED.value, OutgoingStatus.FAILED.value),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT created_at FROM outgoing_actions
                WHERE status NOT IN (?, ?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (OutgoingStatus.SUPPRESSED.value, OutgoingStatus.FAILED.value),
            ).fetchone()
        return float(row["created_at"]) if row else None

    def has_outgoing_key(self, action_key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM outgoing_actions WHERE action_key=? LIMIT 1",
            (action_key,),
        ).fetchone()
        return row is not None

    def list_outgoing(self, *, status: OutgoingStatus | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM outgoing_actions ORDER BY updated_at DESC LIMIT ?",
                (max(1, min(limit, 200)),),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM outgoing_actions WHERE status=? ORDER BY updated_at ASC LIMIT ?",
                (status.value, max(1, min(limit, 200))),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(str(item.pop("metadata_json", "{}")))
            except json.JSONDecodeError:
                item["metadata"] = {}
            result.append(item)
        return result

    def close(self) -> None:
        self._conn.close()

