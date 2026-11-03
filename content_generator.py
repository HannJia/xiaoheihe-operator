from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

try:
    from .policy import normalize_text
except ImportError:  # pragma: no cover - direct Runner fallback
    from policy import normalize_text


# 读不到宿主人设、也没有在插件里单独填写时用的中性人设。
DEFAULT_PERSONA = "你是一个爱打游戏的普通年轻人。"

PersonaSource = Callable[[], Awaitable[str]]


class ContentGenerator:
    def __init__(self, llm: Any, persona: PersonaSource | None = None) -> None:
        """persona 返回人设文本：默认是宿主 MaiBot 的昵称和人格，插件配置里可单独覆盖。"""

        self.llm = llm
        self._persona = persona

    async def _identity(self) -> str:
        if self._persona is not None:
            try:
                text = str(await self._persona() or "").strip()
            except Exception:  # noqa: BLE001 - 人设读不到不应阻止生成
                text = ""
            if text:
                return text
        return DEFAULT_PERSONA

    async def reply(
        self,
        *,
        post_title: str,
        post_body: str,
        comment: str = "",
        proactive: bool = False,
    ) -> str | None:
        """proactive=True 表示刷到帖子主动留言；否则是回复别人对我的评论。"""
        if proactive:
            task = (
                "你刚刷到这篇帖子，像普通玩家一样随手留一句评论：说你自己的感受、经历或一个小看法，"
                "可以吐槽、接梗或附和。不要复述或总结帖子内容，不要像在写点评或新闻稿，"
                "不要用“确实”“这波”“诚意拉满”这类套话开头。一般 10 到 50 字，一两句话就够。"
            )
        else:
            task = (
                f"对方评论：{normalize_text(comment, 500)}\n"
                "这是别人回复你的评论。像真人聊天一样接着回一句：接住对方的话，可以附和、补充或开个玩笑，"
                "不要重复对方的原话，不要说教。一般 10 到 60 字。"
            )
        identity = await self._identity()
        prompt = [
            {
                "role": "system",
                "content": (
                    f"{identity}\n"
                    "你在小黑盒社区里就是一个普通玩家：说话口语、随意，有自己的态度，不端着，不营销，不连续提问，"
                    "不用感叹号刷屏。只围绕当前帖子和评论说话，不编造具体游戏数据、服务器、账号或现实经历。"
                    "技术问题最多用大白话说一两句感受或思路，不写代码、不代做题。没有合适内容时只输出 SKIP。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"帖子标题：{normalize_text(post_title, 120)}\n"
                    f"帖子正文：{normalize_text(post_body, 900)}\n"
                    f"{task}\n"
                    "只输出评论正文，不要加引号、前缀或解释。"
                ),
            },
        ]
        result = await self.llm.generate(prompt, temperature=0.85, max_tokens=600)
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
        identity = await self._identity()
        prompt = [
            {
                "role": "system",
                "content": (
                    f"{identity}\n"
                    "你正在回复小黑盒私信，"
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
        result = await self.llm.generate(prompt, temperature=0.65, max_tokens=600)
        if not isinstance(result, dict) or not result.get("success"):
            return None
        text = normalize_text(
            result.get("response", ""), max(40, min(int(max_chars), 300))
        )
        if not text or text.upper() == "SKIP":
            return None
        return text

    async def post(self, *, life_context: str, topic_hint: str = "") -> tuple[str, str] | None:
        identity = await self._identity()
        direction_line = (
            f"话题方向：{normalize_text(topic_hint, 120)}\n" if topic_hint and topic_hint.strip() else ""
        )
        prompt = [
            {
                "role": "system",
                "content": (
                    f"{identity}\n"
                    "你在小黑盒发帖就像普通用户随手记一笔：写此刻在干嘛、最近在想什么、一点小情绪或小观察，"
                    "口语、具体、有画面，不写成攻略、测评或营销文案，不用一串感叹号，结尾不要硬加提问。"
                    "不写代码、脚本、调试、配置或服务器内容；不编造具体数据、账号信息或现实经历。"
                    "想聊什么就聊什么，游戏、数码、生活、吃穿、感想都可以，不用限定在某个领域。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"当前生活轨迹：{normalize_text(life_context, 600)}\n"
                    f"{direction_line}"
                    "请只输出 JSON：{\"title\":\"不超过28字的标题\",\"body\":\"不超过220字的正文\"}。"
                    "语气自然，像真人分享，避免营销口吻和连续提问。"
                ),
            },
        ]
        result = await self.llm.generate(prompt, temperature=0.8, max_tokens=900)
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

