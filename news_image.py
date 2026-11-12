"""Render verified news into one readable image; public article photos are optional.

Text is typeset with Pillow, never redrawn by an image model. Remote URLs are
untrusted: resolve and pin public IPs, validate every redirect, limit time/bytes,
and send no account cookies. Article excerpts are labelled, not fake screenshots.
"""
from __future__ import annotations

import asyncio
import http.client
import io
import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

WIDTH = 1080
MAX_HTML = 2_000_000
MAX_IMAGE = 5_000_000
MAX_PIXELS = 24_000_000


def public_target(url: str) -> tuple[str, int, str, str]:
    parts = urlsplit(url)
    if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
            or parts.port not in (None, 443)):
        raise ValueError("only public HTTPS URLs are allowed")
    host = parts.hostname.encode("idna").decode("ascii")
    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    ips = list(dict.fromkeys(row[4][0] for row in addresses))
    if not ips or any(not ipaddress.ip_address(ip).is_global for ip in ips):
        raise ValueError("non-public address")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return host, 443, ips[0], path


def fetch_public(url: str, *, limit: int, types: tuple[str, ...]) -> tuple[bytes, str]:
    """HTTPS with pinned DNS and normal certificate/SNI validation; no proxies/cookies."""
    deadline = time.monotonic() + 18
    for _ in range(4):
        host, port, ip, path = public_target(url)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("article fetch deadline")
        connection = http.client.HTTPSConnection(host, port, timeout=min(6, remaining))
        connection._create_connection = lambda address, timeout=None, source_address=None: socket.create_connection(
            (ip, port), timeout, source_address
        )
        try:
            connection.request("GET", path, headers={
                "User-Agent": "Xiaoheihe-News-Preview/0.6 (+public-source-attribution)",
                "Accept": ", ".join(types), "Accept-Encoding": "identity",
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                location = response.getheader("Location")
                if not location:
                    raise ValueError("redirect without location")
                url = urljoin(url, location)
                continue
            mime = response.getheader("Content-Type", "").split(";")[0].strip().lower()
            if response.status != 200 or not any(mime.startswith(t) for t in types):
                raise ValueError("unavailable public media")
            if int(response.getheader("Content-Length", "0")) > limit:
                raise ValueError("media too large")
            chunks, size = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("article fetch deadline")
                if connection.sock:
                    connection.sock.settimeout(min(6, remaining))
                chunk = response.read(min(65536, limit + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > limit:
                    raise ValueError("media too large")
            return b"".join(chunks), url
        finally:
            connection.close()
    raise ValueError("too many redirects")


class ArticleMedia(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        key = (values.get("property") or values.get("name") or "").lower()
        if tag == "meta" and key in ("og:image", "og:image:secure_url", "twitter:image"):
            image = values.get("content") or ""
            if image and image not in self.images:
                self.images.append(image)


@dataclass
class NewsMedia:
    image: bytes | None = None
    image_url: str = ""
    source_url: str = ""
    excerpt: str = ""


def article_media(item: dict[str, Any], *, photos: bool = True) -> NewsMedia:
    # A short, attributed extract; never use search summaries as a verbatim quote.
    text = str(item.get("content") or "")
    evidence = str(item.get("evidence") or "")
    if evidence and evidence in text:
        text = evidence
    text = " ".join(text.split())
    excerpt = text[:100]
    if len(text) > 100:
        if excerpt[-1:].isascii() and " " in excerpt[60:]:
            excerpt = excerpt.rsplit(" ", 1)[0]
        excerpt += "…"
    media = NewsMedia(source_url=str(item.get("url") or ""), excerpt=excerpt)
    if not photos:
        return media
    try:
        body, final = fetch_public(media.source_url, limit=MAX_HTML, types=("text/html",))
        parser = ArticleMedia()
        parser.feed(body.decode("utf-8", "replace"))
        for image in parser.images[:2]:
            # Generic newsroom logos are not related photographs.
            if any(x in image.casefold() for x in ("social-image", "default", "logo", "avatar")):
                continue
            try:
                data, image_url = fetch_public(urljoin(final, image), limit=MAX_IMAGE, types=("image/",))
                from PIL import Image
                with Image.open(io.BytesIO(data)) as picture:
                    w, h = picture.size
                    if w * h > MAX_PIXELS or w < 320 or h < 160:
                        continue
                    picture.verify()
                media.image, media.image_url = data, image_url
                break
            except Exception:  # A bad photograph must not suppress a verified story.
                continue
    except Exception:
        pass
    return media


async def collect_media(items: list[dict[str, Any]], *, photos: bool = True) -> list[NewsMedia]:
    semaphore = asyncio.Semaphore(3)
    async def one(item):
        async with semaphore:
            return await asyncio.to_thread(article_media, item, photos=photos)
    return list(await asyncio.gather(*(one(item) for item in items)))


def font_path() -> str:
    candidates = [
        str(Path(__file__).parent / "assets" / "NotoSansCJKsc-Regular.otf"),
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("需要中文字体：安装 fonts-wqy-zenhei 或 Noto Sans CJK")


def wrap(draw, text: str, font, width: int) -> list[str]:
    lines, line = [], ""
    tokens = re.findall(r"[A-Za-z0-9]+(?:[._/+\-][A-Za-z0-9]+)*|[^\n]|\n", str(text))
    units = []
    for token in tokens:
        units.extend(list(token) if draw.textlength(token, font=font) > width else [token])
    for char in units:
        if char == "\n":
            lines.append(line)
            line = ""
        elif char in "，。；：、！？）》】”’.,;:!?%)]}" and line:
            # Punctuation can hang slightly into the card's right gutter.
            line += char
        elif line and draw.textlength(line + char, font=font) > width:
            carry = line[-1] if line[-1] in "（《【“‘(" else ""
            lines.append(line[:-1].rstrip() if carry else line.rstrip())
            line = carry + char.lstrip()
        else:
            line += char
    if line:
        lines.append(line)
    return lines


def render_news(draft, *, now: float, media: list[NewsMedia] | None = None) -> bytes:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    if not 1 <= len(draft.items) <= 5:
        raise ValueError("news image requires 1 to 5 verified items")
    fonts = {size: ImageFont.truetype(font_path(), size) for size in (20, 23, 26, 29, 32, 42, 66)}
    measuring = ImageDraw.Draw(Image.new("RGB", (WIDTH, 10)))
    media = media or [NewsMedia() for _ in draft.items]
    cards = []
    for index, item in enumerate(draft.items):
        asset = media[index] if index < len(media) else NewsMedia()
        # These are the exact verified sentences used in the accompanying post.
        fact = wrap(measuring, item["fact"], fonts[32], 884)
        opinion = wrap(measuring, item.get("comment", ""), fonts[26], 884)
        excerpt = wrap(measuring, asset.excerpt, fonts[23], 836) if asset.excerpt and not asset.image else []
        height = 132 + 48 * len(fact) + (44 + 38 * len(opinion) if opinion else 0)
        height += 310 if asset.image else (68 + 34 * len(excerpt) if excerpt else 0)
        cards.append((item, asset, fact, opinion, excerpt, height))
    height = 350 + sum(card[-1] + 24 for card in cards) + 156
    image = Image.new("RGB", (WIDTH, height), "#eff3f7")
    draw = ImageDraw.Draw(image)
    ink, muted, accent = "#162c42", "#61758a", "#087f8c"
    draw.rectangle((0, 0, WIDTH, 285), fill=ink)
    draw.rounded_rectangle((52, 42, 288, 82), radius=20, fill=accent)
    draw.text((73, 46), "萧鱼儿 · 每日简报", font=fonts[23], fill="white")
    draw.text((48, 100), "AI 科技一图流", font=fonts[66], fill="white")
    stamp = time.strftime("%Y.%m.%d  /  %H:%M", time.localtime(now))
    draw.text((54, 208), f"{stamp}  截止更新", font=fonts[26], fill="#c4d8e4")
    draw.text((54, 303), f"{len(cards):02d} 条资讯  ·  产品 / 研究 / 科技人物", font=fonts[26], fill=muted)
    y = 350
    for index, (item, asset, facts, opinions, excerpt, card_height) in enumerate(cards, 1):
        draw.rounded_rectangle((36, y, 1044, y + card_height), radius=24, fill="white")
        draw.rounded_rectangle((68, y + 28, 127, y + 75), radius=12, fill=accent)
        draw.text((79, y + 29), f"{index:02d}", font=fonts[29], fill="white")
        source = str(item.get("source") or "来源")[:26]
        published = item.get("published")
        date = time.strftime("%m-%d", time.localtime(published)) if isinstance(published, (int, float)) else ""
        draw.text((146, y + 36), source + (f"  ·  {date}" if date else ""), font=fonts[23], fill=muted)
        cy = y + 100
        for line in facts:
            draw.text((72, cy), line, font=fonts[32], fill=ink)
            cy += 48
        if asset.image:
            with Image.open(io.BytesIO(asset.image)) as photo:
                photo = ImageOps.exif_transpose(photo)
                photo = ImageOps.fit(photo.convert("RGB"), (884, 252), method=Image.Resampling.LANCZOS)
                image.paste(photo, (72, cy + 8))
            draw.text((76, cy + 267), f"报道配图 · {source}", font=fonts[20], fill=muted)
            cy += 310
        elif excerpt:
            box_height = 58 + 34 * len(excerpt)
            draw.rounded_rectangle((72, cy + 8, 956, cy + box_height), radius=12, fill="#f1f6f8")
            draw.text((94, cy + 16), "原文摘录", font=fonts[20], fill=accent)
            for line in excerpt:
                draw.text((94, cy + 48), line, font=fonts[23], fill=muted)
                cy += 34
            cy += 68
        if opinions:
            draw.line((72, cy + 12, 956, cy + 12), fill="#e3ebef", width=2)
            cy += 28
            for line in opinions:
                draw.text((72, cy), line, font=fonts[26], fill=accent)
                cy += 38
        y += card_height + 24
    draw.text((54, y + 8), "来源详见配文链接 · 图片及摘录归原作者所有", font=fonts[23], fill=muted)
    draw.text((54, y + 48), "按核查后的资讯排版；事件进展以发布方后续公告为准。", font=fonts[23], fill=muted)
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
