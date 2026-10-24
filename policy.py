from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone


_SENSITIVE = re.compile(r"(?:api[_ -]?key|token|cookie|密码|私钥|服务器|ssh|登录码)", re.I)
_TECHNICAL = re.compile(r"(?:代码|脚本|sql|调试|配置|数学题|证明|算法|部署|报错)", re.I)


def normalize_text(value: object, limit: int = 1200) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit].strip()


def content_fingerprint(*parts: object) -> str:
    normalized = "\n".join(normalize_text(part, 400) for part in parts)
    return hashlib.sha256(normalized.casefold().encode("utf-8")).hexdigest()


def is_publishable(text: str) -> tuple[bool, str]:
    text = normalize_text(text)
    if not text:
        return False, "empty"
    if _SENSITIVE.search(text):
        return False, "sensitive"
    if _TECHNICAL.search(text):
        return False, "technical_boundary"
    return True, "ok"


def day_start(now: float | None = None) -> float:
    current = datetime.fromtimestamp(now or datetime.now().timestamp(), tz=timezone.utc)
    return current.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()

