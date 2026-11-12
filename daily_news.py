"""每日 AI 早报的纯逻辑：搜索词、候选筛选、事实核查、帖子组装、配图 prompt 和私聊文案。

联网、模型、绘图和发帖调用都在 plugin.py；这里不碰网络和数据库，便于单测。
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

try:
    from .policy import blocked_hit, has_fake_experience, is_news_publishable, normalize_text
    from . import news_sources
except ImportError:  # pragma: no cover - direct Runner fallback
    from policy import blocked_hit, has_fake_experience, is_news_publishable, normalize_text
    import news_sources


DEFAULT_QUERIES = (
    "{date} AI 人工智能 新闻",
    "{date} 大模型 发布",
    "{date} AI 工具 开源 新功能",
    "{date} 科技 AI 人物 人事变动 公开声明 官方公告",
    "{date} NVIDIA OpenAI Anthropic CEO leadership announcement",
)
DEFAULT_BLOCKED_KEYWORDS = (
    "翻墙", "科学上网", "VPN", "梯子", "注册教程", "买号", "代充", "中转",
    "出口管制", "制裁", "实体清单", "选举", "军事", "战争", "股价", "涨停", "跌停",
    "投资建议", "色情", "深度伪造", "换脸", "赌博", "诈骗",
)
MAX_AGE_SECONDS = 36 * 3600
FUTURE_SLACK_SECONDS = 2 * 3600
RECENT_DAYS = 3
PER_SITE_LIMIT = 2
MAX_CANDIDATES = 15
MIN_ITEMS = 3
MAX_ITEMS = 5
SOURCE_TEXT_LIMIT = 2000
FALLBACK_INTRO = "早上刷到几条 AI 新闻，随手记一下。"
IMAGE_STYLE = (
    "扁平插画风格，柔和明亮的配色，科技感，画面干净；静物或空场景构图，"
    "画面中没有任何人物、文字、字母、标志或水印"
)
FALLBACK_SCENE = "清晨的书桌上放着一杯咖啡和一台亮着屏幕的笔记本电脑，窗外有柔和的晨光"

_DIGEST_TITLE = re.compile(r"(?:早报|日报|周报|晚报|汇总|盘点|速览|一周|快讯合集)")
_TRACKING_PARAM = re.compile(r"^(?:utm_|spm|fbclid|gclid|from$|source$|share)", re.I)

# 配文显示来源名称与链接；认识的域名显示中文名，其余显示主机名。
SOURCE_NAMES = {
    "jiqizhixin.com": "机器之心",
    "qbitai.com": "量子位",
    "ithome.com": "IT之家",
    "36kr.com": "36氪",
    "ifanr.com": "爱范儿",
    "sspai.com": "少数派",
    "infoq.cn": "InfoQ",
    "leiphone.com": "雷锋网",
    "geekpark.net": "极客公园",
    "huxiu.com": "虎嗅",
    "pingwest.com": "品玩",
    "cnbeta.com.tw": "cnBeta",
    "thepaper.cn": "澎湃新闻",
    "jiemian.com": "界面新闻",
    "yicai.com": "第一财经",
    "sina.com.cn": "新浪",
    "qq.com": "腾讯网",
    "163.com": "网易",
    "sohu.com": "搜狐",
    "zhihu.com": "知乎",
    "theverge.com": "The Verge",
    "techcrunch.com": "TechCrunch",
    "venturebeat.com": "VentureBeat",
    "technologyreview.com": "MIT Technology Review",
    "arstechnica.com": "Ars Technica",
    "wired.com": "Wired",
    "reuters.com": "路透社",
    "bloomberg.com": "彭博社",
    "openai.com": "OpenAI 官方",
    "anthropic.com": "Anthropic 官方",
    "deepmind.google": "Google DeepMind 官方",
    "blog.google": "Google 官方博客",
    "huggingface.co": "Hugging Face",
    "github.com": "GitHub",
    "nvidia.com": "英伟达官方",
    "microsoft.com": "微软官方",
    "meta.com": "Meta 官方",
    "about.fb.com": "Meta 官方",
    "alibabagroup.com": "阿里巴巴官方",
    "tencent.com": "腾讯官方",
    "qwen.ai": "通义千问官方",
    "qwencloud.com": "QwenCloud 官方",
    "aliyun.com": "阿里云官方",
    "alibabacloud.com": "阿里云官方",
    "vercel.com": "Vercel 官方",
}

PRIMARY_DOMAINS = (
    "nvidia.com", "openai.com", "anthropic.com", "microsoft.com", "blog.google",
    "deepmind.google", "about.fb.com", "meta.com", "alibabagroup.com", "tencent.com",
    "bytedance.com", "deepseek.com", "apple.com", "amazon.com", "sec.gov",
    "qwen.ai", "qwencloud.com", "aliyun.com", "alibabacloud.com", "vercel.com",
)
_PEOPLE_CLAIM = re.compile(
    r"出轨|婚外|不忠|丑闻|辞职|辞去|卸任|罢免|解雇|离职|任命|接任|被捕|起诉|性骚扰|"
    r"\b(?:affair|unfaithful|scandal|resign\w*|oust\w*|fired|interim\s+ceo|ceo\s+transition)\b", re.I
)


def sensitive_people_claim(text: str) -> bool:
    return bool(_PEOPLE_CLAIM.search(text))


def primary_source(url: str) -> bool:
    return news_sources.source_kind(url) == "primary"


def _host(url: str) -> str:
    try:
        host = (urlsplit(str(url or "")).hostname or "").casefold()
    except ValueError:
        return ""
    return host.removeprefix("www.")


def _known_domain(host: str) -> str:
    for domain in SOURCE_NAMES:
        if host == domain or host.endswith("." + domain):
            return domain
    return ""


def source_name(url: str) -> str:
    host = _host(url)
    domain = _known_domain(host)
    return SOURCE_NAMES[domain] if domain else host


def site_key(url: str) -> str:
    """同站限额用的键：认识的来源按主域名，不认识的按主机名。"""

    host = _host(url)
    return _known_domain(host) or host


def normalize_url(url: str) -> str:
    """去重用的规范网址：只收 http(s)，去掉锚点和追踪参数。"""

    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAM.match(k)]
    host = parts.hostname.casefold().removeprefix("www.")
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), ""))


def format_queries(templates: list[str] | tuple[str, ...], now: float) -> list[str]:
    local = time.localtime(now)
    yesterday = time.localtime(now - 86400)
    values = {
        "date": f"{local.tm_mon}月{local.tm_mday}日",
        "yesterday": f"{yesterday.tm_mon}月{yesterday.tm_mday}日",
    }
    result: list[str] = []
    for template in templates:
        text = str(template or "")
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        text = normalize_text(text, 120)
        if text and text not in result:
            result.append(text)
        if len(result) >= 5:
            break
    return result


def parse_published(value: object) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def collect_candidates(
    hits: list[dict[str, Any]],
    *,
    now: float,
    blocked: list[str] | tuple[str, ...],
    recent_urls: set[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """搜索结果 → 候选：去重，丢掉没日期、过期、命中屏蔽词、近几天用过的，同站限额。"""

    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    recent = {normalize_url(url) for url in recent_urls if url}
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        url = str(hit.get("url") or "").strip()
        key = normalize_url(url)
        title = normalize_text(hit.get("title", ""), 120)
        if not key or not title:
            drop("invalid")
            continue
        if key in seen:
            drop("duplicate")
            continue
        seen.add(key)
        if key in recent:
            drop("recent")
            continue
        published = parse_published(hit.get("published_at"))
        if published is None:
            drop("no_date")
            continue
        if published < now - MAX_AGE_SECONDS or published > now + FUTURE_SLACK_SECONDS:
            drop("stale")
            continue
        snippet = normalize_text(hit.get("snippet", ""), 400)
        summary = normalize_text(hit.get("summary", ""), 900)
        if not _known_domain(_host(url)) and not primary_source(url):
            drop("unknown_source")
            continue
        if _DIGEST_TITLE.search(title):
            drop("digest")
            continue
        if blocked_hit(f"{title} {snippet} {summary}", blocked):
            drop("blocked")
            continue
        if sensitive_people_claim(f"{title} {snippet} {summary}") and not primary_source(url):
            drop("people_needs_primary")
            continue
        pool.append(
            {
                "url": url,
                "key": key,
                "title": title,
                "snippet": snippet,
                "summary": summary,
                "published": published,
                "source": source_name(url),
                "site": site_key(url),
                "known": bool(_known_domain(_host(url))),
                "digest": bool(_DIGEST_TITLE.search(title)),
            }
        )
    # 单条新闻优先于“早报/汇总”页，认识的来源优先，越新越靠前。
    pool.sort(key=lambda item: (item["digest"], not item["known"], -item["published"]))
    per_site: dict[str, int] = {}
    result: list[dict[str, Any]] = []
    for item in pool:
        if per_site.get(item["site"], 0) >= PER_SITE_LIMIT:
            drop("site_cap")
            continue
        per_site[item["site"]] = per_site.get(item["site"], 0) + 1
        result.append(item)
        if len(result) >= MAX_CANDIDATES:
            break
    return result, dropped


_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_BARE_URL = re.compile(r"https?://\S+|www\.\S+", re.I)


def clean_page_text(text: str, limit: int = SOURCE_TEXT_LIMIT) -> str:
    """网页正文 → 给模型和核查用的纯文字：去掉图片、链接地址和裸网址。"""

    value = _MD_IMAGE.sub(" ", str(text or ""))
    value = _MD_LINK.sub(r"\1", value)
    value = _BARE_URL.sub(" ", value)
    return normalize_text(value, limit)


# 事实核查：数字和英文词必须出现在来源里。借用 B 站插件 fact_check.py 的思路，
# 按整词比较，避免 "1000" 因为出现在 "10000" 里被误判为有据可查。
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
_LATIN = re.compile(r"[a-z]+")
_GENERIC_LATIN = {"ai", "app", "api", "ios", "pc", "gpu", "cpu", "agi", "aigc", "llm", "vs", "ok"}
_DASHES = re.compile(r"[\u2010-\u2015\u2212]")


def _fact_norm(text: str) -> str:
    return _DASHES.sub("-", unicodedata.normalize("NFKC", str(text or ""))).casefold()


def _numbers(text: str) -> set[str]:
    result: set[str] = set()
    for raw in _NUMBER.findall(_fact_norm(text)):
        value = raw.replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        result.add(value.lstrip("0") or "0")
    return result


def _latin_words(text: str) -> set[str]:
    return {word for word in _LATIN.findall(_fact_norm(text)) if word not in _GENERIC_LATIN}


def unsupported_tokens(text: str, sources: list[str]) -> list[str]:
    """text 里有、但所有来源里都找不到的数字和英文词。"""

    joined = " ".join(sources)
    numbers = _numbers(text) - _numbers(joined)
    words = _latin_words(text) - _latin_words(joined)
    return sorted(numbers) + sorted(words)


class NewsSkip(Exception):
    """今天放弃，不再重试（候选太少、模型说没有合适的、分区配置错误）。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail


