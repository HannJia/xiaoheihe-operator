from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class OutgoingStatus(StrEnum):
    PLANNED = "planned"
    DRY_RUN = "dry_run"
    ACCEPTED = "accepted"
    SENDING = "sending"
    VERIFIED = "verified"
    SEND_UNKNOWN = "send_unknown"
    FAILED = "failed"
    SUPPRESSED = "suppressed"


@dataclass(slots=True)
class OutgoingAction:
    action_key: str
    kind: str
    target_id: str
    content: str
    status: OutgoingStatus = OutgoingStatus.PLANNED
    external_id: str = ""
    error: str = ""
    request_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class IncomingEvent:
    event_key: str
    kind: str
    post_id: str
    comment_id: str = ""
    root_comment_id: str = ""
    sender_id: str = ""
    sender_name: str = ""
    text: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ContentDraft:
    title: str
    body: str
    topic_ids: list[str] = field(default_factory=list)
    community_ids: list[str] = field(default_factory=list)
    image_request_id: str = ""
    image_bytes: list[bytes] = field(default_factory=list)

