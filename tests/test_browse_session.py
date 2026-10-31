"""逛推荐流：不定时、多看少评、评论按天分摊、点赞额度与幂等、睡觉时不逛。"""

from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from models import OutgoingAction, OutgoingStatus
from store import OperatorStore
from test_notification_retry import _load_plugin_class
from vendor_xiaoheihe.models import ApiPage


class _Logger:
    def __init__(self):
        self.lines = []

    def _add(self, message, *args, **kwargs):
        self.lines.append(message % args if args else message)

    info = warning = error = debug = _add


class _FixedRng:
    """random() 恒为 0：概率大于 0 的判定都会通过；额度用完时概率为 0，仍然不会触发。"""

    def random(self):
        return 0.0

    def randint(self, low, high):
        return low

    def uniform(self, low, high):
        return low


class _Generator:
    async def reply(self, **kwargs):
        return "这个画风看着挺舒服的，周末想试试。"


class _Persona:
    def __init__(self, text="当前活动：打游戏（客厅） 情绪：7.0/10"):
        self.text = text

    async def life_context(self):
        return self.text


def _strict(post_id):
    return {
        "post_id": post_id,
        "title": f"新出的独立游戏 {post_id}",
        "content": "画风很舒服的单机游戏",
        "section_names": ["PC游戏"],
        "author": {"uid": "someone"},
    }


class _FeedClient:
    def __init__(self, items):
        self.items = items
        self.feed_calls = 0
        self.opened = []
        self.sent = []
        self.likes = []

    async def fetch_feed(self, *, offset=0):
        self.feed_calls += 1
        return ApiPage(items=list(self.items), next_cursor="", has_more=False)

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        if not root_comment_id:
            self.opened.append(post_id)
        return SimpleNamespace(
            title="帖子标题",
            body="帖子正文",
            comments=[{"text": text} for _, text in self.sent],
        )

    async def send_comment(self, route, content):
        self.sent.append((route.post_id, content))
        return SimpleNamespace(confirmed=True, external_comment_id=f"c{len(self.sent)}")

    async def like_post(self, post_id):
        self.likes.append(post_id)
        return {"status": "ok"}


async def _instant(low, high):
    return None


class BrowseSessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _make_plugin(self, directory: Path, *, mode="auto", comments=5, likes=6, persona=None):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.basic.enabled = True
        plugin.config.basic.mode = mode
        plugin.config.browse.feed_active_start_time = "00:00"
        plugin.config.browse.feed_active_end_time = "00:00"
        plugin.config.browse.feed_interval_seconds = 600
        plugin.config.automation.posts_per_day = 0
        plugin.config.automation.proactive_comments_per_day = comments
        plugin.config.automation.likes_per_day = likes
        plugin.config.automation.send_cooldown_seconds = 0
        plugin._store = OperatorStore(directory / "state.db")
        plugin._generator = _Generator()
        plugin._persona = persona or _Persona()
        plugin._rng = _FixedRng()
        plugin._pause = _instant
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def _rows(self, plugin, kind):
        return [row for row in plugin._store.list_outgoing(limit=100) if row.get("kind") == kind]

    def test_keeps_browsing_and_liking_after_comment_quota_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp))
            for index in range(5):
                key = f"feed_comment:old{index}:x"
                plugin._store.begin_outgoing(OutgoingAction(key, "proactive_comment", f"old{index}", "旧评论"))
                plugin._store.update_outgoing(key, OutgoingStatus.VERIFIED)
            client = _FeedClient([_strict("p1"), _strict("p2")])

            asyncio.run(plugin._proactive_once(client, post_window_active=False, browse_window_active=True))

            self.assertEqual(client.feed_calls, 1)
            self.assertEqual(client.opened, ["p1", "p2"])
            self.assertEqual(client.sent, [])
            self.assertEqual(client.likes, ["p1", "p2"])
            plugin._store.close()

    def test_opens_non_game_posts_without_interacting(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp))
            chat = {
                "post_id": "chat1",
                "title": "新装的电脑配置，服务器延迟好高",
                "content": "随便聊聊",
                "section_names": ["盒友日常"],
                "author": {"uid": "someone"},
            }
            client = _FeedClient([chat, _strict("p1")])

            asyncio.run(plugin._browse_session(client))

            self.assertEqual(client.opened, ["chat1", "p1"])
            self.assertEqual([post_id for post_id, _ in client.sent], ["p1"])
            self.assertEqual(client.likes, ["p1"])
            plugin._store.close()

    def test_comments_at_most_once_per_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp), likes=0)
            client = _FeedClient([_strict("p1"), _strict("p2")])

            asyncio.run(plugin._proactive_once(client, post_window_active=False, browse_window_active=True))

            self.assertEqual(client.opened, ["p1", "p2"])
            self.assertEqual([post_id for post_id, _ in client.sent], ["p1"])
            rows = self._rows(plugin, "proactive_comment")
            self.assertEqual([row["status"] for row in rows], [OutgoingStatus.VERIFIED.value])
            self.assertEqual(client.likes, [])
            plugin._store.close()

    def test_like_quota_and_one_like_per_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp), comments=0, likes=1)
            client = _FeedClient([_strict("p1"), _strict("p2")])

            asyncio.run(plugin._browse_session(client))
            asyncio.run(plugin._browse_session(client))
            self.assertEqual(client.likes, ["p1"])

            plugin.config.automation.likes_per_day = 6
            asyncio.run(plugin._browse_session(client))
            self.assertEqual(client.likes, ["p1", "p2"])
            self.assertEqual(len(self._rows(plugin, "post_like")), 2)
            plugin._store.close()

    def test_skips_while_asleep_but_browses_before_bed(self):
        cases = (
            ("当前活动：睡觉（卧室） 情绪：6.0/10", 0),
            ("当前活动：小睡（沙发） 情绪：6.0/10", 0),
            ("当前活动：准备睡觉（卧室） 情绪：6.0/10", 1),
        )
        for text, expected_feed_calls in cases:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                plugin = self._make_plugin(Path(tmp), persona=_Persona(text))
                client = _FeedClient([_strict("p1")])

                asyncio.run(plugin._proactive_once(client, post_window_active=True, browse_window_active=True))

                self.assertEqual(client.feed_calls, expected_feed_calls)
                plugin._store.close()

    def test_dry_run_records_like_without_calling_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp), mode="dry_run", comments=0)
            client = _FeedClient([_strict("p1")])

            asyncio.run(plugin._browse_session(client))

            self.assertEqual(client.likes, [])
            rows = self._rows(plugin, "post_like")
            self.assertEqual([row["status"] for row in rows], [OutgoingStatus.DRY_RUN.value])
            plugin._store.close()

    def test_comment_chance_spreads_quota_over_the_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp))
            plugin.config.browse.feed_interval_seconds = 3600

            morning = plugin._daily_chance("proactive_comment", 5, 15 * 3600)
            last_hour = plugin._daily_chance("proactive_comment", 5, 1800)

            self.assertAlmostEqual(morning, 5 / 15)
            self.assertEqual(last_hour, 1.0)
            self.assertEqual(plugin._daily_chance("proactive_comment", 0, 15 * 3600), 0.0)
            self.assertLessEqual(plugin._daily_chance("post_like", 6, 1800, cap=0.7), 0.7)
            plugin._store.close()

    def test_feed_gap_is_jittered_around_configured_interval(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp))
            plugin.config.browse.feed_interval_seconds = 3600
            plugin._rng = __import__("random").Random(7)

            gaps = [plugin._next_feed_gap() for _ in range(300)]

            self.assertTrue(all(1800 <= gap <= 5400 for gap in gaps))
            self.assertGreater(max(gaps) - min(gaps), 2400)
            plugin._store.close()

    def test_window_seconds_left(self):
        seconds_left = self.plugin_type._proactive_once.__globals__["_window_seconds_left"]
        today = time.localtime()
        noon = time.mktime((today.tm_year, today.tm_mon, today.tm_mday, 12, 0, 0, 0, 0, -1))

        self.assertEqual(seconds_left("08:00", "23:00", noon), 11 * 3600)
        self.assertEqual(seconds_left("08:00", "23:00", noon - 5 * 3600), 0.0)
        self.assertEqual(seconds_left("00:00", "00:00", noon), 12 * 3600)
        self.assertEqual(seconds_left("11:00", "02:00", noon), 12 * 3600)

    def test_likes_do_not_hold_the_comment_cooldown(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin = self._make_plugin(Path(tmp))
            plugin.config.automation.send_cooldown_seconds = 120
            plugin._store.begin_outgoing(OutgoingAction("post_like:p1", "post_like", "p1", ""))
            plugin._store.update_outgoing("post_like:p1", OutgoingStatus.VERIFIED)
            self.assertTrue(plugin._cooldown_ready())

            plugin._store.begin_outgoing(OutgoingAction("feed_comment:p2:x", "proactive_comment", "p2", "评论"))
            plugin._store.update_outgoing("feed_comment:p2:x", OutgoingStatus.VERIFIED)
            self.assertFalse(plugin._cooldown_ready())
            plugin._store.close()


if __name__ == "__main__":
    unittest.main()
