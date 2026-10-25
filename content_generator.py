from __future__ import annotations

import json
from typing import Any

try:
    from .policy import normalize_text
except ImportError:  # pragma: no cover - direct Runner fallback
    from policy import normalize_text


class ContentGenerator:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    async def reply(self, *, post_title: str, post_body: str, comment: str) -> str | None:
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是 maibot，潮汐工作室的内容运营。你在小黑盒社区回复时要自然、简短、像真人，"
                    "只围绕当前帖子和评论说话。数学题、代码、脚本、调试、配置和技术任务不要处理，"
                    "不要编造服务器、账号或现实经历。若没有合适内容，只输出 SKIP。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"帖子标题：{normalize_text(post_title, 120)}\n"
                    f"帖子正文：{normalize_text(post_body, 900)}\n"
                    f"对方评论：{normalize_text(comment, 500)}\n"
                    "请生成一条不超过120字的自然回复；不要加引号、前缀或解释。"
                ),
            },
        ]
        result = await self.llm.generate(prompt, temperature=0.7, max_tokens=220)
        if not isinstance(result, dict) or not result.get("success"):
            return None
        text = normalize_text(result.get("response", ""), 240)
        if not text or text.upper() == "SKIP":
            return None
        return text

    async def direct_message_reply(
        self,
        *,
        sender_name: str,
        message: str,
        recent_context: str = "",
        max_chars: int = 180,
    ) -> str | None:
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是 maibot，潮汐工作室的内容运营。你正在回复小黑盒私信，"
                    "语气自然、简短、像真人，不主动索要隐私或账号凭证。数学题、代码、脚本、"
                    "调试、配置、服务器和其他技术任务直接说明不会处理。遇到施压、连续轰炸或无法判断的内容，"
                    "礼貌结束话题。没有合适回复时只输出 SKIP。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"对方昵称：{normalize_text(sender_name, 60)}\n"
                    f"最近上下文：{normalize_text(recent_context, 900)}\n"
                    f"最新私信：{normalize_text(message, 500)}\n"
                    f"请生成一条不超过{max(40, min(int(max_chars), 300))}字的私信回复；"
                    "不要加引号、前缀或解释。"
                ),
            },
        ]
        result = await self.llm.generate(prompt, temperature=0.65, max_tokens=220)
        if not isinstance(result, dict) or not result.get("success"):
            return None
        text = normalize_text(
            result.get("response", ""), max(40, min(int(max_chars), 300))
        )
        if not text or text.upper() == "SKIP":
            return None
        return text

    async def post(self, *, life_context: str, topic_hint: str = "游戏日常") -> tuple[str, str] | None:
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是 maibot，潮汐工作室的内容运营。你在小黑盒发帖时只写生活轨迹、游戏体验、"
                    "轻松的社区观察和普通玩家话题；不能回答数学题、代码、脚本、调试、配置或服务器问题。"
                    "不能编造具体游戏数据、账号信息或现实经历。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前生活轨迹：{normalize_text(life_context, 600)}\n"
                    f"话题方向：{normalize_text(topic_hint, 120)}\n"
                    "请只输出 JSON：{\"title\":\"不超过28字的标题\",\"body\":\"不超过220字的正文\"}。"
                    "语气自然，像真人分享，避免营销口吻和连续提问。"
                ),
            },
        ]
        result = await self.llm.generate(prompt, temperature=0.8, max_tokens=360)
        if not isinstance(result, dict) or not result.get("success"):
            return None
        raw = normalize_text(result.get("response", ""), 900)
        parsed = _parse_post_json(raw)
        if not parsed:
            return None
        title = normalize_text(parsed.get("title", ""), 80)
        body = normalize_text(parsed.get("body", ""), 300)
        if not title or not body or body.upper() == "SKIP":
            return None
        return title, body


def _parse_post_json(raw: str) -> dict[str, str] | None:
    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`").removeprefix("json").strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError:
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None

