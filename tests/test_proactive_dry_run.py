from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from models import OutgoingStatus
from store import OperatorStore
from test_notification_retry import _Logger, _load_plugin_class
from content_generator import post_image_prompt
from unittest import mock


class _Generator:
    async def post(self, **kwargs):
        return "测试草稿标题", "测试草稿正文"


class _Persona:
    async def life_context(self):
        return "今天适合分享游戏日常。"

    async def identity_reference(self):
        return b"reference"


class _Client:
    def __init__(self):
        self.create_post_calls = 0
        self.upload_image_calls = 0

    async def resolve_topic_names(self, names):
        return {name: str(index + 100) for index, name in enumerate(names)}

    async def upload_image_bytes(self, *args, **kwargs):
        self.upload_image_calls += 1
        raise AssertionError("dry_run 不应上传图片")

    async def create_post(self, **kwargs):
        self.create_post_calls += 1
        raise AssertionError("dry_run 不应调用 create_post")


class _FailingDrawing:
    async def submit(self, *args, **kwargs):
        return {"task_id": "task-timeout"}

    async def wait(self, *args, **kwargs):
        raise RuntimeError("upstream timeout")


class _PublishClient:
    def __init__(self):
        self.posts = []

    async def resolve_topic_names(self, names):
        return {name: str(index + 100) for index, name in enumerate(names)}

    async def upload_image_bytes(self, *args, **kwargs):
        raise AssertionError("配图失败降级时不应上传图片")

    async def create_post(self, **kwargs):
        self.posts.append(kwargs)
        return {"link_id": "post-text-fallback"}

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        return SimpleNamespace(
            title="测试草稿标题",
            body="测试草稿正文",
            image_urls=[],
        )


class ProactiveDryRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def test_dry_run_records_proactive_post_without_external_write(self):
        async def run(directory: Path):
            plugin = self.plugin_type()
            plugin.config = plugin.config_model()
            plugin.config.basic.enabled = True
            plugin.config.basic.mode = "dry_run"
            plugin.config.automation.posts_per_day = 1
            plugin.config.automation.post_active_start_time = "00:00"
            plugin.config.automation.post_active_end_time = "00:00"
            plugin.config.automation.post_target_primary = "Steam"
            plugin.config.automation.post_target_secondary = ""
            plugin.config.content.generate_post_images = True
            plugin._store = OperatorStore(directory / "state.db")
            plugin._generator = _Generator()
            plugin._persona = _Persona()
            plugin._drawing = None
            plugin.ctx = SimpleNamespace(logger=_Logger())
            client = _Client()

            await plugin._create_proactive_post(client)

            self.assertEqual(client.create_post_calls, 0)
            self.assertEqual(client.upload_image_calls, 0)
            rows = [
                row
                for row in plugin._store.list_outgoing()
                if row.get("kind") == "proactive_post"
            ]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], OutgoingStatus.DRY_RUN.value)
            self.assertEqual(rows[0]["metadata"]["title"], "测试草稿标题")
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_image_failure_falls_back_to_verified_text_post(self):
        async def run(directory: Path):
            plugin = self.plugin_type()
            plugin.config = plugin.config_model()
            plugin.config.basic.enabled = True
            plugin.config.basic.mode = "auto"
            plugin.config.automation.post_active_start_time = "00:00"
            plugin.config.automation.post_active_end_time = "00:00"
            plugin.config.automation.post_target_primary = "Steam"
            plugin.config.automation.post_target_secondary = ""
            plugin.config.content.generate_post_images = True
            plugin._store = OperatorStore(directory / "state.db")
            plugin._generator = _Generator()
            plugin._persona = _Persona()
            plugin._drawing = _FailingDrawing()
            plugin.ctx = SimpleNamespace(logger=_Logger())
            client = _PublishClient()

            await plugin._create_proactive_post(client)

            self.assertEqual(len(client.posts), 1)
            self.assertEqual(
                client.posts[0]["content_blocks"],
                [{"type": "text", "text": "测试草稿正文"}],
            )
            row = plugin._store.list_outgoing(limit=1)[0]
            self.assertEqual(row["status"], OutgoingStatus.VERIFIED.value)
            self.assertEqual(row["external_id"], "post-text-fallback")
            self.assertTrue(row["metadata"]["image_fallback"])
            self.assertFalse(row["metadata"]["image_required"])
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_disabled_daily_posts_skip_generation_and_retries(self):
        async def run(directory):
            plugin=self.plugin_type()
            plugin.config=plugin.config_model()
            plugin.config.automation=type(plugin.config.automation)()
            plugin.config.automation.daily_posts_enabled=False
            plugin.config.news=type(plugin.config.news)()
            plugin.config.news.enabled=True
            plugin._generator=mock.AsyncMock()
            plugin._persona=mock.AsyncMock()
            plugin._store=OperatorStore(directory/'state.db')
            client=_Client()
            await plugin._create_proactive_post(client)
            await plugin._retry_pre_send_proactive_posts(client)
            self.assertEqual(plugin._store.list_outgoing(),[])
            plugin._generator.post.assert_not_awaited()
            self.assertTrue(plugin.config.news.enabled)
            self.assertEqual(client.create_post_calls,0)
            plugin._store.close()
        with tempfile.TemporaryDirectory() as tmp:asyncio.run(run(Path(tmp)))

    def test_post_toggle_preserves_browsing(self):
        async def run():
            plugin=self.plugin_type()
            plugin.config=plugin.config_model()
            plugin.config.basic=type(plugin.config.basic)()
            plugin.config.basic.enabled=True
            plugin.config.basic.mode='auto'
            plugin.config.automation=type(plugin.config.automation)()
            plugin.config.automation.daily_posts_enabled=False
            plugin._persona_asleep=mock.AsyncMock(return_value=False)
            plugin._create_proactive_post=mock.AsyncMock()
            plugin._browse_session=mock.AsyncMock()
            await plugin._proactive_once(object(),post_window_active=True,browse_window_active=True)
            plugin._create_proactive_post.assert_not_awaited()
            plugin._browse_session.assert_awaited_once()
        asyncio.run(run())

    def test_illustration_uses_configured_name_without_software_branding(self):
        prompt=post_image_prompt('海边散步','在海边吹风。','萧鱼儿')
        self.assertIn('萧鱼儿',prompt)
        self.assertNotIn('maibot',prompt.casefold())
        self.assertIn('不要社交软件界面',prompt)
        self.assertIn('不要任何文字',prompt)


if __name__ == "__main__":
    unittest.main()
