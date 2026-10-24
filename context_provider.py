from __future__ import annotations

import base64
import binascii
import time
from pathlib import Path
from typing import Any

try:
    from .policy import normalize_text
except ImportError:  # pragma: no cover - direct Runner fallback
    from policy import normalize_text


class PersonaContextProvider:
    """Read the existing life and identity plugins through public API calls."""

    def __init__(
        self,
        ctx: Any,
        *,
        life_api_name: str,
        identity_api_name: str,
    ) -> None:
        self.ctx = ctx
        self.life_api_name = life_api_name.strip()
        self.identity_api_name = identity_api_name.strip()
        self._life_cache = ""
        self._life_cache_at = 0.0
        self._identity_cache: bytes | None = None
        self._identity_checked_at = 0.0

    async def life_context(self) -> str:
        now = time.monotonic()
        if self._life_cache and now - self._life_cache_at < 120:
            return self._life_cache
        if self.life_api_name:
            try:
                response = await self.ctx.api.call(self.life_api_name, version="1")
                text = _extract_context_text(response)
                if text:
                    self._life_cache = normalize_text(text, 600)
                    self._life_cache_at = now
                    return self._life_cache
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.debug("生活轨迹 API 暂不可用：%s", type(exc).__name__)
        return "当前时间为" + time.strftime("%H:%M") + "，请只依据已有设定自然表达。"

    async def identity_reference(self) -> bytes | None:
        now = time.monotonic()
        if self._identity_cache is not None:
            return self._identity_cache
        if now - self._identity_checked_at < 120:
            return None
        self._identity_checked_at = now
        if not self.identity_api_name:
            return None
        try:
            response = await self.ctx.api.call(self.identity_api_name, version="1")
            image = _extract_image_bytes(response)
            if image:
                self._identity_cache = image
            return image
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.debug("身份参考图 API 暂不可用：%s", type(exc).__name__)
            return None


def _extract_context_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("context", "life_context", "status", "summary", "text", "message"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        for key in ("result", "data"):
            if key in value:
                nested = _extract_context_text(value[key])
                if nested:
                    return nested
    return ""


def _extract_image_bytes(value: Any) -> bytes | None:
    if isinstance(value, dict):
        for key in ("base64", "image_base64", "data", "image"):
            if key in value:
                result = _extract_image_bytes(value[key])
                if result:
                    return result
        for key in ("result", "payload"):
            if key in value:
                result = _extract_image_bytes(value[key])
                if result:
                    return result
        path = value.get("path") or value.get("file_path")
        if isinstance(path, str):
            return _read_image_path(path)
    elif isinstance(value, list):
        for item in value:
            result = _extract_image_bytes(item)
            if result:
                return result
    elif isinstance(value, str):
        candidate = value.strip()
        if candidate.startswith("data:image/") and "," in candidate:
            candidate = candidate.split(",", 1)[1]
        try:
            decoded = base64.b64decode(candidate, validate=True)
        except (ValueError, binascii.Error):
            return None
        if 32 <= len(decoded) <= 12 * 1024 * 1024:
            return decoded
    return None


def _read_image_path(path: str) -> bytes | None:
    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.stat().st_size > 12 * 1024 * 1024:
            return None
        return candidate.read_bytes()
    except OSError:
        return None
