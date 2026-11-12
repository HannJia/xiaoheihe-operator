"""Public, non-generative source documents and first-publication metadata."""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote, urljoin, urlsplit
from zoneinfo import ZoneInfo
try:
    from .news_image import fetch_public
except ImportError:
    from news_image import fetch_public

TEXT_LIMIT = 10000
OFFICIAL_SITES = (
    "openai.com", "anthropic.com", "claude.com", "nvidia.com", "blog.google",
    "deepmind.google", "research.google", "cloud.google.com", "microsoft.com",
    "meta.com", "meta.ai", "about.fb.com", "thinkingmachines.ai", "minimax.io",
    "liquid.ai", "nousresearch.com", "baidu.com", "qwen.ai", "qwencloud.com",
    "qianwenai.com", "qwenlm.github.io", "aliyun.com", "alibabacloud.com", "alibabagroup.com",
    "tencent.com", "bytedance.com", "deepseek.com", "apple.com", "amazon.com",
    "huggingface.co", "vercel.com", "metr.org", "sec.gov", "openrouter.ai",
    "huggingface-moe-blogpost.hf.space",
)
HF_OWNERS = {"nvidia", "baidu", "qwen", "tencent", "tencent-hunyuan", "minimaxai",
             "nousresearch", "liquidai", "google", "meta-llama", "deepseek-ai",
             "huggingface", "openai", "microsoft", "thinking-machines-lab"}
GH_OWNERS = {"qwenlm", "nvidia", "nvidia-research", "cloudflare", "openai",
             "anthropics", "google", "google-deepmind", "microsoft", "meta-llama",
             "minimax-ai", "tencent", "baidu", "huggingface", "nousresearch",
             "liquid4all", "deepseek-ai", "thinking-machines-lab"}
REPORTERS = ("reuters.com", "bloomberg.com", "apnews.com", "ft.com", "wsj.com", "techcrunch.com")


def source_kind(url: str) -> str:
    try:
        p = urlsplit(url)
        host = (p.hostname or "").lower().removeprefix("www.")
        bits = [x.casefold() for x in p.path.split("/") if x]
        port = p.port
    except ValueError:
        return ""
    if p.scheme != "https" or p.username or p.password or port not in (None, 443):
        return ""
    # A platform host is not an endorsement of every user's repository/post.
    if host == "github.com":
        return "primary" if len(bits) >= 2 and bits[0] in GH_OWNERS and not any(
            x in bits for x in ("issues", "discussions", "pull", "pulls")) else ""
    if host == "huggingface.co":
        if len(bits) == 2 and bits[0] == "blog" and bits[1] not in ("community", "posts"):
            return "primary"
        owner = bits[1] if len(bits) > 1 and bits[0] in ("datasets", "spaces") else (bits[0] if bits else "")
        return "primary" if owner in HF_OWNERS and len(bits) >= 2 and not any(
            x in bits for x in ("discussions", "community", "posts")) else ""
    if (host.startswith(("developer.", "cloud.")) and any(
            x in p.path for x in ("/developer/article/", "/article/"))) or "/community/" in p.path:
        return ""
    if any(host == x or host.endswith("." + x) for x in OFFICIAL_SITES):
        return "primary"
    if any(host == x or host.endswith("." + x) for x in REPORTERS):
        return "report"
    return ""


