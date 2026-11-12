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


# 早报专用检查。正常 AI 新闻里常见 token、部署、代码生成、GPU 服务器，不能套用上面的
# _SENSITIVE/_TECHNICAL；这里只拦凭证类字眼、自曝身份、内部词、链接和引流。
_NEWS_SENSITIVE = re.compile(r"(?:api[_ -]?key|cookie|密码|私钥|ssh\s*key|登录码|验证码)", re.I)
_AI_SELF = r"(?:AI|ＡＩ|人工智能|机器人|语言模型|大模型|聊天机器人|智能助手|程序)"
_AI_NOT_SELF = r"(?!\s*(?:爱好者|从业者|行业|圈|领域|相关|产品|工具|新闻|资讯|公司|创业|应用|方向|技术|员))"
_SELF_DISCLOSURE = re.compile(
    rf"(?:(?:我|本人)(?:其实|就|真的|本来)?是(?:一个|一名|一款|个|台)?\s*{_AI_SELF}{_AI_NOT_SELF}"
    rf"|(?:作为|身为)(?:一个|一名|一款|个|名|款)?\s*{_AI_SELF}{_AI_NOT_SELF}"
    r"|as an ai\b)",
    re.I,
)
_INTERNAL = re.compile(r"(?:maibot|maisaka|麦麦)", re.I)
_URL = re.compile(r"(?:https?://|www\.)", re.I)
_PROMO = re.compile(r"(?:关注我|私信我|加群|进群|加微信|(?<![a-z])(?:vx|v信)(?![a-z]))", re.I)
_FAKE_EXPERIENCE = re.compile(
    r"(?:我们工作室|工作室|我们|我)[^没未不别非，。！？；,.!?;\s]{0,4}"
    r"(?:用了|用过|用上|在用(?!户)|试了|试过|体验了|体验过|实测|上手了|装了|买了|订阅了|开通了)"
)


def blocked_hit(text: str, blocked_keywords: list[str] | tuple[str, ...] = ()) -> str:
    """返回文字里命中的第一个屏蔽词，没有就返回空字符串。"""

    lowered = normalize_text(text).casefold()
    for word in blocked_keywords:
        token = str(word or "").strip()
        if token and token.casefold() in lowered:
            return token
    return ""


def is_news_publishable(text: str, blocked_keywords: list[str] | tuple[str, ...] = ()) -> tuple[bool, str]:
    """早报里的一句话能不能公开发；不合格时返回原因代码。"""

    text = normalize_text(text)
    if not text:
        return False, "empty"
    if _NEWS_SENSITIVE.search(text):
        return False, "sensitive"
    if _SELF_DISCLOSURE.search(text):
        return False, "self_disclosure"
    if _INTERNAL.search(text):
        return False, "internal_word"
    if _URL.search(text):
        return False, "url"
    if _PROMO.search(text):
        return False, "promo"
    if blocked_hit(text, blocked_keywords):
        return False, "blocked_keyword"
    return True, "ok"


def has_fake_experience(text: str) -> bool:
    """看法句里有没有“我/我们/工作室用过、试过”这类编造的亲身经历。"""

    return bool(_FAKE_EXPERIENCE.search(normalize_text(text)))

