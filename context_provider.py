from __future__ import annotations

import base64
import binascii
import time
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
        self._host_persona_cache = ""
        self._host_persona_at: float | None = None

    async def host_persona(self) -> str:
        """读宿主的昵称和人格设定，和 MaiBot 自己的回复用同一份人设。

        拼法与宿主 replyer 一致：「你的名字是{昵称}。」换行接人格。WebUI 改人设或切换
        人设方案后，宿主会把生效的人格写回 personality.personality，这里最多 60 秒后跟上。
        读不到时返回空字符串，由调用方用中性人设兜底。
        """

        now = time.monotonic()
        if self._host_persona_at is not None and now - self._host_persona_at < 60:
            return self._host_persona_cache
        nickname = personality = ""
        try:
            raw_nickname = await self.ctx.config.get("bot.nickname", "")
            raw_personality = await self.ctx.config.get("personality.personality", "")
            nickname = normalize_text(raw_nickname if isinstance(raw_nickname, str) else "", 40)
            personality = normalize_text(raw_personality if isinstance(raw_personality, str) else "", 1500)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.debug("宿主人设暂不可读：%s", type(exc).__name__)
        lines = []
        if nickname:
            lines.append(f"你的名字是{nickname}。")
        if personality:
            lines.append(personality)
        self._host_persona_cache = "\n".join(lines)
        self._host_persona_at = now
        return self._host_persona_cache

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
        # 回退：直接读「生活频率」插件的状态文件（org.orge-8.life-frequency / life_state.json）
        text = _read_life_state_file(self.ctx)
        if text:
            self._life_cache = normalize_text(text, 600)
            self._life_cache_at = now
            return self._life_cache
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


_ACTIVITY_LABELS = {
    "sleep": "睡觉",
    "nap": "小睡",
    "daze": "刚醒赖床",
    "study": "学习",
    "work": "工作",
    "game": "玩游戏",
    "watch": "看番",
    "music": "听歌",
    "meal": "吃饭",
    "daily": "日常",
    "commute": "通勤",
    "rest": "休息",
}


def _read_life_state_file(ctx: Any) -> str:
    """回退通道：读「生活频率」插件（org.orge-8.life-frequency）的 life_state.json。

    生活频率最新版没有 get_current_context 这个 API，但会把状态写到
    data/plugins/org.orge-8.life-frequency/life_state.json。这里直接读文件，
    把 activity / scene / emotion / energy 拼成一句「当前生活轨迹」文本。
    """

    try:
        import json as _json
        from pathlib import Path as _Path

        data_dir = getattr(getattr(ctx, "paths", None), "data_dir", None)
        if not data_dir:
            return ""
        candidates = [
            _Path(data_dir) / "org.orge-8.life-frequency" / "life_state.json",
            _Path(str(data_dir)).parent / "plugins" / "org.orge-8.life-frequency" / "life_state.json",
        ]
        for path in candidates:
            if not path.exists():
                continue
            try:
                state = _json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(state, dict):
                continue
            activity = str(state.get("activity") or "").strip()
            scene = str(state.get("scene") or "").strip()
            emotion = state.get("emotion")
            energy = state.get("energy")
            label = _ACTIVITY_LABELS.get(activity.lower(), activity or "日常")
            parts = [f"当前活动：{label}"]
            if scene:
                parts[-1] += f"（{scene}）"
            if isinstance(emotion, (int, float)):
                parts.append(f"情绪 {emotion:.1f}/10")
            if isinstance(energy, (int, float)):
                parts.append(f"体力 {energy:.1f}/10")
            return "，".join(parts)
    except Exception:
        return ""
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
