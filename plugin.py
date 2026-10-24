from __future__ import annotations

import asyncio
import hashlib
import time
from pathlib import Path
from typing import ClassVar, Iterable

from maibot_sdk import API, Command, Field, MaiBotPlugin, PluginConfigBase, Tool

try:
    from .drawpic_gateway import DrawingGateway
    from .content_generator import ContentGenerator
    from .context_provider import PersonaContextProvider
    from .models import IncomingEvent, OutgoingAction, OutgoingStatus
    from .policy import content_fingerprint, is_publishable, normalize_text
    from .qr_generator import qr_data_url
    from .store import OperatorStore
    from .vendor_xiaoheihe.models import Notification, NotificationType, RoutingTarget
    from .xiaoheihe_runtime import XiaoheiheRuntime
except ImportError:  # pragma: no cover - direct Runner fallback
    from drawpic_gateway import DrawingGateway
    from content_generator import ContentGenerator
    from context_provider import PersonaContextProvider
    from models import IncomingEvent, OutgoingAction, OutgoingStatus
    from policy import content_fingerprint, is_publishable, normalize_text
    from qr_generator import qr_data_url
    from store import OperatorStore
    from vendor_xiaoheihe.models import Notification, NotificationType, RoutingTarget
    from xiaoheihe_runtime import XiaoheiheRuntime


class XiaoheihePluginSection(PluginConfigBase):
    config_version: str = Field(default="0.1.0", description="配置版本")
    enabled: bool = Field(default=False, description="是否启用小黑盒运营插件")
    profile_id: str = Field(default="default", description="小黑盒账号档案 ID")
    mode: str = Field(default="dry_run", description="运营模式：auto 或 dry_run；新安装默认只演练不发送")
    poll_interval_seconds: int = Field(default=60, description="通知轮询间隔，最低 30 秒")
    feed_interval_seconds: int = Field(default=900, description="主动浏览间隔")
    posts_per_day: int = Field(default=1, description="每日主动发帖上限")
    proactive_comments_per_day: int = Field(default=4, description="每日主动评论上限")
    notification_replies_per_day: int = Field(default=10, description="每日通知回复上限")
    send_cooldown_seconds: int = Field(default=120, description="账号级发送冷却")
    community_allowlist: list[str] = Field(default_factory=list, description="允许主动浏览的社区 ID")
    generate_post_images: bool = Field(default=True, description="主动帖子是否默认生成配图")
    life_context_api: str = Field(
        default="org.orge-8.life-frequency.get_current_context",
        description="生活轨迹插件公开 API 名称",
    )
    identity_reference_api: str = Field(
        default="org.mai-mai.self-identity.get_default_reference_image",
        description="身份插件公开参考图 API 名称",
    )
    post_topic_ids: str = Field(default="", description="主动发帖使用的社区/话题 ID")
    game_topic_hints: list[str] = Field(
        default_factory=lambda: [
            "游戏", "steam", "switch", "ps5", "pc", "副本", "角色", "抽卡",
            "绝区零", "原神", "崩坏", "星穹铁道", "apex", "瓦罗兰特", "单机",
        ],
        description="主动互动的游戏话题关键词",
    )


