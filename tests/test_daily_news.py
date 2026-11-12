"""每日 AI 早报：纯逻辑、调度、发帖状态机和私聊老板。"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import daily_news
from models import OutgoingAction, OutgoingStatus
from policy import has_fake_experience, is_news_publishable
from store import OperatorStore
from test_notification_retry import _load_plugin_class
from vendor_xiaoheihe.parsers import _rich_post_content

PLUGIN_TYPE = _load_plugin_class()
PLUGIN_MODULE = PLUGIN_TYPE._news_step.__globals__


def at(hour: int, minute: int = 0, day: int = 6) -> float:
    return time.mktime((2026, 11, day, hour, minute, 0, 0, 0, -1))


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


NOW = at(9)
# 四条不同网站的新闻；事实句里的数字和英文词都能在对应来源里找到。
SOURCES = [
    ("https://www.jiqizhixin.com/articles/nova2", "Nova 2 模型发布", "Nova 2 支持 128K 上下文，推理速度提升 3 倍。"),
    ("https://www.qbitai.com/2026/11/lumen.html", "Lumen-7B 开源", "Lumen-7B 代码生成准确率提升 40%，每百万 token 2 元。"),
    ("https://www.ithome.com/0/900/1.htm", "修图 App 上线 AI 消除", "免费用户每天可用 5 次 AI 消除。"),
    ("https://36kr.com/p/atlas", "Atlas 数据集公开", "Atlas 数据集包含 120 万张标注图片。"),
]
FACTS = [
    "Nova 2 支持 128K 上下文，推理速度提升 3 倍",
    "Lumen-7B 代码生成准确率提升 40%，每百万 token 2 元",
    "修图 App 上线 AI 消除，免费用户每天可用 5 次",
    "Atlas 数据集公开，包含 120 万张标注图片",
]
INTRO = "早上刷到几条 AI 新闻，随手记一下"
TAKE = "开源这边越来越热闹了"
SCENE = "清晨的书桌上放着一台笔记本电脑和一杯热茶"

class _Logger:
    def __init__(self):
        self.lines = []

    def _record(self, *args, **kwargs):
        self.lines.append(args)

    debug = info = warning = error = exception = _record


class _Web:
    """三条搜索词返回同样四条新闻（去重后剩 4 条）；IT之家原文读不到。"""

    def __init__(self, base: float = NOW, fail: bool = False):
        self.base = base
        self.fail = fail
        self.queries = []

    async def search(self, query, *, max_results=5):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("search down")
        hits = [
            {"url": url, "title": title, "snippet": snippet, "summary": "", "published_at": iso(self.base - 3600 * (n + 1))}
            for n, (url, title, snippet) in enumerate(SOURCES)
        ]
        return {"query": query, "results": hits}

    async def fetch(self, url):
        if "ithome" in url:
            raise RuntimeError("fetch blocked")
        snippet = next(s for u, _, s in SOURCES if u == url)
        return {"url": url, "content": f"![图](https://img.example/a.png) 正文：{snippet} 详见 https://example.com/x"}


class _Generator:
    def __init__(self, comment: str = "这个速度挺让人期待"):
        self.comment = comment
        self.select_calls = []

    async def news_select(self, *, candidates, recent_titles, min_items=3, max_items=5):
        self.select_calls.append([item["url"] for item in candidates])
        return list(range(1, min(len(candidates), max_items) + 1))

    async def news_write(self, *, items):
        rows = []
        for n, item in enumerate(items, start=1):
            index = next(i for i, (url, _, _) in enumerate(SOURCES) if url == item["url"])
            rows.append({"id": n, "fact": FACTS[index], "comment": self.comment if index == 0 else ""})
        return {"intro": INTRO, "items": rows, "take": TAKE, "image_scene": SCENE}

    async def news_review(self, *, written, items, recent, now):
        rows=[]
        for entry in written['items']:
            item=items[entry['id']-1];doc=item['documents'][0]
            rows.append({'id':entry['id'],'fact_checked':entry['fact'],'comment_checked':entry.get('comment',''),
                'verdict':'supported','atomic':True,'duplicate_of':'',
                'event':{'subjects':[item['title']],'action':'release','date':doc['published_raw'],
                         'source':1,'date_basis':'first_publication','date_quote':doc['date_text']},
                'claims':[{'source':1,'quote':doc['content']}],'comment_verdict':'opinion','comment_claims':[]})
        return {'items':rows,'intro_supported':True,'take_supported':True}

class _Drawing:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.requests = []
        self.source_images = []

    async def submit(self, prompt, *, request_id, model="", source_images=None):
        self.requests.append(request_id)
        self.source_images.append(source_images)
        return {"task_id": f"task-{request_id}"}

    async def wait(self, task_id, *, request_id, timeout_seconds=120):
        return {"status": "failed" if self.fail else "completed"}

    async def read_image(self, task_id, *, index=0):
        return b"\x89PNG-news"


class _ApiError(Exception):
    def __init__(self, message, category):
        super().__init__(message)
        self.category = category


class _Client:
    """假的小黑盒客户端：记下上传和发帖，按发出去的内容读回。"""

    def __init__(self, post_error=None, missing_topic=False):
        self.post_error = post_error
        self.missing_topic = missing_topic
        self.uploads = []
        self.posts = []
        self.readbacks = []
        self.store = None
        self.status_at_post = ""

    async def resolve_topic_names(self, names):
        if self.missing_topic:
            raise _ApiError("话题不存在", "topic_selection")
        return {name: "201" for name in names}

    async def upload_image_bytes(self, data, *, filename="x.png", mime_type="image/png"):
        self.uploads.append(filename)
        return {"url": "https://imgheybox.max-c.com/news.png", "width": 1024, "height": 768}

    async def create_post(self, **kwargs):
        if self.store is not None:
            self.status_at_post = self.store.get_outgoing("news:2026-11-06")["status"]
        self.posts.append(kwargs)
        if self.post_error is not None:
            raise self.post_error
        return {"accepted": True, "link_id": "9001"}

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        self.readbacks.append(post_id)
        if post_id != "9001" or not self.posts:
            return None
        # 按小黑盒读回的方式解析帖子正文：文字块用换行拼接，图片单独列出。
        body, images = _rich_post_content(json.dumps(self.posts[-1]["content_blocks"], ensure_ascii=False))
        return SimpleNamespace(title=self.posts[-1]["title"], body=body, image_urls=images, comments=[])


class _Runtime:
    def __init__(self, client, logged_in=True):
        self._client = client
        self.credentials = SimpleNamespace(nickname="萧鱼儿") if logged_in else None

    async def client(self):
        return self._client


class _Send:
    def __init__(self):
        self.texts = []
        self.images = []

    async def text(self, text, stream_id, **kwargs):
        self.texts.append(text)
        return True

    async def image(self, data, stream_id, **kwargs):
        self.images.append(data)
        return True


class _Chat:
    async def get_stream_by_user_id(self, user_id, platform="qq"):
        return {"stream_id": f"private-{user_id}", "user_id": user_id}


class _Persona:
    def __init__(self, activity="吃早饭"):
        self.activity = activity

    async def life_context(self):
        return f"当前活动：{self.activity}（家里）"

SECTIONS = ("plugin", "basic", "browse", "automation", "content", "news", "dm")


class _NewsCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        image_module = PLUGIN_MODULE["news_image"]
        media_patch = mock.patch.object(image_module, "collect_media", mock.AsyncMock(return_value=[]))
        render_patch = mock.patch.object(image_module, "render_news", return_value=b"\x89PNG-news")
        media_patch.start()
        render_patch.start()
        self.addCleanup(media_patch.stop)
        self.addCleanup(render_patch.stop)
        source_module=PLUGIN_MODULE['news_sources']
        original_kind=source_module.source_kind
        kind_patch=mock.patch.object(source_module,'source_kind',side_effect=lambda url:
            'primary' if url in [s[0] for s in SOURCES] else original_kind(url))
        kind_patch.start();self.addCleanup(kind_patch.stop)

    def make_plugin(self, *, mode="dry_run", store=None, drawing=None, client=None, logged_in=True,
                    web=None, activity="吃早饭", owner="10001", generate_image=True):
        plugin = PLUGIN_TYPE()
        config = plugin.config_model()
        # 假 SDK 的配置分组是类属性，每个用例换新实例，避免用例之间互相影响。
        for name in SECTIONS:
            setattr(config, name, type(getattr(config, name))())
        config.basic.mode = mode
        config.basic.owner_qq = owner
        config.news.enabled = True
        config.news.mode = mode
        config.news.post_time = "09:00"
        config.news.respect_sleep = True
        config.news.generate_image = generate_image
        plugin.config = config
        plugin._store = store or OperatorStore(self.dir / "state.db")
        self.addCleanup(plugin._store.close)
        plugin._generator = _Generator()
        plugin._drawing = drawing
        plugin._persona = _Persona(activity)
        self.client = client or _Client()
        self.client.store = plugin._store
        plugin._runtime = _Runtime(self.client, logged_in=logged_in)
        self.web = web or _Web()
        self.send = _Send()
        plugin.ctx = SimpleNamespace(logger=_Logger(), web=self.web, chat=_Chat(), send=self.send)
        async def fixture_source(item, *, now=None):
            original=next(s for u,_,s in SOURCES if u==item['url'])
            date=iso((time.time() if now is None else now)-3600)
            item['content']=original
            item['documents']=[{'url':item['url'],'title':item['title'],'content':original,'kind':'primary',
                'published_raw':date,'published_interval':[(time.time() if now is None else now)-3600]*2,
                'date_text':'First publication metadata: '+date}]
        plugin._fetch_news_source=fixture_source
        return plugin

    def row(self, plugin, day="2026-11-06"):
        return plugin._store.get_outgoing(f"news:{day}")

    async def approve(self, plugin, now=None, *, cancel=False):
        with mock.patch.object(PLUGIN_MODULE['time'],'time',return_value=at(9,1) if now is None else now):
            await plugin._confirm_news_approval(cancel=cancel)


class PureLogicTests(unittest.TestCase):
    def test_collect_candidates_filters_and_caps(self):
        def hit(url, title, hours_ago=1.0, published=True):
            return {"url": url, "title": title, "snippet": "", "summary": "",
                    "published_at": iso(NOW - hours_ago * 3600) if published else ""}

        hits = [
            hit("https://www.jiqizhixin.com/a", "A", 1),
            hit("https://jiqizhixin.com/a/?utm_source=x", "A 重复"),
            hit("https://www.jiqizhixin.com/b", "B", 2),
            hit("https://www.jiqizhixin.com/c", "C", 3),
            hit("https://www.qbitai.com/d", "D 没日期", published=False),
            hit("https://www.qbitai.com/e", "E 过期", hours_ago=40),
            hit("https://www.qbitai.com/f", "F 军事 AI 新进展"),
            hit("https://www.ithome.com/g", "G 前两天讲过"),
            hit("https://36kr.com/h", "H", 4),
        ]
        kept, dropped = daily_news.collect_candidates(
            hits, now=NOW, blocked=daily_news.DEFAULT_BLOCKED_KEYWORDS, recent_urls={"https://www.ithome.com/g"}
        )
        self.assertEqual([item["title"] for item in kept], ["A", "B", "H"])
        self.assertEqual(
            dropped, {"duplicate": 1, "site_cap": 1, "no_date": 1, "stale": 1, "blocked": 1, "recent": 1}
        )
        self.assertEqual(kept[0]["source"], "机器之心")

    def test_verify_written_drops_unsupported_and_fake_lines(self):
        picked = [
            {"url": u, "title": t, "snippet": s, "summary": "", "source": "来源"}
            for u, t, s in SOURCES
        ] + [{"url": "https://example.com/o", "title": "Orion 发布", "snippet": "Orion 正式发布。", "summary": "", "source": "来源"}]
        written = {
            "intro": "我是一个 AI，每天帮大家看新闻",
            "items": [
                {"id": 1, "fact": "Nova 2 支持 128K 上下文，推理速度提升 30 倍"},
                {"id": 2, "fact": FACTS[1], "comment": "我们工作室也在用"},
                {"id": 3, "fact": "修图 App 上线 AI 消除，详见 https://example.com/x"},
                {"id": 4, "fact": FACTS[3], "comment": "感觉能训练出 300 亿参数的模型"},
                {"id": 5, "fact": "Orion 今天正式发布"},
            ],
            "take": TAKE,
        }
        kept, dropped, intro, take = daily_news.verify_written(written, picked, daily_news.DEFAULT_BLOCKED_KEYWORDS)
        self.assertEqual([item["title"] for item in kept], ["Lumen-7B 开源", "Atlas 数据集公开"])
        self.assertEqual([item["comment"] for item in kept], ["", ""])
        reasons = {item["title"]: item["reason"] for item in dropped}
        self.assertEqual(reasons["Nova 2 模型发布"], "unsupported:30")
        self.assertEqual(reasons["Lumen-7B 开源"], "comment_fake_experience")
        self.assertEqual(reasons["修图 App 上线 AI 消除"], "url")
        self.assertEqual(reasons["Atlas 数据集公开"], "comment_unsupported:300")
        self.assertEqual(reasons["Orion 发布"], "relative_time")
        self.assertEqual(intro, daily_news.FALLBACK_INTRO)
        self.assertEqual(take, TAKE)

    def test_news_policy_allows_ai_terms_but_blocks_disclosure(self):
        self.assertEqual(is_news_publishable("每百万 token 2 元，单卡就能部署，代码生成更准"), (True, "ok"))
        self.assertEqual(is_news_publishable("GPU 服务器价格下来了"), (True, "ok"))
        self.assertEqual(is_news_publishable("我是 AI，帮你看新闻")[1], "self_disclosure")
        self.assertEqual(is_news_publishable("作为一个人工智能，我觉得")[1], "self_disclosure")
        self.assertTrue(is_news_publishable("我是 AI 爱好者，这个挺好玩")[0])
        self.assertTrue(is_news_publishable("作为程序员，我很期待")[0])
        self.assertEqual(is_news_publishable("感兴趣的关注我")[1], "promo")
        self.assertEqual(is_news_publishable("麦麦觉得不错")[1], "internal_word")
        self.assertEqual(is_news_publishable("详见 www.example.com")[1], "url")
        self.assertEqual(is_news_publishable("太突然了", ["突然"])[1], "blocked_keyword")
        self.assertTrue(has_fake_experience("我们工作室也在用"))
        self.assertTrue(has_fake_experience("我试了一下"))
        self.assertFalse(has_fake_experience("我还没用过"))
        self.assertFalse(has_fake_experience("用户可以免费试用"))

    def test_blocks_are_single_line_and_read_back_equal(self):
        items = [
            {"fact": "A&B 发布\n<新模型>，支持\"长文本\"", "source": "机器之心", "comment": ""},
            {"fact": "Atlas 数据集公开", "source": "36氪", "comment": "挺期待"},
        ]
        blocks = daily_news.build_blocks("早上刷到几条\nAI 新闻", items, "开源这边越来越热闹了")
        self.assertTrue(all("\n" not in block for block in blocks))
        self.assertEqual(blocks[1], "1. A＆B 发布 《新模型》，支持”长文本”（机器之心）")
        self.assertEqual(blocks[2], "2. Atlas 数据集公开（36氪）挺期待")
        draft = daily_news.NewsDraft(title=daily_news.build_title(NOW), blocks=blocks, items=[])
        self.assertEqual(draft.title, "11月6日 AI 早报")
        content = [{"type": "text", "text": block} for block in blocks] + [{"type": "img", "url": "https://x/1.png"}]
        body, images = _rich_post_content(json.dumps(content, ensure_ascii=False))
        context = SimpleNamespace(title=draft.title, body=body, image_urls=images)
        self.assertTrue(PLUGIN_MODULE["_same_post_content"](draft.title, draft.body, context, require_image=True))

    def test_image_prompt_strips_brands_and_falls_back(self):
        prompt = daily_news.image_prompt("GPT-5 发布会现场的大屏幕和观众席", daily_news.DEFAULT_BLOCKED_KEYWORDS)
        self.assertNotIn("GPT", prompt)
        self.assertNotIn("5", prompt.split("。")[0])
        self.assertIn(daily_news.IMAGE_STYLE, prompt)
        self.assertTrue(daily_news.image_prompt("", ()).startswith(daily_news.FALLBACK_SCENE))
        self.assertTrue(daily_news.image_prompt("军事演习现场", daily_news.DEFAULT_BLOCKED_KEYWORDS).startswith(daily_news.FALLBACK_SCENE))

    def test_failure_text_and_small_helpers(self):
        text = daily_news.failure_text("attempts_exhausted", last_error="search_failed:RuntimeError:search down")
        self.assertIn("试了 3 次都没成功", text)
        self.assertIn("联网搜索失败", text)
        self.assertIn("她还在睡", daily_news.failure_text("missed_window", skip_reason="asleep"))
        preview = daily_news.failure_text("", last_error="write_failed", header="AI 早报预览没生成出来")
        self.assertEqual(preview, "AI 早报预览没生成出来\n原因：写早报的模型调用失败")
        web_results = PLUGIN_MODULE["_web_results"]
        self.assertEqual(web_results({"results": [{"url": "a"}, "bad"]}), [{"url": "a"}])
        self.assertEqual(web_results({"result": {"results": [{"url": "b"}]}}), [{"url": "b"}])
        self.assertEqual(web_results({"error": "x"}), [])
        stream_id_of = PLUGIN_MODULE["_stream_id_of"]
        self.assertEqual(stream_id_of({"stream_id": "s1"}), "s1")
        self.assertEqual(stream_id_of(None), "")

class ScheduleTests(_NewsCase):
    def test_dry_run_runs_once_from_post_time(self):
        async def scenario():
            plugin = self.make_plugin(drawing=_Drawing())
            await plugin._maybe_daily_news(at(8, 59))
            self.assertIsNone(self.row(plugin))
            self.assertEqual(self.web.queries, [])
            await plugin._maybe_daily_news(at(9))
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.DRY_RUN.value)
            self.assertEqual(len(self.web.queries), 5)
            self.assertIn("11月6日", self.web.queries[0])
            self.assertEqual(self.client.posts, [])
            self.assertEqual(self.client.uploads, [])
            self.assertEqual(row["metadata"]["title"], "11月6日 AI 早报")
            self.assertEqual(len(row["metadata"]["sources"]), 4)
            self.assertEqual(len(self.send.texts), 1)
            report = self.send.texts[0]
            self.assertTrue(report.startswith("（小黑盒）AI 早报草稿（试运行，没发小黑盒）"))
            self.assertIn("1. Nova 2 支持 128K 上下文，推理速度提升 3 倍（机器之心）这个速度挺让人期待", report)
            self.assertIn("https://www.qbitai.com/2026/11/lumen.html", report)
            self.assertIn("搜到 20 条，能用 4 条", report)
            self.assertIn("“盒友杂谈”能找到", report)
            self.assertEqual(len(self.send.images), 1)
            self.assertEqual(plugin._drawing.requests, [])
            self.assertEqual(plugin._drawing.source_images, [])
            await plugin._maybe_daily_news(at(9, 30))
            self.assertEqual(len(self.web.queries), 5)
            self.assertEqual(len(self.send.texts), 1)

        asyncio.run(scenario())

    def test_next_day_uses_new_key_and_skips_recent_sources(self):
        async def scenario():
            plugin = self.make_plugin()
            await plugin._maybe_daily_news(at(9))
            self.web.base = at(9, day=7)
            await plugin._maybe_daily_news(at(9, day=7))
            row = self.row(plugin, "2026-11-07")
            self.assertEqual(row["status"], OutgoingStatus.FAILED.value)
            self.assertEqual(row["metadata"]["final_reason"], "few_candidates")
            self.assertEqual(self.row(plugin)["status"], OutgoingStatus.DRY_RUN.value)
            self.assertEqual(len(self.send.texts), 2)
            self.assertIn("符合条件的新闻不到 3 条", self.send.texts[1])
            self.assertIn("近 3 天讲过 4", self.send.texts[1])

        asyncio.run(scenario())

    def test_waits_while_asleep_and_reports_missed_window_once(self):
        async def scenario():
            plugin = self.make_plugin(activity="睡觉")
            for moment in (at(9), at(10), at(10, 59)):
                await plugin._maybe_daily_news(moment)
            self.assertIsNone(self.row(plugin))
            self.assertEqual(self.web.queries, [])
            self.assertEqual(self.send.texts, [])
            await plugin._maybe_daily_news(at(11))
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.FAILED.value)
            self.assertEqual(row["metadata"]["final_reason"], "missed_window")
            self.assertEqual(row["metadata"]["skip_reason"], "asleep")
            self.assertEqual(len(self.send.texts), 1)
            self.assertIn("到截止时间还没能开始（她还在睡）", self.send.texts[0])
            await plugin._maybe_daily_news(at(11, 30))
            self.assertEqual(len(self.send.texts), 1)

        asyncio.run(scenario())

    def test_started_after_deadline_stays_silent(self):
        async def scenario():
            plugin = self.make_plugin()
            await plugin._maybe_daily_news(at(11, 5))
            self.assertIsNone(self.row(plugin))
            self.assertEqual(self.send.texts, [])

        asyncio.run(scenario())

    def test_auto_mode_without_login_reminds_once_then_gives_up(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto", logged_in=False)
            await plugin._maybe_daily_news(at(9))
            await plugin._maybe_daily_news(at(9, 30))
            self.assertEqual(self.web.queries, [])
            self.assertEqual(len(self.send.texts), 1)
            self.assertIn("小黑盒没登录", self.send.texts[0])
            await plugin._maybe_daily_news(at(11))
            self.assertEqual(self.row(plugin)["metadata"]["skip_reason"], "not_logged_in")
            self.assertEqual(len(self.send.texts), 2)

        asyncio.run(scenario())

class PublishTests(_NewsCase):
    def test_auto_marks_sending_before_post_and_verifies(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto", drawing=_Drawing())
            await plugin._maybe_daily_news(at(9))
            self.assertEqual(self.client.posts, [])
            self.assertEqual(self.row(plugin)['status'], OutgoingStatus.AWAITING_APPROVAL.value)
            await self.approve(plugin)
            self.assertEqual(self.client.status_at_post, OutgoingStatus.SENDING.value)
            self.assertEqual(len(self.client.posts), 1)
            post = self.client.posts[0]
            self.assertEqual(post["title"], "11月6日 AI 早报")
            self.assertEqual(post["topic_ids"], "201")
            self.assertEqual(post["hashtags"], ["AI资讯"])
            self.assertNotIn("maibot", json.dumps(post, ensure_ascii=False).casefold())
            self.assertEqual(post["content_blocks"][-1]["type"], "img")
            self.assertEqual(self.client.uploads, ["news-2026-11-06.png"])
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            self.assertEqual(row["external_id"], "9001")
            self.assertEqual(len(self.send.texts), 2)
            self.assertIn("可以发送", self.send.texts[0])
            self.assertIn("AI 早报已发到小黑盒", self.send.texts[-1])
            self.assertIn("https://www.xiaoheihe.cn/app/bbs/link/9001", self.send.texts[-1])

        asyncio.run(scenario())

    def test_uncertain_post_error_is_never_resent(self):
        async def scenario():
            client = _Client(post_error=_ApiError("评论请求超时", "send_unknown"))
            plugin = self.make_plugin(mode="auto", client=client, generate_image=False)
            for moment in (at(9), at(9, 30), at(10, 30), at(11, 30)):
                await plugin._maybe_daily_news(moment)
            await self.approve(plugin)
            self.assertEqual(len(client.posts), 1)
            self.assertEqual(self.row(plugin)["status"], OutgoingStatus.SEND_UNKNOWN.value)
            self.assertEqual(len(self.send.texts), 2)
            self.assertIn("不会自动重发", self.send.texts[-1])

        asyncio.run(scenario())

    def test_rejected_post_fails_final_without_retry(self):
        async def scenario():
            client = _Client(post_error=_ApiError("错误的动态分区", "upstream_rejected"))
            plugin = self.make_plugin(mode="auto", client=client, generate_image=False)
            await plugin._maybe_daily_news(at(9))
            await plugin._maybe_daily_news(at(9, 30))
            await self.approve(plugin)
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.FAILED.value)
            self.assertEqual(row["metadata"]["final_reason"], "rejected")
            self.assertEqual(len(client.posts), 1)
            self.assertEqual(len(self.send.texts), 2)
            self.assertIn("小黑盒拒绝了这条帖子", self.send.texts[-1])
            self.assertIn("错误的动态分区", self.send.texts[-1])

        asyncio.run(scenario())

    def test_missing_topic_gives_up_before_searching(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto", client=_Client(missing_topic=True))
            await plugin._maybe_daily_news(at(9))
            self.assertEqual(self.web.queries, [])
            self.assertEqual(self.row(plugin)["metadata"]["final_reason"], "topic_selection")
            self.assertIn("发帖分区找不到", self.send.texts[0])

        asyncio.run(scenario())

    def test_layout_failure_posts_text_only(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto", drawing=_Drawing(fail=True))
            PLUGIN_MODULE["news_image"].render_news.side_effect = RuntimeError("no font")
            await plugin._maybe_daily_news(at(9))
            await self.approve(plugin)
            self.assertEqual(plugin._drawing.requests, [])
            self.assertEqual(self.client.uploads, [])
            self.assertTrue(all(block["type"] == "text" for block in self.client.posts[0]["content_blocks"]))
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            self.assertFalse(row["metadata"]["image_required"])
            self.assertIn("一图流排版失败", self.send.texts[0])
            self.assertEqual(self.send.images, [])

        asyncio.run(scenario())


class RecoveryTests(_NewsCase):
    def test_leftover_planned_record_is_retried(self):
        async def scenario():
            plugin = self.make_plugin(drawing=_Drawing())
            plugin._store.begin_outgoing(
                OutgoingAction("news:2026-11-06", "news_post", "", "", metadata={"attempts": 1, "mode": "dry_run"})
            )
            await plugin._maybe_daily_news(at(9, 30))
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.FAILED.value)
            self.assertEqual(row["metadata"]["last_error"], "interrupted")
            self.assertEqual(self.web.queries, [])
            await plugin._maybe_daily_news(at(9, 31))
            row = self.row(plugin)
            self.assertEqual(row["status"], OutgoingStatus.DRY_RUN.value)
            self.assertEqual(row["metadata"]["attempts"], 2)
            self.assertEqual(plugin._drawing.requests, [])
            self.assertEqual(len(self.send.texts), 1)

        asyncio.run(scenario())

    def test_leftover_sending_record_becomes_unknown_without_resend(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto")
            plugin._store.begin_outgoing(
                OutgoingAction("news:2026-11-06", "news_post", "", "", status=OutgoingStatus.SENDING)
            )
            await plugin._maybe_daily_news(at(9, 30))
            await plugin._maybe_daily_news(at(9, 45))
            self.assertEqual(self.row(plugin)["status"], OutgoingStatus.SEND_UNKNOWN.value)
            self.assertEqual(self.client.posts, [])
            self.assertEqual(len(self.send.texts), 1)
            self.assertIn("不会自动重发", self.send.texts[0])

        asyncio.run(scenario())

    def test_search_failures_retry_with_gap_then_give_up(self):
        async def scenario():
            plugin = self.make_plugin(web=_Web(fail=True))
            await plugin._maybe_daily_news(at(9))
            self.assertEqual(len(self.web.queries), 5)
            await plugin._maybe_daily_news(at(9, 5))
            self.assertEqual(len(self.web.queries), 5)
            await plugin._maybe_daily_news(at(9, 10))
            await plugin._maybe_daily_news(at(9, 20))
            self.assertEqual(len(self.web.queries), 15)
            self.assertEqual(self.send.texts, [])
            await plugin._maybe_daily_news(at(9, 30))
            row = self.row(plugin)
            self.assertEqual(row["metadata"]["final_reason"], "attempts_exhausted")
            self.assertEqual(len(self.web.queries), 15)
            self.assertEqual(len(self.send.texts), 1)
            self.assertIn("试了 3 次都没成功", self.send.texts[0])
            self.assertIn("联网搜索失败", self.send.texts[0])

        asyncio.run(scenario())

    def test_gives_up_when_next_retry_would_pass_deadline(self):
        async def scenario():
            plugin = self.make_plugin(web=_Web(fail=True))
            await plugin._maybe_daily_news(at(10, 52))
            await plugin._maybe_daily_news(at(10, 53))
            self.assertEqual(self.row(plugin)["metadata"]["final_reason"], "deadline")
            self.assertEqual(len(self.web.queries), 5)
            self.assertIn("到截止时间还没成功", self.send.texts[0])

        asyncio.run(scenario())

class OtherTests(_NewsCase):
    def test_missing_critic_never_sends_or_renders_a_draft(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            plugin._generator.news_review=mock.AsyncMock(return_value=None)
            await plugin._maybe_daily_news(at(9))
            row=self.row(plugin)
            self.assertEqual(row['status'],OutgoingStatus.FAILED.value)
            self.assertIn('verify_failed',row['metadata']['last_error'])
            self.assertEqual(self.client.posts,[]);self.assertEqual(self.send.images,[])
            PLUGIN_MODULE['news_image'].render_news.assert_not_called()
        asyncio.run(scenario())

    def test_no_original_page_cannot_fall_back_to_search_summary(self):
        async def scenario():
            plugin=self.make_plugin()
            plugin._fetch_news_source=PLUGIN_TYPE._fetch_news_source.__get__(plugin,PLUGIN_TYPE)
            plugin._generator.news_write=mock.AsyncMock()
            self.web.fetch=mock.AsyncMock(side_effect=AssertionError('no generated fallback'))
            with mock.patch.object(PLUGIN_MODULE['news_sources'],'fetch_document',mock.AsyncMock(side_effect=ValueError('blocked'))):
                with self.assertRaises(PLUGIN_MODULE['daily_news'].NewsRetry):
                    await plugin._build_news_draft(now=at(9),request_tag='offline')
            plugin._generator.news_write.assert_not_awaited()
            self.web.fetch.assert_not_awaited()
        asyncio.run(scenario())

    def test_unreviewed_draft_cannot_reach_upload_or_create_post(self):
        async def scenario():
            plugin=self.make_plugin(mode='auto')
            draft=daily_news.NewsDraft(title='test',blocks=['unverified'],items=[])
            with self.assertRaises(PLUGIN_MODULE['daily_news'].NewsRetry):
                await plugin._publish_news('news:2026-11-06',draft,self.client,'201')
            self.assertEqual(self.client.posts,[]);self.assertEqual(self.client.uploads,[])
        asyncio.run(scenario())

    def test_legacy_history_is_available_to_semantic_dedup(self):
        plugin=self.make_plugin()
        plugin._store.begin_outgoing(OutgoingAction('news:2026-11-05','news_post','','',
            metadata={'sources':[{'title':'Inkling release','url':'https://example.com/old'}],
                      'body':'1. Inkling 2 Small 发布（示例站）挺期待'}))
        rows=plugin._recent_news_events(at(9))
        self.assertEqual(rows[0]['fact'],'Inkling 2 Small 发布')
        self.assertEqual(rows[0]['id'],'2026-11-05:1')

    def test_preview_command_sends_draft_without_record(self):
        async def scenario():
            plugin = self.make_plugin(drawing=_Drawing(), web=_Web(base=time.time()))
            ok, _, _ = await plugin.handle_news_preview_command()
            self.assertTrue(ok)
            await plugin._news_preview_task
            self.assertEqual(plugin._store.list_outgoing(), [])
            self.assertTrue(self.send.texts[0].startswith("（小黑盒）AI 早报预览（没发小黑盒）"))
            self.assertEqual(len(self.send.images), 1)
            self.assertEqual(plugin._drawing.requests, [])
            plugin.config.basic.owner_qq = ""
            ok, _, _ = await plugin.handle_news_preview_command()
            self.assertFalse(ok)

        asyncio.run(scenario())

    def test_reconcile_skips_records_older_than_48_hours(self):
        async def scenario():
            plugin = self.make_plugin(mode="auto")
            store = plugin._store
            for key, link_id in (("news:2026-11-05", "9001"), ("news:2026-11-01", "8001")):
                store.begin_outgoing(OutgoingAction(key, "news_post", "", "", metadata={"link_id": link_id}))
                store.update_outgoing(key, OutgoingStatus.SEND_UNKNOWN)
            store._conn.execute(
                "UPDATE outgoing_actions SET created_at=? WHERE action_key=?",
                (time.time() - 72 * 3600, "news:2026-11-01"),
            )
            store._conn.commit()
            await plugin._reconcile_unknowns(self.client)
            self.assertEqual(self.client.readbacks, ["9001"])

        asyncio.run(scenario())

    def test_login_lost_notifies_once(self):
        async def scenario():
            plugin = self.make_plugin()
            plugin._had_credentials = True
            plugin._runtime.credentials = None
            await plugin._check_login_lost()
            await plugin._check_login_lost()
            self.assertEqual(len(self.send.texts), 1)
            self.assertIn("小黑盒登录失效了", self.send.texts[0])
            plugin._runtime.credentials = SimpleNamespace(nickname="萧鱼儿")
            await plugin._check_login_lost()
            plugin._runtime.credentials = None
            await plugin._check_login_lost()
            self.assertEqual(len(self.send.texts), 1)

        asyncio.run(scenario())

    def test_disabled_news_does_nothing(self):
        async def scenario():
            plugin = self.make_plugin()
            plugin.config.news.enabled = False
            await plugin._maybe_daily_news(at(9))
            self.assertIsNone(self.row(plugin))
            self.assertEqual(self.web.queries, [])

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