class NewsRetry(Exception):
    """发帖前失败，过一会儿可以再试（搜索、模型、核查后条目不够）。"""

    def __init__(self, stage: str, detail: str = "") -> None:
        super().__init__(f"{stage}:{detail}" if detail else stage)
        self.stage = stage
        self.detail = detail


@dataclass
class NewsDraft:
    title: str
    blocks: list[str]
    items: list[dict[str, Any]]
    dropped: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    image_bytes: bytes | None = None
    image_note: str = ""
    image_sources: list[dict[str, str]] = field(default_factory=list)
    verification: dict[str, Any] = field(default_factory=dict)

    @property
    def body(self) -> str:
        # 读回时 _rich_post_content 用 "\n" 拼接多个文字块，这里保持一致。
        return "\n".join(self.blocks)

    def metadata(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "body": self.body,
            "sources": [{"url": i["url"], "title": i["title"], "source": i["source"]} for i in self.items],
            "dropped": self.dropped[:10],
            "image_note": self.image_note,
            "image_sources": self.image_sources,
            "verification": self.verification,
            "verified_items": [{"title": i["title"], "fact": i["fact"],
                "event": i.get("verified_event", {}), "url": i["url"]} for i in self.items],
        }


def source_text(candidate: dict[str, Any]) -> str:
    """核查依据：标题、搜索摘要、搜索服务给的长摘要和读到的原文。"""

    return " ".join(str(candidate.get(key) or "") for key in ("title", "snippet", "summary", "content"))


