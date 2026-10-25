from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from models import OutgoingAction, OutgoingStatus
from policy import content_fingerprint, is_publishable
from store import OperatorStore


def test_store_deduplicates_incoming_and_outgoing(tmp_path: Path):
    store = OperatorStore(tmp_path / "state.db")
    assert store.claim_incoming("event-1", "reply", {"text": "hello"})
    assert not store.claim_incoming("event-1", "reply", {"text": "hello"})
    action = OutgoingAction("action-1", "reply", "post-1", "hello")
    assert store.begin_outgoing(action)
    assert not store.begin_outgoing(action)
    store.update_outgoing("action-1", OutgoingStatus.SEND_UNKNOWN, error="timeout")
    store.close()


def test_store_retries_pre_send_failure_without_external_id(tmp_path: Path):
    store = OperatorStore(tmp_path / "state.db")
    action = OutgoingAction("action-2", "proactive_post", "", "draft")
    assert store.begin_outgoing(action)
    store.update_outgoing("action-2", OutgoingStatus.FAILED, error="drawpic_task_id")
    assert store.begin_outgoing(action)
    assert store.list_outgoing(limit=1)[0]["status"] == OutgoingStatus.PLANNED.value
    store.close()


def test_dry_run_does_not_consume_daily_quota_or_send_cooldown(tmp_path: Path):
    store = OperatorStore(tmp_path / "dry-run.db")
    dry_run = OutgoingAction("dry-run-action", "notification_reply", "post", "reply")
    assert store.begin_outgoing(dry_run)
    store.update_outgoing("dry-run-action", OutgoingStatus.DRY_RUN)
    assert store.count_since("notification_reply", time.time() - 10) == 0
    assert store.last_outgoing_at() is None

    sent = OutgoingAction("sent-action", "notification_reply", "post", "reply")
    assert store.begin_outgoing(sent)
    store.update_outgoing("sent-action", OutgoingStatus.VERIFIED)
    assert store.count_since("notification_reply", time.time() - 10) == 1
    assert store.last_outgoing_at() is not None
    store.close()


def test_incoming_event_stays_pending_until_reply_is_terminal(tmp_path: Path):
    store = OperatorStore(tmp_path / "state.db")
    assert store.claim_incoming("event-pending", "reply", {"text": "hello"})
    assert store.list_pending_incoming(kind="reply", now=time.time())

    store.defer_incoming("event-pending", "daily_quota", delay_seconds=3600, increment_attempt=False)
    assert not store.list_pending_incoming(kind="reply", now=time.time())
    assert store.list_pending_incoming(kind="reply", now=time.time() + 3601)
    assert store.get_incoming("event-pending")["attempts"] == 0

    store.mark_incoming_done("event-pending")
    assert not store.list_pending_incoming(kind="reply", now=time.time() + 7200)
    assert not store.claim_incoming("event-pending", "reply", {"text": "hello"})
    store.close()


def test_incoming_event_uses_bounded_retries_then_stops(tmp_path: Path):
    store = OperatorStore(tmp_path / "retry.db")
    assert store.claim_incoming("event-retry", "reply", {"text": "hello"})
    store.defer_incoming("event-retry", "temporary", delay_seconds=1, max_attempts=2)
    row = store.get_incoming("event-retry")
    assert row["state"] == "pending"
    assert row["attempts"] == 1
    store.defer_incoming("event-retry", "temporary", delay_seconds=1, max_attempts=2)
    row = store.get_incoming("event-retry")
    assert row["state"] == "failed"
    assert row["attempts"] == 2
    assert not store.list_pending_incoming(now=time.time() + 10)
    store.close()


def test_store_migrates_recent_orphaned_claim_but_protects_outgoing_actions(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE incoming_events (
            event_key TEXT PRIMARY KEY, kind TEXT NOT NULL, payload_json TEXT NOT NULL,
            created_at REAL NOT NULL, claimed_at REAL
        );
        CREATE TABLE outgoing_actions (
            action_key TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL,
            content TEXT NOT NULL, status TEXT NOT NULL, external_id TEXT NOT NULL DEFAULT '',
            request_id TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL, updated_at REAL NOT NULL
        );
        """
    )
    now = time.time()
    conn.execute(
        "INSERT INTO incoming_events VALUES (?, ?, ?, ?, ?)",
        ("orphan", "reply", '{"text":"pending"}', now, now),
    )
    conn.execute(
        "INSERT INTO incoming_events VALUES (?, ?, ?, ?, ?)",
        ("sent", "reply", '{"text":"already handled"}', now, now),
    )
    conn.execute(
        "INSERT INTO incoming_events VALUES (?, ?, ?, ?, ?)",
        ("rejected", "reply", '{"text":"explicit upstream rejection"}', now, now),
    )
    conn.execute(
        "INSERT INTO incoming_events VALUES (?, ?, ?, ?, ?)",
        ("uncertain", "reply", '{"text":"uncertain send"}', now, now),
    )
    conn.execute(
        "INSERT INTO outgoing_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("reply:sent", "notification_reply", "post", "reply", "verified", "comment-1", "", "", "{}", now, now),
    )
    conn.execute(
        "INSERT INTO outgoing_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("reply:rejected", "notification_reply", "post", "reply", "failed", "", "", "upstream_rejected", "{}", now, now),
    )
    conn.execute(
        "INSERT INTO outgoing_actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("reply:uncertain", "notification_reply", "post", "reply", "send_unknown", "", "", "timeout", "{}", now, now),
    )
    conn.commit()
    conn.close()

    store = OperatorStore(db_path)
    pending_keys = {row["event_key"] for row in store.list_pending_incoming(now=now + 1)}
    assert pending_keys == {"orphan"}
    assert store.get_incoming("sent")["state"] == "done"
    assert store.get_incoming("rejected")["state"] == "done"
    assert store.get_incoming("uncertain")["state"] == "done"
    store.close()


def test_policy_rejects_sensitive_and_technical_content():
    assert is_publishable("今天海边风很舒服")[0]
    assert not is_publishable("把你的 token 发我")[0]
    assert not is_publishable("帮我写一段代码")[0]
    assert content_fingerprint("A") == content_fingerprint("a")

