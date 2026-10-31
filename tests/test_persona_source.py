"""人设来源：默认跟随宿主 MaiBot 的昵称和人格，插件里单独填写时优先，读不到时用中性人设。"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from content_generator import DEFAULT_PERSONA, ContentGenerator
from context_provider import PersonaContextProvider
from test_notification_retry import _load_plugin_class

PLUGIN_ROOT = Path(__file__).resolve().parents[1]


class _Logger:
    def debug(self, *args, **kwargs):
        pass

    info = warning = error = debug


class _HostConfig:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error
        self.calls = []

    async def get(self, key, default=None):
        self.calls.append(key)
        if self.error is not None:
            raise self.error
        return self.values.get(key, default)


class _RecordingLLM:
    def __init__(self, response):
        self.response = response
        self.prompts = []

    async def generate(self, prompt, temperature=None, max_tokens=None):
        self.prompts.append(prompt)
        return {"success": True, "response": self.response}


def _provider(config):
    ctx = SimpleNamespace(config=config, logger=_Logger(), api=None)
    return PersonaContextProvider(ctx, life_api_name="", identity_api_name="")


class HostPersonaTests(unittest.TestCase):
    def test_reads_nickname_and_personality_like_host_replyer(self):
        config = _HostConfig({"bot.nickname": "小鱼", "personality.personality": "是一个爱打游戏的大学生。"})

        text = asyncio.run(_provider(config).host_persona())

        self.assertEqual(text, "你的名字是小鱼。\n是一个爱打游戏的大学生。")
        self.assertEqual(config.calls, ["bot.nickname", "personality.personality"])

    def test_unreadable_host_config_returns_empty(self):
        text = asyncio.run(_provider(_HostConfig(error=RuntimeError("E_CAPABILITY_DENIED"))).host_persona())

        self.assertEqual(text, "")

    def test_non_string_values_are_ignored(self):
        config = _HostConfig({"bot.nickname": {"oops": 1}, "personality.personality": "是学生。"})

        self.assertEqual(asyncio.run(_provider(config).host_persona()), "是学生。")

    def test_result_is_cached_for_a_minute(self):
        config = _HostConfig({"bot.nickname": "小鱼", "personality.personality": "是学生。"})
        provider = _provider(config)

        async def twice():
            await provider.host_persona()
            await provider.host_persona()

        asyncio.run(twice())
        self.assertEqual(len(config.calls), 2)


class GeneratorPersonaTests(unittest.TestCase):
    def _system_prompts(self, persona, response="这个更新挺实在的"):
        llm = _RecordingLLM(response)
        generator = ContentGenerator(llm, persona=persona)

        async def run():
            await generator.reply(post_title="标题", post_body="正文", proactive=True)
            await generator.reply(post_title="标题", post_body="正文", comment="对方的话")
            await generator.direct_message_reply(sender_name="盒友", message="在吗")
            llm.response = '{"title":"今天","body":"打了两把"}'
            await generator.post(life_context="在家", topic_hint="游戏日常")

        asyncio.run(run())
        return [prompt[0]["content"] for prompt in llm.prompts]

    def test_every_prompt_starts_with_the_given_persona(self):
        async def persona():
            return "你的名字是小鱼。\n是学生。"

        prompts = self._system_prompts(persona)

        self.assertEqual(len(prompts), 4)
        for content in prompts:
            self.assertTrue(content.startswith("你的名字是小鱼。\n是学生。\n"), content[:40])
            self.assertNotIn("潮汐工作室", content)
            self.assertNotIn("maibot", content)

    def test_falls_back_to_neutral_persona(self):
        async def empty():
            return ""

        async def broken():
            raise RuntimeError("boom")

        for persona in (None, empty, broken):
            for content in self._system_prompts(persona):
                self.assertTrue(content.startswith(DEFAULT_PERSONA + "\n"), content[:40])


class PluginPersonaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def _plugin(self, override=""):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.content.persona_override = override
        plugin._persona = _provider(_HostConfig({"bot.nickname": "小鱼", "personality.personality": "是学生。"}))
        return plugin

    def test_follows_host_persona_by_default(self):
        self.assertEqual(asyncio.run(self._plugin()._persona_identity()), "你的名字是小鱼。\n是学生。")

    def test_plugin_override_wins(self):
        plugin = self._plugin(override="  你是阿杰，平时只玩单机。  ")

        self.assertEqual(asyncio.run(plugin._persona_identity()), "你是阿杰，平时只玩单机。")

    def test_override_is_a_new_install_default_of_empty(self):
        self.assertEqual(self.plugin_type.config_model().content.persona_override, "")


class ManifestTests(unittest.TestCase):
    def test_declares_config_get_for_host_persona(self):
        manifest = json.loads((PLUGIN_ROOT / "_manifest.json").read_text(encoding="utf-8"))

        self.assertIn("config.get", manifest["capabilities"])


if __name__ == "__main__":
    unittest.main()