def _fact_problem(text: str, source: str, blocked: list[str] | tuple[str, ...]) -> str:
    ok, reason = is_news_publishable(text, blocked)
    if not ok:
        return reason
    if re.search(r"今天|昨天|今日|昨日|前天|明天", text):
        return "relative_time"
    missing = unsupported_tokens(text, [source])
    return "unsupported:" + ",".join(missing[:5]) if missing else ""


def _opinion_problem(text: str, sources: list[str], blocked: list[str] | tuple[str, ...]) -> str:
    ok, reason = is_news_publishable(text, blocked)
    if not ok:
        return reason
    if has_fake_experience(text):
        return "fake_experience"
    missing = unsupported_tokens(text, sources)
    return "unsupported:" + ",".join(missing[:5]) if missing else ""


def verify_written(
    written: dict[str, Any],
    picked: list[dict[str, Any]],
    blocked: list[str] | tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[dict[str, str]], str, str]:
    """核查模型写的早报：返回 (保留的条目, 删掉的条目, 开头, 结尾看法)。"""

    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, str]] = []
    used: set[int] = set()
    for entry in written.get("items") or []:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        if index in used or not 1 <= index <= len(picked):
            continue
        used.add(index)
        candidate = picked[index - 1]
        source = str(candidate.get("content") or "") if candidate.get("documents") else source_text(candidate)
        fact = normalize_text(entry.get("fact", ""), 120)
        comment = normalize_text(entry.get("comment", ""), 40)
        problem = _fact_problem(fact, source, blocked)
        if not problem and sensitive_people_claim(fact + " " + candidate["title"]):
            evidence = str(entry.get("evidence") or "").strip()
            original = str(candidate.get("content") or "")
            if not primary_source(candidate["url"]) or len(evidence) < 12 or evidence not in original:
                problem = "people_needs_primary_quote"
            elif re.search(r"\bresign\w*\b|辞职|辞去", original, re.I) and re.search(r"开除|解雇|罢免", fact):
                problem = "status_escalation"
        if problem:
            dropped.append({"title": candidate["title"], "reason": problem})
            continue
        if comment:
            comment_problem = _opinion_problem(comment, [fact, source], blocked)
            if comment_problem:
                dropped.append({"title": candidate["title"], "reason": "comment_" + comment_problem})
                comment = ""
        evidence = str(entry.get("evidence") or "").strip()
        original = str(candidate.get("content") or "")
        kept.append({**candidate, "fact": fact, "comment": comment,
                     "evidence": evidence if len(evidence) >= 12 and evidence in original else ""})
    for index, candidate in enumerate(picked, start=1):
        if index not in used:
            dropped.append({"title": candidate["title"], "reason": "omitted"})
    kept = kept[:MAX_ITEMS]
    grounds = [part for item in kept for part in (item["fact"], source_text(item))]
    intro = normalize_text(written.get("intro", ""), 40)
    if not intro or _opinion_problem(intro, [], blocked):
        intro = FALLBACK_INTRO
    take = normalize_text(written.get("take", ""), 100)
    if take and _opinion_problem(take, grounds, blocked):
        dropped.append({"title": "结尾看法", "reason": _opinion_problem(take, grounds, blocked)})
        take = ""
    return kept, dropped, intro, take


