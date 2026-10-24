from __future__ import annotations

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


def test_policy_rejects_sensitive_and_technical_content():
    assert is_publishable("今天海边风很舒服")[0]
    assert not is_publishable("把你的 token 发我")[0]
    assert not is_publishable("帮我写一段代码")[0]
    assert content_fingerprint("A") == content_fingerprint("a")