class XiaoheiheConfig(PluginConfigBase):
    """插件根配置；``plugin`` 子节由 MaiBot Runner 管理版本。"""

    plugin: XiaoheihePluginSection = Field(default_factory=XiaoheihePluginSection)

    @property
    def enabled(self) -> bool:
        return self.plugin.enabled

    @property
    def profile_id(self) -> str:
        return self.plugin.profile_id

    @property
    def mode(self) -> str:
        return self.plugin.mode

    @property
    def poll_interval_seconds(self) -> int:
        return self.plugin.poll_interval_seconds

    @property
    def feed_interval_seconds(self) -> int:
        return self.plugin.feed_interval_seconds

    @property
    def posts_per_day(self) -> int:
        return self.plugin.posts_per_day

    @property
    def proactive_comments_per_day(self) -> int:
        return self.plugin.proactive_comments_per_day

    @property
    def notification_replies_per_day(self) -> int:
        return self.plugin.notification_replies_per_day

    @property
    def send_cooldown_seconds(self) -> int:
        return self.plugin.send_cooldown_seconds

    @property
    def community_allowlist(self) -> list[str]:
        return self.plugin.community_allowlist

    @property
    def generate_post_images(self) -> bool:
        return self.plugin.generate_post_images

    @property
    def life_context_api(self) -> str:
        return self.plugin.life_context_api

    @property
    def identity_reference_api(self) -> str:
        return self.plugin.identity_reference_api

    @property
    def post_topic_ids(self) -> str:
        return self.plugin.post_topic_ids

    @property
    def game_topic_hints(self) -> list[str]:
        return self.plugin.game_topic_hints