_UNSAFE_CHARS = str.maketrans({"&": "＆", "<": "《", ">": "》", '"': "”"})


def post_safe(text: str) -> str:
    """每个文字块保持单行，并替换平台可能转义的字符，避免读回对不上。"""

    return normalize_text(str(text or "").translate(_UNSAFE_CHARS), 400)


def build_title(now: float) -> str:
    local = time.localtime(now)
    return f"{local.tm_mon}月{local.tm_mday}日 AI 早报"


def build_blocks(intro: str, items: list[dict[str, Any]], take: str) -> list[str]:
    blocks = [post_safe(intro)] if intro else []
    for number, item in enumerate(items, start=1):
        line = f"{number}. {item['fact']}（{item['source']}）"
        if item.get("comment"):
            line += item["comment"]
        blocks.append(post_safe(line))
    for number, item in enumerate(items, start=1):
        url = normalize_url(item.get("url", ""))
        if url:
            blocks.append(f"来源 {number}：{url}")
    if take:
        blocks.append(post_safe(take))
    return [block for block in blocks if block]


_LATIN_OR_DIGIT = re.compile(r"[A-Za-z0-9]+(?:[-.·][A-Za-z0-9]+)*[-.·]?")


def image_prompt(scene: str, blocked: list[str] | tuple[str, ...] = ()) -> str:
    """配图 prompt：去掉英文和数字（防止画出品牌名、文字），加固定风格。"""

    text = _LATIN_OR_DIGIT.sub("", unicodedata.normalize("NFKC", str(scene or "")))
    text = normalize_text(text, 60).strip("，。、；：,.;: ")
    if len(text) < 4 or blocked_hit(text, blocked):
        text = FALLBACK_SCENE
    return f"{text}。{IMAGE_STYLE}。"


