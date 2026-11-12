"""Persist the exact reviewed news draft until its owner explicitly approves it."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

try:
    from .daily_news import NewsDraft
except ImportError:
    from daily_news import NewsDraft

MAX_BYTES = 20 * 1024 * 1024


def packet(draft: NewsDraft, *, owner: str, profile: str, target: str, hashtags: list[str]) -> dict:
    return {"version": 1, "owner": owner, "profile": profile, "target": target, "hashtags": hashtags,
            "draft": {"title": draft.title, "blocks": draft.blocks, "items": draft.items,
                "dropped": draft.dropped, "notes": draft.notes, "image_note": draft.image_note,
                "image_sources": draft.image_sources, "verification": draft.verification,
                "image_base64": base64.b64encode(draft.image_bytes).decode("ascii") if draft.image_bytes else ""}}


def encoded(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: dict) -> str:
    return hashlib.sha256(encoded(value)).hexdigest()


def save(directory: Path, value: dict) -> str:
    raw = encoded(value)
    if len(raw) > MAX_BYTES:
        raise ValueError("approval_draft_too_large")
    checksum = hashlib.sha256(raw).hexdigest()
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / (uuid.uuid4().hex + ".tmp")
    try:
        temporary.write_bytes(raw)
        temporary.replace(directory / (checksum + ".json"))
    finally:
        temporary.unlink(missing_ok=True)
    return checksum


def load(directory: Path, checksum: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise ValueError("invalid_approval_digest")
    path = directory / (checksum + ".json")
    if path.is_symlink() or path.stat().st_size > MAX_BYTES:
        raise ValueError("invalid_approval_file")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != checksum:
        raise ValueError("approval_draft_changed")
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1 or digest(value) != checksum:
        raise ValueError("invalid_approval_packet")
    return value


def restore(value: dict) -> NewsDraft:
    data = dict(value["draft"])
    image = data.pop("image_base64")
    return NewsDraft(**data, image_bytes=base64.b64decode(image, validate=True) if image else None)


def message_identity(message: Any) -> tuple[str, str, str, str, float]:
    """Use the host's message envelope; never derive authority from message text."""
    def get(obj, key, default=None):
        return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)
    info = get(message, "message_info", {})
    user = get(info, "user_info", {})
    group = get(info, "group_info", {})
    platform = str(get(message, "platform", "") or get(info, "platform", ""))
    owner = str(get(user, "user_id", ""))
    additional = get(info, "additional_config", {})
    if get(additional, "relayed_by_plugin", False):
        owner = ""
    group_id = str(get(group, "group_id", "") or ("unknown_group" if group else ""))
    text = str(get(message, "processed_plain_text", "") or "").strip()
    try:
        timestamp = float(get(info, "time", 0) or 0)
    except (ValueError, TypeError):
        timestamp = 0
    return platform, owner, group_id, text, timestamp