class XiaoheiheOperatorPlugin(MaiBotPlugin):
    config_model = XiaoheiheConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()

    def __init__(self) -> None:
        super().__init__()
        self._store: OperatorStore | None = None
        self._drawing: DrawingGateway | None = None
        self._runtime: XiaoheiheRuntime | None = None
        self._generator: ContentGenerator | None = None
        self._persona: PersonaContextProvider | None = None
        self._worker: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_feed_at = 0.0

    async def on_load(self) -> None:
        data_dir = Path(self.ctx.paths.data_dir)
        self._store = OperatorStore(data_dir / "state.db")
        self._drawing = DrawingGateway(self.ctx.api)
        self._runtime = XiaoheiheRuntime(data_dir, self.config.profile_id)
        self._generator = ContentGenerator(self.ctx.llm)
        self._persona = PersonaContextProvider(
            self.ctx,
            life_api_name=str(self.config.life_context_api),
            identity_api_name=str(self.config.identity_reference_api),
        )
        self.ctx.logger.info("小黑盒运营插件已加载：enabled=%s mode=%s", self.config.enabled, self.config.mode)
        if self.config.enabled:
            self._start_worker()

    async def on_unload(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        if self._store is not None:
            self._store.close()
        if self._runtime is not None:
            await self._runtime.close()

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != "self":
            return
        self.ctx.logger.info("小黑盒运营插件配置已更新：version=%s", version)
        if self.config.enabled:
            self._start_worker()
        elif self._worker is not None:
            self._worker.cancel()
            self._worker = None

    def _start_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._stop.clear()
            self._worker = asyncio.create_task(self._run_worker(), name="xiaoheihe-operator")

    async def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error("小黑盒运营轮询失败：%s", exc)
            await asyncio.sleep(max(30, int(self.config.poll_interval_seconds)))

    async def _poll_once(self) -> None:
        if self._store is None or self._runtime is None or self._runtime.credentials is None:
            return
        client = await self._runtime.client()
        await self._retry_pre_send_proactive_posts(client)
        await self._reconcile_unknowns(client)
        await self._retry_cooldown_comments(client)
        for event_type in (NotificationType.MENTION, NotificationType.REPLY):
            await self._poll_notifications(client, event_type)
        now = time.time()
        if now - self._last_feed_at >= max(300, int(self.config.feed_interval_seconds)):
            self._last_feed_at = now
            await self._proactive_once(client)

    async def _retry_cooldown_comments(self, client) -> None:
        """Retry comments deferred by the account-level cooldown.

        Cooldown suppression is transient. The action stays in the audit log,
        while this pass retries it after the cooldown expires. Only one action
        is attempted per poll so a backlog cannot burst through the account
        rate limit.
        """
        if self._store is None or str(self.config.mode).lower() != "auto":
            return
        for row in self._store.list_outgoing(status=OutgoingStatus.SUPPRESSED, limit=50):
            if str(row.get("error") or "") != "cooldown":
                continue
            kind = str(row.get("kind") or "")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if kind == "notification_reply":
                if not self._within_quota(
                    "notification_reply", int(self.config.notification_replies_per_day)
                ):
                    continue
                route = RoutingTarget(
                    profile_id=self.config.profile_id,
                    post_id=str(metadata.get("post_id") or row.get("target_id") or ""),
                    root_comment_id=str(metadata.get("root_comment_id") or ""),
                    parent_comment_id=str(metadata.get("parent_comment_id") or ""),
                    notification_id=str(metadata.get("event_key") or ""),
                )
            elif kind == "proactive_comment":
                if not self._within_quota(
                    "proactive_comment", int(self.config.proactive_comments_per_day)
                ):
                    continue
                route = RoutingTarget(
                    profile_id=self.config.profile_id,
                    post_id=str(metadata.get("post_id") or row.get("target_id") or ""),
                )
            else:
                continue
            if not route.post_id or not str(row.get("content") or "").strip():
                continue
            await self._dispatch_comment(
                client,
                action_key=str(row.get("action_key") or ""),
                route=route,
                content=str(row.get("content") or ""),
            )
            return

    async def _poll_notifications(self, client, event_type: NotificationType) -> None:
        assert self._store is not None
        cursor, baseline_ready = self._store.get_cursor(self.config.profile_id, event_type.value)
        page = await client.fetch_notifications(event_type, cursor=cursor, page_size=20)
        items = list(getattr(page, "items", []) or [])
        next_cursor = str(getattr(page, "next_cursor", "") or cursor)
        if not baseline_ready:
            self._store.set_cursor(self.config.profile_id, event_type.value, next_cursor, baseline_ready=True)
            self.ctx.logger.info("小黑盒 %s 通知历史基线已建立", event_type.value)
            return
        for item in items:
            event = self._normalize_event(item, event_type)
            if event is None:
                continue
            audit_payload = {
                "event_key": event.event_key,
                "kind": event.kind,
                "post_id": event.post_id,
                "comment_id": event.comment_id,
                "root_comment_id": event.root_comment_id,
                "sender_id": event.sender_id,
                "sender_name": event.sender_name,
                "text": event.text,
            }
            if not self._store.claim_incoming(event.event_key, event_type.value, audit_payload):
                continue
            await self._process_event(client, event)
        self._store.set_cursor(self.config.profile_id, event_type.value, next_cursor, baseline_ready=True)

    @staticmethod
    def _normalize_event(item: object, event_type: NotificationType) -> IncomingEvent | None:
        if isinstance(item, dict) and isinstance(item.get("notification"), Notification):
            notification = item["notification"]
            if not notification.post_id or not notification.message_id:
                return None
            return IncomingEvent(
                event_key=notification.message_id,
                kind=notification.event_type.value,
                post_id=str(notification.post_id),
                comment_id=str(notification.parent_comment_id or notification.external_comment_id),
                root_comment_id=str(notification.root_comment_id or ""),
                sender_id=str(notification.sender_uid or ""),
                sender_name=str(notification.sender_nickname or ""),
                text=str(notification.content or ""),
                raw=notification.raw or item,
            )
        if not isinstance(item, dict):
            return None
        post_id = str(item.get("post_id") or item.get("linkid") or item.get("link_id") or "")
        notification_id = str(item.get("notification_id") or item.get("message_id") or "")
        comment_id = str(item.get("comment_id") or item.get("comment_a_id") or item.get("commentid") or "")
        if not post_id or not (notification_id or comment_id):
            return None
        return IncomingEvent(
            event_key=f"{event_type.value}:{notification_id or comment_id}",
            kind=event_type.value,
            post_id=post_id,
            comment_id=comment_id,
            root_comment_id=str(item.get("root_comment_id") or item.get("comment_a_id") or ""),
            sender_id=str(item.get("sender_uid") or item.get("userid_a") or ""),
            sender_name=str(item.get("sender_nickname") or item.get("user_a") or ""),
            text=str(item.get("content") or item.get("comment_a_text") or item.get("text") or ""),
            raw=item,
        )

    async def _process_event(self, client, event: IncomingEvent) -> None:
        if self._store is None or self._generator is None:
            return
        if not self._within_quota("notification_reply", int(self.config.notification_replies_per_day)):
            self.ctx.logger.info("小黑盒通知回复达到每日配额，已跳过：%s", event.event_key)
            return
        context = await client.fetch_thread_context(event.post_id, root_comment_id=event.root_comment_id)
        if context is None:
            self.ctx.logger.warning("小黑盒通知缺少帖子上下文，已跳过：post_id=%s", event.post_id)
            return
        reply = await self._generator.reply(
            post_title=str(getattr(context, "title", "")),
            post_body=str(getattr(context, "body", "")),
            comment=event.text,
        )
        if not reply:
            return
        allowed, reason = is_publishable(reply)
        if not allowed:
            self.ctx.logger.info("小黑盒回复被内容边界拦截：%s", reason)
            return
        action_key = f"reply:{event.event_key}"
        action = OutgoingAction(
            action_key,
            "notification_reply",
            event.post_id,
            reply,
            metadata={
                "post_id": event.post_id,
                "root_comment_id": event.root_comment_id,
                "parent_comment_id": event.comment_id,
                "event_key": event.event_key,
            },
        )
        if not self._store.begin_outgoing(action):
            return
        route = RoutingTarget(
            profile_id=self.config.profile_id,
            post_id=event.post_id,
            root_comment_id=event.root_comment_id,
            parent_comment_id=event.comment_id,
            notification_id=event.event_key,
        )
        await self._dispatch_comment(
            client,
            action_key=action_key,
            route=route,
            content=reply,
        )

    def _day_start(self) -> float:
        local = time.localtime()
        return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))

    def _within_quota(self, kind: str, limit: int) -> bool:
        if self._store is None:
            return False
        if limit <= 0:
            return False
        return self._store.count_since(kind, self._day_start()) < limit

    def _cooldown_ready(self) -> bool:
        if self._store is None:
            return False
        previous = self._store.last_outgoing_at()
        if previous is None:
            return True
        return time.time() - previous >= max(0, int(self.config.send_cooldown_seconds))

    async def _dispatch_comment(self, client, *, action_key: str, route: RoutingTarget, content: str) -> None:
        assert self._store is not None
        if str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            return
        if not self._cooldown_ready():
            self._store.update_outgoing(action_key, OutgoingStatus.SUPPRESSED, error="cooldown")
            return
        try:
            result = await client.send_comment(route, content)
            if not result.confirmed:
                self._store.update_outgoing(action_key, OutgoingStatus.SEND_UNKNOWN)
                return
            verified = await self._verify_comment(client, route, content)
            status = OutgoingStatus.VERIFIED if verified else OutgoingStatus.SEND_UNKNOWN
            self._store.update_outgoing(
                action_key,
                status,
                external_id=result.external_comment_id,
                error="" if verified else "readback_not_found",
            )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", ""))
            status = OutgoingStatus.FAILED if category in {"credential_invalid", "forbidden", "rate_limited"} else OutgoingStatus.SEND_UNKNOWN
            self._store.update_outgoing(action_key, status, error=type(exc).__name__)
            self.ctx.logger.error("小黑盒评论发送失败或结果未知：%s", type(exc).__name__)

    async def _verify_comment(self, client, route: RoutingTarget, content: str) -> bool:
        try:
            comments = await client.recent_comments(route, limit=30)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("小黑盒评论读回核验失败：%s", type(exc).__name__)
            return False
        expected = normalize_text(content, 240)
        for item in comments:
            if not isinstance(item, dict):
                continue
            candidate = normalize_text(
                str(item.get("text") or item.get("content") or item.get("comment_a_text") or ""),
                240,
            )
            if candidate and candidate == expected:
                return True
        return False

    async def _reconcile_unknowns(self, client) -> None:
        if self._store is None:
            return
        for row in self._store.list_outgoing(status=OutgoingStatus.SEND_UNKNOWN, limit=30):
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            try:
                if row.get("kind") in {"notification_reply", "proactive_comment"}:
                    route = RoutingTarget(
                        profile_id=self.config.profile_id,
                        post_id=str(metadata.get("post_id") or row.get("target_id") or ""),
                        root_comment_id=str(metadata.get("root_comment_id") or ""),
                        parent_comment_id=str(metadata.get("parent_comment_id") or ""),
                    )
                    if route.post_id and await self._verify_comment(client, route, str(row.get("content") or "")):
                        self._store.update_outgoing(row["action_key"], OutgoingStatus.VERIFIED)
                elif row.get("kind") == "proactive_post":
                    link_id = str(
                        metadata.get("link_id")
                        or row.get("target_id")
                        or row.get("external_id")
                        or ""
                    )
                    if link_id:
                        context = await client.fetch_thread_context(link_id)
                        if context and _same_post_content(
                            str(metadata.get("title") or ""),
                            str(metadata.get("body") or row.get("content") or ""),
                            context,
                            require_image=bool(metadata.get("image_required")),
                        ):
                            self._store.update_outgoing(row["action_key"], OutgoingStatus.VERIFIED, external_id=link_id)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.debug("小黑盒未知发送状态仍无法核验：%s", type(exc).__name__)

    async def _proactive_once(self, client) -> None:
        if not self.config.enabled or str(self.config.mode).lower() not in {"auto", "dry_run"}:
            return
        if self._within_quota("proactive_post", int(self.config.posts_per_day)):
            await self._create_proactive_post(client)
        if self._within_quota("proactive_comment", int(self.config.proactive_comments_per_day)):
            try:
                page = await client.fetch_feed(offset=0)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.warning("小黑盒推荐流读取失败：%s", type(exc).__name__)
                return
            for item in page.items:
                if self._feed_candidate(item):
                    await self._comment_on_feed_item(client, item)
                    break

    def _feed_candidate(self, item: object) -> bool:
        if not isinstance(item, dict):
            return False
        post_id = str(item.get("post_id") or item.get("link_id") or "")
        if not post_id:
            return False
        if self._runtime and self._runtime.credentials:
            author = item.get("author") if isinstance(item.get("author"), dict) else {}
            if str(author.get("uid") or author.get("userid") or "") == str(self._runtime.credentials.uid):
                return False
        allowlist = {str(value).strip() for value in self.config.community_allowlist if str(value).strip()}
        if not allowlist:
            return False
        values = {str(item.get(key) or "") for key in ("community_id", "topic_id", "section_id", "link_tag")}
        values.update(str(value) for value in item.get("section_names", []) if value)
        if not values.intersection(allowlist):
            return False
        text = " ".join(
            str(item.get(key) or "") for key in ("title", "subject", "content", "description", "text")
        )
        text += " " + " ".join(str(value) for value in item.get("section_names", []) if value)
        lowered = text.casefold()
        hints = [str(value).casefold() for value in self.config.game_topic_hints if str(value).strip()]
        if hints and not any(hint in lowered for hint in hints):
            return False
        return is_publishable(text)[0]

    async def _comment_on_feed_item(self, client, item: dict[str, object]) -> None:
        assert self._store is not None and self._generator is not None
        post_id = str(item.get("post_id") or item.get("link_id") or "")
        context = await client.fetch_thread_context(post_id)
        if context is None:
            return
        reply = await self._generator.reply(
            post_title=str(getattr(context, "title", "")),
            post_body=str(getattr(context, "body", "")),
            comment="请围绕这篇帖子自然分享一句。",
        )
        if not reply or not is_publishable(reply)[0]:
            return
        action_key = f"feed_comment:{post_id}:{content_fingerprint(reply)}"
        action = OutgoingAction(
            action_key,
            "proactive_comment",
            post_id,
            reply,
            metadata={"post_id": post_id},
        )
        if not self._store.begin_outgoing(action):
            return
        await self._dispatch_comment(
            client,
            action_key=action_key,
            route=RoutingTarget(profile_id=self.config.profile_id, post_id=post_id),
            content=reply,
        )

    async def _create_proactive_post(self, client) -> None:
        if self._store is None or self._generator is None or self._persona is None:
            return
        topic_ids = _normalize_topic_ids(self.config.post_topic_ids)
        if not topic_ids:
            return
        if len(topic_ids) > 2:
            self.ctx.logger.warning("小黑盒单条帖子最多选择 2 个分区，已跳过超限配置")
            return
        topic_id_value = ",".join(topic_ids)
        if not self._cooldown_ready():
            return
        retry_row = next(
            (
                row
                for row in self._store.list_outgoing(status=OutgoingStatus.FAILED, limit=30)
                if row.get("kind") == "proactive_post"
                and not str(row.get("external_id") or "")
                and str(row.get("error") or "").startswith("pre_send:")
                and isinstance(row.get("metadata"), dict)
                and str(row["metadata"].get("title") or "").strip()
                and str(row["metadata"].get("body") or "").strip()
            ),
            None,
        )
        if retry_row is not None:
            metadata = retry_row["metadata"]
            title = str(metadata["title"])
            body = str(metadata["body"])
            fingerprint = str(metadata.get("fingerprint") or content_fingerprint(title + "\n" + body))
            action_key = str(retry_row["action_key"])
            self.ctx.logger.info("小黑盒主动帖准备重试同一条发送前失败动作：%s", action_key[:28])
        else:
            life_context = await self._persona.life_context()
            first_post = self._store.count_since("proactive_post", 0) == 0
            topic_hint = (
                "第一天入驻小黑盒 Steam 社区，轻松介绍自己、说明以后想分享的游戏日常，语气自然像刚来报到"
                if first_post
                else "游戏日常和生活观察"
            )
            draft = await self._generator.post(life_context=life_context, topic_hint=topic_hint)
            if not draft:
                return
            title, body = draft
            if not is_publishable(title)[0] or not is_publishable(body)[0]:
                return
            fingerprint = content_fingerprint(title + "\n" + body)
            action_key = f"post:{fingerprint}"
        action = OutgoingAction(
            action_key,
            "proactive_post",
            "",
            body,
            request_id=f"xhh-post-{fingerprint[:40]}",
            metadata={
                "title": title,
                "body": body,
                "fingerprint": fingerprint,
                "topic_ids": topic_id_value,
                "image_required": bool(self.config.generate_post_images),
            },
        )
        if not self._store.begin_outgoing(action):
            return
        external_send_started = False
        stage = "prepare"
        try:
            blocks: list[dict[str, str]] = [{"type": "text", "text": body}]
            if self.config.generate_post_images:
                stage = "draw_submit"
                if self._drawing is None:
                    raise RuntimeError("绘图网关未初始化")
                reference = await self._persona.identity_reference()
                prompt = f"萧鱼儿的小黑盒生活分享配图：{title}。{body}。保持身份参考图中的人物形象一致。"
                task = await self._drawing.submit(
                    prompt,
                    request_id=action.request_id,
                    source_images=[reference] if reference else None,
                )
                stage = "draw_task_id"
                task_id = _task_id(task)
                if not task_id:
                    raise RuntimeError("绘图任务未返回 task_id")
                stage = "draw_wait"
                finished = await self._drawing.wait(task_id, request_id=action.request_id)
                if str(finished.get("status", "")) != "completed":
                    raise RuntimeError("绘图任务未完成")
                stage = "draw_read_image"
                image_bytes = await self._drawing.read_image(task_id)
                stage = "upload_image"
                uploaded = await client.upload_image_bytes(image_bytes, filename=f"{fingerprint}.png")
                blocks.append(
                    {
                        "type": "img",
                        "url": str(uploaded["url"]),
                        "width": int(uploaded.get("width") or 0),
                        "height": int(uploaded.get("height") or 0),
                    }
                )

            stage = "create_post"
            external_send_started = True
            result = await client.create_post(
                title=title,
                content_blocks=blocks,
                topic_ids=topic_id_value,
                hashtags=["游戏日常", "萧鱼儿"],
            )
            link_id = str(result.get("link_id") or "")
            if not link_id:
                self._store.update_outgoing(action_key, OutgoingStatus.SEND_UNKNOWN, error="missing_link_id")
                return
            context = await client.fetch_thread_context(link_id)
            if context and _same_post_content(
                title,
                body,
                context,
                require_image=bool(self.config.generate_post_images),
            ):
                self._store.update_outgoing(action_key, OutgoingStatus.VERIFIED, external_id=link_id)
            else:
                self._store.update_outgoing(action_key, OutgoingStatus.SEND_UNKNOWN, external_id=link_id, error="readback_not_found")
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", ""))
            if not external_send_started:
                status = OutgoingStatus.FAILED
                error = f"pre_send:{stage}:{type(exc).__name__}"
            else:
                status = OutgoingStatus.FAILED if category in {"credential_invalid", "forbidden", "rate_limited"} else OutgoingStatus.SEND_UNKNOWN
                error = type(exc).__name__
            self._store.update_outgoing(action_key, status, error=error)
            self.ctx.logger.error(
                "小黑盒主动发帖失败或结果未知：stage=%s error=%s category=%s status=%s detail=%s",
                stage,
                type(exc).__name__,
                str(getattr(exc, "category", "")),
                str(getattr(exc, "status_code", "")),
                str(exc)[:180],
            )

    async def _retry_pre_send_proactive_posts(self, client) -> None:
        """Retry an action only when no external post request was started."""

        if self._store is None:
            return
        rows = self._store.list_outgoing(status=OutgoingStatus.FAILED, limit=30)
        if any(
            row.get("kind") == "proactive_post"
            and not str(row.get("external_id") or "")
            and str(row.get("error") or "").startswith("pre_send:")
            for row in rows
        ):
            await self._create_proactive_post(client)

    @Tool("xiaoheihe_status", brief_description="查看小黑盒运营插件状态")
    async def handle_status(self, **kwargs):
        return {
            "success": True,
            "enabled": bool(self.config.enabled),
            "mode": str(self.config.mode),
            "profile_id": str(self.config.profile_id),
            "message": "小黑盒运营插件已加载" if not self.config.enabled else "小黑盒运营插件运行中",
        }

    @Tool("xiaoheihe_request_login", brief_description="生成小黑盒扫码登录信息")
    async def handle_request_login(self, **kwargs):
        if self._runtime is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            return {"success": True, **(await self._runtime.request_qr())}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": type(exc).__name__}

    @Tool("xiaoheihe_check_login", brief_description="检查小黑盒扫码登录状态")
    async def handle_check_login(self, **kwargs):
        if self._runtime is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            return {"success": True, **(await self._runtime.check_qr())}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": type(exc).__name__}

    @Tool("xiaoheihe_check_content", brief_description="检查一段内容是否符合萧鱼儿的小黑盒发布边界")
    async def handle_check_content(self, content: str, **kwargs):
        allowed, reason = is_publishable(content)
        return {"success": True, "allowed": allowed, "reason": reason}

    def _webui_login_payload(self, result: dict[str, object]) -> dict[str, object]:
        """Build a WebUI-safe login payload without exposing the raw QR URL."""

        if self._runtime is None:
            return {
                "state": "failed",
                "has_credentials": False,
                "message": "插件尚未加载",
                "qr_image": None,
                "remaining_seconds": 0,
            }
        session = self._runtime.session
        credentials = self._runtime.credentials
        payload = {
            "state": str(result.get("state") or ("success" if credentials else "idle")),
            "has_credentials": bool(credentials),
            "nickname": str(result.get("nickname") or (credentials.nickname if credentials else "")),
            "profile_id": self.config.profile_id,
            "message": str(result.get("message") or ""),
            "remaining_seconds": max(0, int(session.expires_at - time.time())) if session else 0,
            "qr_image": None,
        }
        if session is not None and session.qr_content and payload["state"] in {
            "waiting_scan",
            "scanned_waiting_confirm",
        }:
            payload["qr_image"] = qr_data_url(session.qr_content)
        return payload

    @API(
        name="webui_login_status",
        description="读取小黑盒 WebUI 登录状态并轮询当前二维码，只返回页面所需数据",
        version="1",
    )
    async def api_webui_login_status(self, **kwargs):
        del kwargs
        if self._runtime is None:
            return self._webui_login_payload({"state": "failed", "message": "插件尚未加载"})
        if self._runtime.session is not None:
            result = await self.handle_check_login()
        else:
            credentials = self._runtime.credentials
            result = {
                "state": "success" if credentials else "idle",
                "message": "已登录" if credentials else "尚未登录",
                "nickname": credentials.nickname if credentials else "",
            }
        return self._webui_login_payload(result)

    @API(
        name="webui_request_login",
        description="为小黑盒 WebUI 工作台生成新的扫码登录二维码",
        version="1",
    )
    async def api_webui_request_login(self, **kwargs):
        del kwargs
        result = await self.handle_request_login()
        if not result.get("success"):
            raise RuntimeError(str(result.get("error") or result.get("message") or "二维码生成失败"))
        return self._webui_login_payload({"state": "waiting_scan", "message": "请使用小黑盒 App 扫码"})

    @Command("xhh_status", pattern=r"^/xhh_status$")
    async def handle_command(self, **kwargs):
        result = await self.handle_status(**kwargs)
        return True, result["message"], 2

    @Command("xhh_login", pattern=r"^/xhh_login$")
    async def handle_login_command(self, **kwargs):
        result = await self.handle_request_login(**kwargs)
        if not result.get("success"):
            return False, f"小黑盒登录二维码生成失败：{result.get('error') or result.get('message', 'unknown')}", 1
        return (
            True,
            "小黑盒登录二维码已生成，请打开 MaiBot 的‘小黑盒运营’插件工作台扫码；扫码后使用 /xhh_login_check 检查状态。",
            2,
        )

    @Command("xhh_login_check", pattern=r"^/xhh_login_check$")
    async def handle_login_check_command(self, **kwargs):
        result = await self.handle_check_login(**kwargs)
        return True, f"小黑盒登录状态：{result.get('state', 'unknown')} {result.get('message', '')}", 2


def _task_id(value: object) -> str:
    if isinstance(value, dict):
        for key in ("task_id", "id", "taskId"):
            item = value.get(key)
            if item not in (None, ""):
                return str(item)
        for item in value.values():
            found = _task_id(item)
            if found:
                return found
    return ""


def _normalize_topic_ids(value: object) -> list[str]:
    result: list[str] = []
    for item in str(value or "").replace("，", ",").split(","):
        token = item.strip()
        if token and token.isdigit() and token not in result:
            result.append(token)
    return result


def _same_post_content(
    title: str,
    body: str,
    context: object,
    *,
    require_image: bool = False,
) -> bool:
    observed_title = normalize_text(str(getattr(context, "title", "")), 100)
    observed_body = normalize_text(str(getattr(context, "body", "")), 320)
    image_present = bool(getattr(context, "image_urls", []) or [])
    return bool(observed_title and observed_body) and (not require_image or image_present) and (
        content_fingerprint(observed_title) == content_fingerprint(normalize_text(title, 100))
        and content_fingerprint(observed_body) == content_fingerprint(normalize_text(body, 320))
    )


def create_plugin():
    return XiaoheiheOperatorPlugin()