_REASON_LABELS = {
    "empty": "没写内容",
    "sensitive": "含账号凭证类字眼",
    "self_disclosure": "像在说自己是 AI",
    "internal_word": "含内部词",
    "url": "含网址",
    "promo": "含引流词",
    "blocked_keyword": "含屏蔽词",
    "relative_time": "用了“今天/昨天”这类说法",
    "fake_experience": "说自己或工作室用过、试过",
    "omitted": "模型没写这一条",
    "people_needs_primary_quote": "人物事件缺少可核对的官方原文引句",
    "status_escalation": "把辞职等状态夸大为开除或罢免",
    "review_missing": "缺少独立审校结论", "review_wrong_fact": "审校未对应当前原句",
    "evidence_missing": "没有可核对的逐字原文证据", "event_missing": "事件身份不完整",
    "original_time_missing": "缺少原始发布日期", "event_stale": "原始事件已过期",
    "event_time_uncertain": "原始时间不足以确认在时效窗口内", "future_event": "事件日期在未来",
    "event_date_evidence": "事件日期缺少对应原文证据", "event_duplicate": "同一事件已收录",
    "verify_failed": "独立审校不可用，暂不生成早报",
    "report_attribution": "第三方报道缺少明确归属",
}
_DROP_LABELS = {
    "duplicate": "重复", "recent": "近 3 天讲过", "no_date": "没日期", "stale": "过期",
    "blocked": "屏蔽词", "site_cap": "同站过多", "invalid": "无效",
    "people_needs_primary": "人物事件缺少官方来源",
    "unknown_source": "来源尚未核实", "digest": "二次汇总页",
}


def reason_label(reason: str) -> str:
    if reason.startswith("semantic_rejected:"):
        return "原文审校未通过：" + reason.partition(":")[2]
    if reason.startswith("comment_semantic_rejected:"):
        return "简评审校未通过：" + reason.partition(":")[2]
    if reason.startswith("comment_"):
        return "感想" + reason_label(reason.removeprefix("comment_"))
    if reason.startswith("unsupported:"):
        return "原文里找不到 " + reason.removeprefix("unsupported:")
    return _REASON_LABELS.get(reason, reason)


def funnel_note(hit_count: int, kept_count: int, dropped: dict[str, int]) -> str:
    parts = [f"{_DROP_LABELS.get(key, key)} {value}" for key, value in sorted(dropped.items()) if value]
    tail = f"（{'，'.join(parts)}）" if parts else ""
    return f"搜到 {hit_count} 条，能用 {kept_count} 条{tail}"


def owner_report(draft: NewsDraft, *, header: str) -> str:
    lines = [header, f"标题：{draft.title}", *draft.blocks, "", "来源："]
    lines += [f"{n}. {item['source']}：{item['url']}" for n, item in enumerate(draft.items, start=1)]
    if draft.dropped:
        lines.append("核查删掉：")
        lines += [f"- {d['title'][:30]}：{reason_label(d['reason'])}" for d in draft.dropped[:6]]
    lines += draft.notes
    lines.append(draft.image_note or ("配图：见下图" if draft.image_bytes else "配图：未开启"))
    return "\n".join(line for line in lines if line is not None)[:1800]


_FINAL_REASONS = {
    "missed_window": "到截止时间还没能开始",
    "few_candidates": "符合条件的新闻不到 3 条",
    "model_skip": "模型判断今天没有合适的新闻",
    "attempts_exhausted": "试了 3 次都没成功",
    "deadline": "到截止时间还没成功",
    "topic_selection": "发帖分区找不到，请检查早报的“发帖分区”配置",
    "rejected": "小黑盒拒绝了这条帖子",
}
_SKIP_REASONS = {"asleep": "她还在睡", "not_logged_in": "小黑盒没登录", "cooldown": "发送冷却中"}
_STAGES = {
    "search_failed": "联网搜索失败，请检查 MaiBot 联网服务“日常搜索”和它的 Key",
    "select_failed": "挑新闻的模型调用失败",
    "write_failed": "写早报的模型调用失败",
    "too_few_items": "能用的条目不到 3 条",
    "interrupted": "上次生成到一半程序重启了",
    "topic_failed": "解析发帖分区失败",
    "not_logged_in": "小黑盒没登录",
    "verify_failed": "原文事实审校失败，暂不生成早报",
}


def failure_text(
    reason: str,
    *,
    last_error: str = "",
    skip_reason: str = "",
    detail: str = "",
    header: str = "今天的 AI 早报没发出去",
) -> str:
    """早报没发出去（或预览没生成出来）时给老板的说明。"""

    text = header
    if reason:
        text += f"：{_FINAL_REASONS.get(reason, reason)}"
        if reason == "missed_window" and skip_reason:
            text += f"（{_SKIP_REASONS.get(skip_reason, skip_reason)}）"
    if detail:
        text += f"\n详情：{normalize_text(detail, 200)}"
    if last_error:
        stage, _, rest = last_error.partition(":")
        label = _STAGES.get(stage)
        if label and rest:
            label += f"（{normalize_text(rest, 100)}）"
        text += f"\n{'最后一次失败' if reason else '原因'}：{label or normalize_text(last_error, 160)}"
    return text