def normalized(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def parse_date(value: object, url: str = "") -> tuple[float, float] | None:
    """Return an honest interval; a date-only label is not an invented time."""
    text = normalized(value)
    if not text or len(text) > 100:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%Y年%m月%d日", "%Y/%m/%d"):
        try:
            text = datetime.strptime(text, fmt).strftime("%Y-%m-%d")
            break
        except ValueError:
            pass
    try:
        value_date = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    host = (urlsplit(url).hostname or "").lower()
    china = any(x in host for x in ("baidu.", "tencent.", "qwen", "aliyun.", "minimax.", "bytedance."))
    zone = ZoneInfo("Asia/Shanghai" if china else "America/Los_Angeles")
    if value_date.tzinfo is None:
        value_date = value_date.replace(tzinfo=zone)
    start = value_date.timestamp()
    if len(text) == 10:
        return start, (value_date + timedelta(days=1)).timestamp()
    return start, start


class ArticleParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.main_parts: list[str] = []
        self.dates: list[str] = []
        self.visible_dates: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._title = False
        self._skip = 0
        self._main = 0
        self._json = False
        self._script: list[str] = []
        self._seen_text = 0

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        if tag in ("main", "article"): self._main += 1
        if tag == "title": self._title = True
        if tag == "script":
            self._json = d.get("type", "").lower() == "application/ld+json"
            self._script = []
        if tag in ("script", "style", "noscript", "nav", "footer"): self._skip += 1
        if tag == "meta":
            key = (d.get("property") or d.get("name") or d.get("itemprop") or "").lower()
            if key in ("article:published_time", "datepublished", "date_published", "pubdate", "publishdate", "parsely-pub-date"):
                self.dates.append(d.get("content") or "")
        if tag == "time" and d.get("datetime") and not any(
                x in str(d).casefold() for x in ("modified", "updated")):
            self.dates.append(d["datetime"])
        if tag == "a" and d.get("href"): self.links.append(d["href"])
        if tag in ("p", "div", "br", "h1", "h2", "time", "li"):
            self.parts.append("\n")
            if self._main: self.main_parts.append("\n")

    def handle_data(self, data):
        if self._json: self._script.append(data)
        if self._title: self.title += data
        if not self._skip:
            label = normalized(data)
            if self._seen_text < 2200 and len(label) <= 40 and parse_date(label):
                self.visible_dates.append(label)
            self._seen_text += len(data)
            self.parts.append(data)
            if self._main: self.main_parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._json:
            try: self._walk(json.loads("".join(self._script)))
            except (ValueError, TypeError): pass
            self._json = False
        if tag in ("script", "style", "noscript", "nav", "footer"): self._skip = max(0, self._skip - 1)
        if tag == "title": self._title = False
        if tag in ("main", "article"): self._main = max(0, self._main - 1)

    def _walk(self, value):
        if isinstance(value, list):
            for x in value: self._walk(x)
        elif isinstance(value, dict):
            # Never use dateModified as a first-publication timestamp.
            kind = value.get("@type", "")
            if any(x in str(kind).casefold() for x in ("article", "posting", "report")):
                if isinstance(value.get("datePublished"), str): self.dates.append(value["datePublished"])
            for key, x in value.items():
                if key in ("@graph", "mainEntity", "mainEntityOfPage", "hasPart"): self._walk(x)


def parse_article(html: str, url: str) -> dict[str, Any]:
    parser = ArticleParser()
    parser.feed(html)
    text = normalized(" ".join(parser.main_parts or parser.parts))
    # A date somewhere in body prose is not a first-publication timestamp.
    # Missing machine/time metadata remains unknown instead of borrowing a date.
    dated = [(date, parse_date(date, url)) for date in parser.dates]
    dated = [(raw, interval) for raw, interval in dated if interval]
    # Pick earliest known publication, not the latest update in the page.
    dated.sort(key=lambda x: x[1][0])
    raw, interval = dated[0] if dated else ("", None)
    return {"url": url, "title": normalized(parser.title)[:180], "content": text[:TEXT_LIMIT],
            "published_raw": raw, "published_interval": list(interval) if interval else None,
            "kind": source_kind(url), "links": [urljoin(url, x) for x in parser.links[:300]]}


def load_document(url: str) -> dict[str, Any]:
    body, final = fetch_public(url, limit=2_000_000, types=("text/html", "application/xhtml+xml"))
    doc = parse_article(body.decode("utf-8", "replace"), final)
    if not doc["kind"] or len(doc["content"]) < 80:
        raise ValueError("no attributable original article")
    parts = urlsplit(final)
    bits = [x for x in parts.path.split("/") if x]
    # Undated model cards: use the publisher's public repository creation record.
    # It is conservative for model release freshness and is never a modified date.
    if parts.hostname == "huggingface.co" and len(bits) >= 2 and not doc["published_interval"]:
        namespace = "datasets" if bits[0] == "datasets" else "models"
        model = "/".join(bits[1:3] if namespace == "datasets" else bits[:2])
        data, _ = fetch_public(f"https://huggingface.co/api/{namespace}/{model}", limit=2_000_000, types=("application/json",))
        raw = json.loads(data).get("createdAt") or ""
        interval = parse_date(raw, final)
        doc.update(published_raw=raw, published_interval=list(interval) if interval else None)
    if parts.hostname == "github.com" and len(bits) >= 5 and bits[2:4] == ["releases", "tag"]:
        api = f"https://api.github.com/repos/{bits[0]}/{bits[1]}/releases/tags/{quote('/'.join(bits[4:]), safe='')}"
        data, _ = fetch_public(api, limit=2_000_000, types=("application/json",))
        release = json.loads(data)
        if release.get("draft"): raise ValueError("unpublished release")
        raw = release.get("published_at") or ""
        interval = parse_date(raw, final)
        doc.update(published_raw=raw, published_interval=list(interval) if interval else None)
    # Date provenance is kept outside the article text, with an explicit label.
    doc["date_text"] = "First publication metadata: " + doc["published_raw"] if doc["published_raw"] else ""
    return doc


async def fetch_document(url: str) -> dict[str, Any]:
    return await asyncio.to_thread(load_document, url)
