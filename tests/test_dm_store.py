from __future__ import annotations

from pathlib import Path

from dm_store import DirectMessageStore
from vendor_xiaoheihe.models import DirectMessage


def test_dm_store_is_idempotent_and_keeps_baseline_cursor(tmp_path: Path):
    store = DirectMessageStore(tmp_path / "state.db")
    message = DirectMessage(
        profile_id="default",
        message_id="dm-1",
        conversation_id="dm!100",
        sender_uid="100",
        sender_name="大号",
        text="你好",
        created_at=1700000000,
        incoming=True,
        peer_uid="100",
        raw={"msg_id": "dm-1", "content": "你好"},
    )
    assert store.insert_message(message, baseline=True)
    assert not store.insert_message(message, baseline=True)
    store.set_cursor("default", "20", baseline_ready=True)
    cursor, ready = store.get_cursor("default")
    assert (cursor, ready) == ("20", True)
    assert store.summary("default")["message_count"] == 1
    assert store.list_recent("default")[0]["text"] == "你好"
    store.close()
