from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from models import OutgoingStatus
from store import OperatorStore
from test_notification_retry import _Logger, _load_plugin_class


class _Generator:
    async def post(self, **kwargs):
        return "测试草稿标题", "测试草稿正文"


class _Persona:
    async def life_context(self):
        return "今天适合分享游戏日常。"


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


if __name__ == "__main__":
    unittest.main()
