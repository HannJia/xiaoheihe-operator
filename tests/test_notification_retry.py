from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

from models import IncomingEvent, OutgoingAction, OutgoingStatus
from store import OperatorStore
from vendor_xiaoheihe.models import ApiPage, NotificationType


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _load_plugin_class():
    sdk = types.ModuleType("maibot_sdk")

    def field(*, default=None, default_factory=None, **kwargs):
        return default_factory() if default_factory is not None else default

    def decorator(*args, **kwargs):
        def apply(function):
            return function

        return apply

    class PluginConfigBase:
        pass

    class MaiBotPlugin:
        def __init__(self):
            self.ctx = None

    sdk.Command = decorator
    sdk.API = decorator
    sdk.Field = field
    sdk.MaiBotPlugin = MaiBotPlugin
    sdk.PluginConfigBase = PluginConfigBase
    sdk.Tool = decorator

    httpx = types.ModuleType("httpx")
    httpx.Timeout = lambda *args, **kwargs: object()
    httpx.Limits = lambda *args, **kwargs: object()
    httpx.AsyncClient = type("AsyncClient", (), {})
    httpx.TimeoutException = type("TimeoutException", (Exception,), {})
    httpx.NetworkError = type("NetworkError", (Exception,), {})
    httpx.HTTPStatusError = type("HTTPStatusError", (Exception,), {})

    module_name = "xiaoheihe_notification_retry_test_plugin"
    spec = importlib.util.spec_from_file_location(
        module_name,
        PLUGIN_ROOT / "plugin.py",
        submodule_search_locations=[str(PLUGIN_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"maibot_sdk": sdk, "httpx": httpx}):
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
    return module.XiaoheiheOperatorPlugin


class _Logger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class _Generator:
    async def reply(self, **kwargs):
        return "听起来挺有意思的。"


class _Client:
    def __init__(self, pages, context=None):
        self.pages = pages
        self.context = context
        self.calls = []

    async def fetch_notifications(self, event_type, *, cursor="", page=1, page_size=20):
        self.calls.append((page, cursor))
        return self.pages[page]

    async def fetch_thread_context(self, post_id, *, root_comment_id=""):
        return self.context


class _CommentClient(_Client):
    def __init__(self, pages, context, store=None, action_key=""):
        super().__init__(pages, context)
        self.store = store
        self.action_key = action_key
        self.send_calls = 0
        self.observed_status = ""

    async def send_comment(self, route, content):
        self.send_calls += 1
        if self.store is not None:
            self.observed_status = str(
                self.store.get_outgoing(self.action_key)["status"]
            )
        return SimpleNamespace(confirmed=True, external_comment_id="comment-1")


def _notification(key: str) -> dict[str, str]:
    return {
        "post_id": "post-1",
        "notification_id": key,
        "comment_id": f"comment-{key}",
        "root_comment_id": "root-1",
        "text": "这款游戏怎么样？",
    }


class NotificationRetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin_type = _load_plugin_class()

    def make_plugin(self, directory: Path, *, quota: int = 1):
        plugin = self.plugin_type()
        plugin.config = plugin.config_model()
        plugin.config.plugin.mode = "dry_run"
        plugin.config.plugin.notification_replies_per_day = quota
        plugin.config.plugin.poll_interval_seconds = 60
        plugin._store = OperatorStore(directory / "state.db")
        plugin._generator = _Generator()
        plugin.ctx = SimpleNamespace(logger=_Logger())
        return plugin

    def test_first_poll_baselines_old_notifications_without_replying(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory)
            client = _Client({1: ApiPage(items=[_notification("old")], has_more=False)})
            await plugin._poll_notifications(client, NotificationType.REPLY)
            self.assertEqual(plugin._store.get_incoming("reply:old")["state"], "done")
            self.assertEqual(plugin._store.list_outgoing(), [])
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_new_notification_is_found_across_offset_pages_and_dry_run_is_terminal(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory)
            plugin._store.remember_incoming_baseline(
                "reply:old", "reply", {"event_key": "reply:old", "kind": "reply", "post_id": "post-1", "text": "old"}
            )
            # A legacy deployment may have persisted a numeric offset such as
            # 20; the next poll must ignore it and restart at the newest page.
            plugin._store.set_cursor("default", "reply", "20", baseline_ready=True)
            client = _Client(
                {
                    1: ApiPage(items=[_notification("new")], has_more=True),
                    2: ApiPage(items=[_notification("old")], has_more=True),
                },
                context=SimpleNamespace(title="测试游戏", body="游戏介绍"),
            )
            await plugin._poll_notifications(client, NotificationType.REPLY)
            incoming = plugin._store.get_incoming("reply:new")
            self.assertEqual(incoming["state"], "done")
            outgoing = plugin._store.get_outgoing("reply:reply:new")
            self.assertEqual(outgoing["status"], OutgoingStatus.DRY_RUN.value)
            self.assertEqual(client.calls, [(1, ""), (2, "")])
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_quota_exhaustion_defers_event_without_losing_it(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory, quota=1)
            consumed = OutgoingAction("already-used", "notification_reply", "post-1", "earlier")
            plugin._store.begin_outgoing(consumed)
            plugin._store.update_outgoing("already-used", OutgoingStatus.VERIFIED)
            self.assertTrue(plugin._store.claim_incoming(
                "reply:pending", "reply", {"event_key": "reply:pending", "post_id": "post-1", "text": "question"}
            ))
            event = IncomingEvent("reply:pending", "reply", "post-1", text="question")
            await plugin._process_event(_Client({}, context=SimpleNamespace(title="t", body="b")), event)
            row = plugin._store.get_incoming("reply:pending")
            self.assertEqual(row["state"], "pending")
            self.assertGreater(row["next_attempt_at"], 0)
            self.assertEqual(row["attempts"], 0)
            self.assertIsNone(plugin._store.get_outgoing("reply:reply:pending"))
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_attempted_notification_write_is_terminal_and_never_resent(self):
        async def run(directory: Path, status: OutgoingStatus):
            plugin = self.make_plugin(directory, quota=1)
            plugin.config.plugin.mode = "auto"
            key = "reply:attempted"
            plugin._store.claim_incoming(
                "attempted", "reply", {"event_key": "attempted", "post_id": "post-1", "text": "question"}
            )
            action = OutgoingAction(key, "notification_reply", "post-1", "existing reply")
            self.assertTrue(plugin._store.begin_outgoing(action))
            plugin._store.update_outgoing(key, status, error="upstream rejected")
            client = _Client({}, context=SimpleNamespace(title="t", body="b"))

            await plugin._process_event(client, IncomingEvent("attempted", "reply", "post-1", text="question"))

            self.assertEqual(plugin._store.get_incoming("attempted")["state"], "done")
            self.assertEqual(plugin._store.get_outgoing(key)["status"], status.value)
            self.assertEqual(client.calls, [])
            plugin._store.close()

        for status in (OutgoingStatus.FAILED, OutgoingStatus.SEND_UNKNOWN):
            with self.subTest(status=status.value), tempfile.TemporaryDirectory() as tmp:
                asyncio.run(run(Path(tmp), status))

    def test_failed_dispatch_does_not_requeue_notification(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory)
            plugin._store.claim_incoming(
                "failed-dispatch", "reply", {"event_key": "failed-dispatch", "post_id": "post-1", "text": "question"}
            )
            key = "reply:failed-dispatch"
            self.assertTrue(
                plugin._store.begin_outgoing(
                    OutgoingAction(key, "notification_reply", "post-1", "existing reply")
                )
            )
            plugin._store.update_outgoing(key, OutgoingStatus.FAILED, error="upstream rejected")

            plugin._finish_incoming_after_dispatch("failed-dispatch", key)

            self.assertEqual(plugin._store.get_incoming("failed-dispatch")["state"], "done")
            self.assertEqual(plugin._store.list_pending_incoming(kind="reply"), [])
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_missing_context_defers_for_retry(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory)
            plugin._store.claim_incoming(
                "reply:context", "reply", {"event_key": "reply:context", "post_id": "post-1", "text": "question"}
            )
            event = IncomingEvent("reply:context", "reply", "post-1", text="question")
            await plugin._process_event(_Client({}, context=None), event)
            row = plugin._store.get_incoming("reply:context")
            self.assertEqual(row["state"], "pending")
            self.assertEqual(row["last_error"], "thread_context_missing")
            self.assertIsNone(plugin._store.get_outgoing("reply:reply:context"))
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_first_real_comment_is_not_blocked_by_its_own_cooldown_record(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory)
            plugin.config.plugin.mode = "auto"
            plugin.config.plugin.send_cooldown_seconds = 120
            key = "comment:first"
            plugin._store.begin_outgoing(
                OutgoingAction(key, "notification_reply", "post-1", "reply", metadata={"post_id": "post-1"})
            )
            client = _CommentClient(
                {},
                SimpleNamespace(comments=[{"commentid": "comment-1", "text": "reply"}]),
                store=plugin._store,
                action_key=key,
            )
            await plugin._dispatch_comment(
                client,
                action_key=key,
                route=SimpleNamespace(post_id="post-1", root_comment_id="", parent_comment_id=""),
                content="reply",
            )
            self.assertEqual(client.send_calls, 1)
            self.assertEqual(client.observed_status, OutgoingStatus.SENDING.value)
            self.assertEqual(
                plugin._store.get_outgoing(key)["status"], OutgoingStatus.VERIFIED.value
            )
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))

    def test_sending_action_is_readback_reconciled_without_resending(self):
        async def run(directory: Path):
            plugin = self.make_plugin(directory)
            key = "comment:crashed"
            action = OutgoingAction(
                key,
                "notification_reply",
                "post-1",
                "reply",
                metadata={"post_id": "post-1"},
            )
            plugin._store.begin_outgoing(action)
            plugin._store.update_outgoing(key, OutgoingStatus.SENDING, error="send_started")
            client = _CommentClient({}, SimpleNamespace(comments=[{"text": "reply"}]))
            await plugin._reconcile_unknowns(client)
            self.assertEqual(client.send_calls, 0)
            self.assertEqual(
                plugin._store.get_outgoing(key)["status"], OutgoingStatus.VERIFIED.value
            )
            plugin._store.close()

        with tempfile.TemporaryDirectory() as tmp:
            asyncio.run(run(Path(tmp)))


if __name__ == "__main__":
    unittest.main()
