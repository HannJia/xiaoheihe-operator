from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal

from maibot_sdk import API, Command, Field, MaiBotPlugin, PluginConfigBase, Tool

try:
    from maibot_sdk.types import ToolParameterInfo, ToolParamType
except ImportError:  # pragma: no cover - local loading stub
    ToolParameterInfo = None  # type: ignore[assignment,misc]
    ToolParamType = None  # type: ignore[assignment,misc]


# 这些是用户能在配置页看到的名称。插件会在发送前通过小黑盒的搜索接口
# 解析为真实 topic_id，避免要求用户手工查找一串数字。
COMMUNITY_OPTIONS = (
    "Steam",
    "PC 游戏",
    "盒友杂谈",
    "盒友日常",
    "主机游戏",
    "手机游戏",
    "游戏攻略",
    "独立游戏",
    "电竞赛事",
    "优惠资讯",
)
POST_TARGET_OPTIONS = ("",) + COMMUNITY_OPTIONS
DEFAULT_COMMUNITY_ALLOWLIST = ("PC 游戏", "盒友杂谈")
MAX_ALLOWLIST_ITEMS = 10
MAX_POST_TARGETS = 2


def _time_window_active(start: str, end: str, now: float | None = None) -> bool:
    """判断服务器本地时间是否落在 HH:MM 时间窗口内。

    起止时间相同表示全天；结束时间早于开始时间时按跨午夜窗口处理。
    配置格式不合法时安全返回 False，避免意外自动执行。
    """

    def to_minutes(value: object) -> int | None:
        text = str(value or "").strip()
        parts = text.split(":")
        if len(parts) != 2:
            return None
        try:
            hour, minute = int(parts[0]), int(parts[1])
        except ValueError:
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return hour * 60 + minute

    start_minutes = to_minutes(start)
    end_minutes = to_minutes(end)
    if start_minutes is None or end_minutes is None:
        return False
    if start_minutes == end_minutes:
        return True
    current = time.localtime(time.time() if now is None else now)
    current_minutes = current.tm_hour * 60 + current.tm_min
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes

# Literal 让 MaiBot SDK 自动生成真正的下拉框和选项；显示给用户的是中文名称，
# 插件运行时再通过小黑盒搜索接口解析为内部 topic_id。
CommunityName = Literal[
    "Steam",
    "PC 游戏",
    "盒友杂谈",
    "盒友日常",
    "主机游戏",
    "手机游戏",
    "游戏攻略",
    "独立游戏",
    "电竞赛事",
    "优惠资讯",
]
PostTargetName = Literal[
    "",
    "Steam",
    "PC 游戏",
    "盒友杂谈",
    "盒友日常",
    "主机游戏",
    "手机游戏",
    "游戏攻略",
    "独立游戏",
    "电竞赛事",
    "优惠资讯",
]
RunMode = Literal["dry_run", "auto"]

try:
    from .drawpic_gateway import DrawingGateway
    from .content_generator import ContentGenerator
    from .context_provider import PersonaContextProvider
    from .dm_store import DirectMessageStore
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
    from dm_store import DirectMessageStore
    from models import IncomingEvent, OutgoingAction, OutgoingStatus
    from policy import content_fingerprint, is_publishable, normalize_text
    from qr_generator import qr_data_url
    from store import OperatorStore
    from vendor_xiaoheihe.models import Notification, NotificationType, RoutingTarget
    from xiaoheihe_runtime import XiaoheiheRuntime


def _ui_field(*, label: str, hint: str, order: int, **kwargs: Any):
    """统一给 MaiBot 配置页补齐中文标签、提示和顺序。"""

    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra.update({"label": label, "hint": hint, "order": order})
    return Field(json_schema_extra=extra, description=hint, **kwargs)


class XiaoheihePluginSection(PluginConfigBase):
    """插件元信息；旧版扁平配置中的业务字段会自动迁移。"""

    __ui_label__ = "插件信息"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    config_version: str = _ui_field(
        label="配置版本",
        hint="由插件维护，用于识别配置格式；通常不需要手动修改。",
        order=0,
        default="0.1.0",
        json_schema_extra={"disabled": True},
    )


class XiaoheiheBasicSection(PluginConfigBase):
    __ui_label__ = "基础设置"
    __ui_icon__ = "settings-2"
    __ui_order__ = 1

    enabled: bool = _ui_field(
        label="启用自动运营",
        hint="发布版默认开启自动运营；关闭后只保留登录和只读检查，不执行自动发帖、评论或回复。",
        order=0,
        default=True,
    )
    profile_id: str = _ui_field(
        label="小黑盒账号档案 ID",
        hint="当前账号使用的档案名称；单账号保持 default，多账号时再区分。",
        order=1,
        default="default",
    )
    mode: RunMode = _ui_field(
        label="运行模式",
        hint="请选择 dry_run 或 auto；发布版默认 auto。dry_run 只生成并记录计划，auto 才会向小黑盒发送内容。",
        order=2,
        default="auto",
        placeholder="请选择 dry_run 或 auto",
    )


class XiaoheiheBrowseSection(PluginConfigBase):
    __ui_label__ = "浏览与筛选"
    __ui_icon__ = "rss"
    __ui_order__ = 2

    poll_interval_seconds: int = _ui_field(
        label="通知检查间隔（秒）",
        hint="检查提及、评论和楼中楼通知的间隔；最低 30 秒，常用值为 60。",
        order=0,
        default=60,
        step=30,
    )
    feed_interval_seconds: int = _ui_field(
        label="推荐流浏览间隔（秒）",
        hint="读取推荐流的间隔；发布版推荐 600 秒（10 分钟），可按账号活跃度和模型额度自行调整。",
        order=1,
        default=600,
        step=300,
    )
    feed_active_start_time: str = _ui_field(
        label="AI 浏览开始时间",
        hint="只限制推荐流读取和主动评论的时间窗口，使用服务器本地时间 HH:MM；默认 08:00。与结束时间相同表示全天。",
        order=2,
        default="08:00",
        placeholder="例如 08:00",
    )
    feed_active_end_time: str = _ui_field(
        label="AI 浏览结束时间",
        hint="只限制推荐流读取和主动评论的时间窗口，使用服务器本地时间 HH:MM；默认 23:00。支持跨午夜窗口。",
        order=3,
        default="23:00",
        placeholder="例如 23:00",
    )
    game_topic_hints: list[str] = _ui_field(
        label="游戏筛选关键词",
        hint="推荐流只处理命中这些关键词的游戏相关帖子；点击添加项目逐条填写。",
        order=4,
        default_factory=lambda: [
            "游戏", "steam", "switch", "ps5", "pc", "副本", "角色", "抽卡",
            "绝区零", "原神", "崩坏", "星穹铁道", "apex", "瓦罗兰特", "单机",
        ],
    )


class XiaoheiheAutomationSection(PluginConfigBase):
    __ui_label__ = "自动运营额度"
    __ui_icon__ = "send"
    __ui_order__ = 3

    posts_per_day: int = _ui_field(
        label="每日主动发帖数",
        hint="每天最多主动发布的帖子数量；发布版默认 1。设为 0 表示关闭自动发帖。",
        order=0,
        default=1,
        step=1,
    )
    proactive_comments_per_day: int = _ui_field(
        label="每日主动评论数",
        hint="每天最多对推荐流帖子发表评论的数量；设为 0 表示关闭主动评论。",
        order=1,
        default=1,
        step=1,
    )
    notification_replies_per_day: int = _ui_field(
        label="每日通知回复数",
        hint="每天最多回复提及、评论和楼中楼通知的数量；设为 0 表示关闭自动回复。",
        order=2,
        default=1,
        step=1,
    )
    send_cooldown_seconds: int = _ui_field(
        label="发送冷却时间（秒）",
        hint="两次发送之间的最短间隔，用于降低重复发送和账号风控风险。",
        order=3,
        default=120,
        step=30,
    )
    post_active_start_time: str = _ui_field(
        label="自动发帖开始时间",
        hint="只限制主动发帖和失败重试的时间窗口，使用服务器本地时间 HH:MM；默认 10:00。与结束时间相同表示全天。",
        order=4,
        default="10:00",
        placeholder="例如 10:00",
    )
    post_active_end_time: str = _ui_field(
        label="自动发帖结束时间",
        hint="只限制主动发帖和失败重试的时间窗口，使用服务器本地时间 HH:MM；默认 22:00。支持跨午夜窗口。",
        order=5,
        default="22:00",
        placeholder="例如 22:00",
    )
    community_allowlist: list[str] = _ui_field(
        label="社区白名单",
        hint="允许浏览和主动互动的社区；默认包含 PC 游戏、盒友杂谈。最多选择 10 个，留空表示不主动互动。",
        order=6,
        default_factory=lambda: list(DEFAULT_COMMUNITY_ALLOWLIST),
        json_schema_extra={
            "items": {"type": "string", "enum": list(COMMUNITY_OPTIONS)},
            "max_items": MAX_ALLOWLIST_ITEMS,
        },
    )
    post_target_primary: PostTargetName = _ui_field(
        label="发帖目标 1（社区/话题）",
        hint="选择第一个发帖社区或话题；插件会自动解析内部 ID。留空表示不自动发帖。两个目标可以暂时选成一样，发送前会自动去重。",
        order=7,
        default="Steam",
    )
    post_target_secondary: PostTargetName = _ui_field(
        label="发帖目标 2（可选）",
        hint="可再选择一个与目标 1 不同的社区或话题；小黑盒单条帖子最多选择 2 个目标。若重复选择，发送前会自动去重。留空即可。",
        order=8,
        default="PC 游戏",
    )
    post_topic_ids: list[str] = _ui_field(
        label="旧版发帖目标 ID（兼容字段）",
        hint="旧版数字 ID 配置兼容字段，正常使用请通过上面的中文选项选择。",
        order=99,
        default_factory=list,
        json_schema_extra={"hidden": True},
    )


class XiaoheiheContentSection(PluginConfigBase):
    """内容生成与其它 MaiBot 插件的只读联动配置。

    当前联动插件市场中的「麦麦绘图」「生活频率」「自我身份档案」。
    API 名称仍按“插件 ID + @API(name=...)”组成，具体获取方法写在各字段提示中。
    """

    __ui_label__ = "内容与插件联动"
    __ui_icon__ = "puzzle"
    __ui_order__ = 4

    generate_post_images: bool = _ui_field(
        label="自动生成帖子图片",
        hint="可选联动「麦麦绘图」（ID：maimai-drawpic-plugin）。开启后生成帖子图片；未安装联动插件时请关闭，关闭后仍可纯文本自动发帖。",
        order=0,
        default=False,
    )
    life_context_api: str = _ui_field(
        label="生活轨迹 API",
        hint="可选联动「生活频率」（ID：org.orge-8.life-frequency）；当前完整 API 是 org.orge-8.life-frequency.get_current_context，只读调用。未安装时会自动退回当前时间和已有设定，不影响纯文本发帖。",
        order=1,
        default="org.orge-8.life-frequency.get_current_context",
    )
    identity_reference_api: str = _ui_field(
        label="身份参考图 API",
        hint="可选联动「自我身份档案」（ID：org.mai-mai.self-identity）；当前完整 API 是 org.mai-mai.self-identity.get_default_reference_image，只读调用。只在生成图片时作为参考图使用，未安装时会自动跳过。",
        order=2,
        default="org.mai-mai.self-identity.get_default_reference_image",
    )


class XiaoheiheDirectMessageSection(PluginConfigBase):
    __ui_label__ = "私信兼容设置"
    __ui_icon__ = "message-circle"
    __ui_order__ = 5

    dm_enabled: bool = _ui_field(
        label="启用私信功能",
        hint="当前发布版不提供稳定的私信自动发送，建议保持关闭；网页版也不能直接回复私信。",
        order=0,
        default=False,
    )
    dm_mode: str = _ui_field(
        label="私信运行模式",
        hint="兼容保留字段；当前版本不提供私信发送，auto 也会安全闭锁。",
        order=1,
        default="dry_run",
        placeholder="dry_run",
    )
    dm_poll_interval_seconds: int = _ui_field(
        label="私信检查间隔（秒）",
        hint="兼容保留字段；只在未来接入私信能力后生效。",
        order=2,
        default=180,
        step=30,
    )
    dm_daily_quota: int = _ui_field(
        label="每日私信回复数",
        hint="兼容保留字段；当前版本不会发送私信，设为 0 可明确关闭。",
        order=3,
        default=1,
        step=1,
    )
    dm_cooldown_seconds: int = _ui_field(
        label="私信回复冷却（秒）",
        hint="兼容保留字段；未来接入私信能力后用于限制同一账号的回复频率。",
        order=4,
        default=600,
        step=60,
    )
    dm_max_reply_chars: int = _ui_field(
        label="私信回复最大字数",
        hint="兼容保留字段；当前版本不会发送私信。",
        order=5,
        default=180,
        step=20,
    )
    dm_user_allowlist: list[str] = _ui_field(
        label="私信对象白名单",
        hint="兼容保留字段；当前版本不会生成自动私信回复，建议留空。点击添加项目逐条填写 UID。",
        order=6,
        default_factory=list,
    )


class XiaoheiheConfig(PluginConfigBase):
    """插件根配置；分组只调整配置页布局，旧版扁平 [plugin] 会自动迁移。"""

    plugin: XiaoheihePluginSection = Field(default_factory=XiaoheihePluginSection)
    basic: XiaoheiheBasicSection = Field(default_factory=XiaoheiheBasicSection)
    browse: XiaoheiheBrowseSection = Field(default_factory=XiaoheiheBrowseSection)
    automation: XiaoheiheAutomationSection = Field(default_factory=XiaoheiheAutomationSection)
    content: XiaoheiheContentSection = Field(default_factory=XiaoheiheContentSection)
    dm: XiaoheiheDirectMessageSection = Field(default_factory=XiaoheiheDirectMessageSection)

    @property
    def enabled(self) -> bool:
        return self.basic.enabled

    @property
    def profile_id(self) -> str:
        return self.basic.profile_id

    @property
    def mode(self) -> str:
        return self.basic.mode

    @property
    def poll_interval_seconds(self) -> int:
        return self.browse.poll_interval_seconds

    @property
    def feed_interval_seconds(self) -> int:
        return self.browse.feed_interval_seconds

    @property
    def feed_active_start_time(self) -> str:
        return self.browse.feed_active_start_time

    @property
    def feed_active_end_time(self) -> str:
        return self.browse.feed_active_end_time

    @property
    def posts_per_day(self) -> int:
        return self.automation.posts_per_day

    @property
    def proactive_comments_per_day(self) -> int:
        return self.automation.proactive_comments_per_day

    @property
    def notification_replies_per_day(self) -> int:
        return self.automation.notification_replies_per_day

    @property
    def send_cooldown_seconds(self) -> int:
        return self.automation.send_cooldown_seconds

    @property
    def post_active_start_time(self) -> str:
        return self.automation.post_active_start_time

    @property
    def post_active_end_time(self) -> str:
        return self.automation.post_active_end_time

    @property
    def dm_enabled(self) -> bool:
        return self.dm.dm_enabled

    @property
    def dm_mode(self) -> str:
        return self.dm.dm_mode

    @property
    def dm_poll_interval_seconds(self) -> int:
        return self.dm.dm_poll_interval_seconds

    @property
    def dm_daily_quota(self) -> int:
        return self.dm.dm_daily_quota

    @property
    def dm_cooldown_seconds(self) -> int:
        return self.dm.dm_cooldown_seconds

    @property
    def dm_max_reply_chars(self) -> int:
        return self.dm.dm_max_reply_chars

    @property
    def dm_user_allowlist(self) -> list[str]:
        return self.dm.dm_user_allowlist

    @property
    def community_allowlist(self) -> list[str]:
        return self.automation.community_allowlist

    @property
    def generate_post_images(self) -> bool:
        return self.content.generate_post_images

    @property
    def life_context_api(self) -> str:
        return self.content.life_context_api

    @property
    def identity_reference_api(self) -> str:
        return self.content.identity_reference_api

    @property
    def post_topic_ids(self) -> list[str]:
        selected = [
            str(self.automation.post_target_primary or "").strip(),
            str(self.automation.post_target_secondary or "").strip(),
        ]
        selected = [item for item in selected if item]
        if selected:
            return list(dict.fromkeys(selected))[:MAX_POST_TARGETS]
        return list(self.automation.post_topic_ids)

    @property
    def game_topic_hints(self) -> list[str]:
        return self.browse.game_topic_hints


class XiaoheiheOperatorPlugin(MaiBotPlugin):
    config_model = XiaoheiheConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()
    _LEGACY_CONFIG_SECTIONS: ClassVar[dict[str, str]] = {
        "enabled": "basic",
        "profile_id": "basic",
        "mode": "basic",
        "poll_interval_seconds": "browse",
        "feed_interval_seconds": "browse",
        "feed_active_start_time": "browse",
        "feed_active_end_time": "browse",
        "game_topic_hints": "browse",
        "posts_per_day": "automation",
        "proactive_comments_per_day": "automation",
        "notification_replies_per_day": "automation",
        "send_cooldown_seconds": "automation",
        "post_active_start_time": "automation",
        "post_active_end_time": "automation",
        "community_allowlist": "automation",
        "post_target_primary": "automation",
        "post_target_secondary": "automation",
        "post_topic_ids": "automation",
        "generate_post_images": "content",
        "life_context_api": "content",
        "identity_reference_api": "content",
        "dm_enabled": "dm",
        "dm_mode": "dm",
        "dm_poll_interval_seconds": "dm",
        "dm_daily_quota": "dm",
        "dm_cooldown_seconds": "dm",
        "dm_max_reply_chars": "dm",
        "dm_user_allowlist": "dm",
    }

    def __init__(self) -> None:
        super().__init__()
        self._store: OperatorStore | None = None
        self._dm_store: DirectMessageStore | None = None
        self._drawing: DrawingGateway | None = None
        self._runtime: XiaoheiheRuntime | None = None
        self._generator: ContentGenerator | None = None
        self._persona: PersonaContextProvider | None = None
        self._worker: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._last_feed_at = 0.0
        self._last_dm_poll_at = 0.0
        self._topic_id_cache: dict[str, str] = {}

    @staticmethod
    def _sanitize_config(config: Any) -> Any:
        """在 SDK 版本检查前补齐 [plugin].config_version。"""

        if not isinstance(config, Mapping):
            return config
        data = dict(config)
        raw_plugin = data.get("plugin")
        plugin = dict(raw_plugin) if isinstance(raw_plugin, Mapping) else {}
        if not str(plugin.get("config_version") or "").strip():
            plugin["config_version"] = "0.1.0"
        data["plugin"] = plugin
        return data

    def normalize_plugin_config(self, config_data: Any) -> tuple[dict[str, Any], bool]:
        """把旧版扁平 [plugin] 业务字段迁移到新的配置分组。"""

        sanitized = self._sanitize_config(config_data)
        if not isinstance(sanitized, Mapping):
            return sanitized, False

        data = dict(sanitized)
        raw_plugin = data.get("plugin")
        plugin = dict(raw_plugin) if isinstance(raw_plugin, Mapping) else {}
        changed = False
        for field_name, section_name in self._LEGACY_CONFIG_SECTIONS.items():
            if field_name not in plugin:
                continue
            raw_section = data.get(section_name)
            section = dict(raw_section) if isinstance(raw_section, Mapping) else {}
            if field_name not in section:
                section[field_name] = plugin[field_name]
            data[section_name] = section
            plugin.pop(field_name, None)
            changed = True
        automation = data.get("automation")
        if isinstance(automation, Mapping):
            normalized_automation = dict(automation)
            raw_targets = normalized_automation.get("post_topic_ids")
            if isinstance(raw_targets, str):
                legacy_targets = [
                    item.strip()
                    for item in raw_targets.replace("，", ",").split(",")
                    if item.strip()
                ]
                normalized_automation["post_topic_ids"] = legacy_targets
                data["automation"] = normalized_automation
                changed = True
            else:
                legacy_targets = list(raw_targets) if isinstance(raw_targets, list) else []
            # 数字 ID 继续保存在兼容字段中；不要把它写进 Literal 下拉字段，
            # 否则旧配置会在 Pydantic 校验阶段被拒绝。新配置直接从中文选项选择。
            if legacy_targets and not str(normalized_automation.get("post_target_primary") or "").strip():
                named_targets = [item for item in legacy_targets if item in POST_TARGET_OPTIONS]
                if named_targets:
                    normalized_automation["post_target_primary"] = named_targets[0]
                    if len(named_targets) > 1 and not str(normalized_automation.get("post_target_secondary") or "").strip():
                        normalized_automation["post_target_secondary"] = named_targets[1]
                    data["automation"] = normalized_automation
                    changed = True
        data["plugin"] = plugin

        try:
            return super().normalize_plugin_config(data)
        except AttributeError:
            return data, changed

    def get_webui_config_schema(self, **kwargs: Any) -> dict[str, Any]:
        """补齐配置页提示，并把暂不使用的私信分组默认收起。"""

        try:
            schema = super().get_webui_config_schema(**kwargs)
        except AttributeError:
            return {"sections": {}}
        try:
            sections = schema.get("sections") or {}
            for name, section in sections.items():
                if not isinstance(section, dict):
                    continue
                if name == "dm":
                    section["collapsed"] = True
                    section["order"] = 100 + float(section.get("order") or 0)
                    section["description"] = (
                        "当前发布版不提供私信自动发送；只有未来接入稳定发送能力后才需要调整。"
                    )
                else:
                    section["collapsed"] = False
                for field_name, field in (section.get("fields") or {}).items():
                    if isinstance(field, dict) and field.get("description") and not field.get("hint"):
                        field["hint"] = field["description"]
                    # Radix Select 禁止 Select.Item 使用空字符串，但空值本身仍然
                    # 可以作为 value 显示 placeholder。因此保留字段默认空值，
                    # 只从选项列表中移除空项，用户仍可通过清空输入保持不发帖。
                    if (
                        name == "automation"
                        and field_name in {"post_target_primary", "post_target_secondary"}
                        and isinstance(field, dict)
                        and isinstance(field.get("choices"), list)
                    ):
                        field["choices"] = [choice for choice in field["choices"] if str(choice) != ""]
            return schema
        except Exception:
            self.ctx.logger.exception("小黑盒配置页显示元数据修正失败，回退原始 Schema")
            return schema

    async def on_load(self) -> None:
        data_dir = Path(self.ctx.paths.data_dir)
        self._store = OperatorStore(data_dir / "state.db")
        self._dm_store = DirectMessageStore(data_dir / "state.db")
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
        if self._dm_store is not None:
            self._dm_store.close()
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
        post_window_active = _time_window_active(
            self.config.post_active_start_time,
            self.config.post_active_end_time,
        )
        browse_window_active = _time_window_active(
            self.config.feed_active_start_time,
            self.config.feed_active_end_time,
        )
        if post_window_active:
            await self._retry_pre_send_proactive_posts(client)
        await self._reconcile_unknowns(client)
        await self._retry_cooldown_comments(client)
        for event_type in (NotificationType.MENTION, NotificationType.REPLY):
            await self._poll_notifications(client, event_type)
        now = time.time()
        if self.config.dm_enabled and now - self._last_dm_poll_at >= max(60, int(self.config.dm_poll_interval_seconds)):
            self._last_dm_poll_at = now
            await self._poll_direct_messages(client)
        if now - self._last_feed_at >= max(300, int(self.config.feed_interval_seconds)):
            self._last_feed_at = now
            if post_window_active or browse_window_active:
                await self._proactive_once(
                    client,
                    post_window_active=post_window_active,
                    browse_window_active=browse_window_active,
                )

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
            if kind == "proactive_comment" and not _time_window_active(
                self.config.feed_active_start_time,
                self.config.feed_active_end_time,
            ):
                continue
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
            if kind == "notification_reply":
                self._finish_incoming_after_dispatch(
                    str(metadata.get("event_key") or ""),
                    str(row.get("action_key") or ""),
                )
            return

    async def _poll_direct_messages(self, client) -> None:
        if self._dm_store is None or self._store is None or self._runtime is None:
            return
        try:
            _, baseline_ready = self._dm_store.get_cursor(self.config.profile_id)
            # Message lists are newest-first and shift when a new message
            # arrives. Reusing an old offset can skip the message we need.
            page = await client.fetch_direct_messages(
                cursor="",
                page=1,
                page_size=30,
            )
        except Exception as exc:  # noqa: BLE001
            probe = getattr(client, "last_direct_message_probe", {})
            self._dm_store.set_probe(
                self.config.profile_id,
                {"status": "error", **(_safe_status_data(probe) or {})},
            )
            self.ctx.logger.warning(
                "小黑盒私信只读探测失败：type=%s probe=%s",
                type(exc).__name__,
                _safe_status_data(probe),
            )
            return

        messages = list(getattr(page, "items", []) or [])
        probe = getattr(client, "last_direct_message_probe", {})
        self._dm_store.set_probe(
            self.config.profile_id,
            {
                "status": "ok",
                "source": str(getattr(page, "source", "") or ""),
                "accepted_count": len(messages),
                "has_more": bool(getattr(page, "has_more", False)),
                **(_safe_status_data(probe) or {}),
            },
        )
        if not baseline_ready:
            inserted = 0
            for message in messages:
                if self._dm_store.insert_message(message, baseline=True):
                    inserted += 1
            self._dm_store.set_cursor(
                self.config.profile_id,
                str(getattr(page, "next_cursor", "") or ""),
                baseline_ready=True,
            )
            self.ctx.logger.info(
                "小黑盒私信历史基线已建立：messages=%s inserted=%s source=%s",
                len(messages),
                inserted,
                str(getattr(page, "source", "")),
            )
            return

        inserted = 0
        for message in messages:
            if not self._dm_store.insert_message(message):
                continue
            inserted += 1
            if not bool(getattr(message, "incoming", False)):
                continue
            self.ctx.logger.info(
                "发现新的小黑盒私信：message_id=%s sender_uid=%s chars=%s",
                str(getattr(message, "message_id", ""))[:80],
                str(getattr(message, "sender_uid", ""))[:80],
                len(str(getattr(message, "text", "") or "")),
            )
            await self._process_direct_message(message)
        self._dm_store.set_cursor(
            self.config.profile_id,
            str(getattr(page, "next_cursor", "") or ""),
            baseline_ready=True,
        )
        if inserted:
            self.ctx.logger.info(
                "小黑盒私信轮询完成：new_messages=%s source=%s",
                inserted,
                str(getattr(page, "source", "")),
            )

    async def _process_direct_message(self, message) -> None:
        if self._store is None or self._dm_store is None or self._generator is None:
            return
        sender_uid = str(getattr(message, "sender_uid", "") or "").strip()
        allowlist = {
            str(value).strip()
            for value in self.config.dm_user_allowlist
            if str(value).strip()
        }
        if not allowlist or sender_uid not in allowlist:
            self.ctx.logger.info(
                "私信只读记录，未生成自动回复：sender_uid=%s allowlist_configured=%s",
                sender_uid[:80],
                bool(allowlist),
            )
            return
        if not self._within_quota("dm_reply", int(self.config.dm_daily_quota)):
            return

        message_id = str(getattr(message, "message_id", "") or "")
        if not message_id:
            return
        action_key = f"dm_reply:{message_id}"
        if self._store.get_outgoing(action_key):
            return
        previous = self._store.last_outgoing_at(
            kind="dm_reply",
            target_id=sender_uid,
            exclude_action_key=action_key,
        )
        if previous is not None and time.time() - previous < max(0, int(self.config.dm_cooldown_seconds)):
            self.ctx.logger.info(
                "小黑盒私信回复处于同一账号冷却期：sender_uid=%s",
                sender_uid[:80],
            )
            return
        recent = self._dm_store.list_recent(self.config.profile_id, limit=12)
        recent_lines = [
            f"{item.get('sender_name') or item.get('sender_uid') or '对方'}：{normalize_text(str(item.get('text') or ''), 240)}"
            for item in reversed(recent)
            if str(item.get("peer_uid") or "") == sender_uid and str(item.get("text") or "").strip()
        ]
        reply = await self._generator.direct_message_reply(
            sender_name=str(getattr(message, "sender_name", "") or ""),
            message=str(getattr(message, "text", "") or ""),
            recent_context="\n".join(recent_lines[-8:]),
            max_chars=int(self.config.dm_max_reply_chars),
        )
        if not reply:
            return
        allowed, reason = is_publishable(reply)
        if not allowed:
            self.ctx.logger.info("小黑盒私信回复被内容边界拦截：%s", reason)
            return
        action = OutgoingAction(
            action_key,
            "dm_reply",
            sender_uid,
            reply,
            metadata={
                "message_id": message_id,
                "conversation_id": str(getattr(message, "conversation_id", "") or ""),
                "peer_uid": sender_uid,
                "sender_name": str(getattr(message, "sender_name", "") or ""),
                "reply_mode": str(self.config.dm_mode),
            },
        )
        if not self._store.begin_outgoing(action):
            return
        if str(self.config.dm_mode).lower() == "dry_run" or str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            return
        # The read contract is being verified first. A write route is not
        # implemented yet, so auto mode fails closed instead of guessing.
        self._store.update_outgoing(
            action_key,
            OutgoingStatus.FAILED,
            error="dm_send_contract_unavailable",
        )
        self.ctx.logger.warning(
            "小黑盒私信已生成回复但未发送：公开网页只提供读取接口，发送契约未确认 message_id=%s",
            message_id[:80],
        )

    async def _poll_notifications(self, client, event_type: NotificationType) -> None:
        assert self._store is not None
        # The API client treats cursor as an offset. Reusing an advancing
        # offset can skip fresh notifications when new items shift the list.
        _, baseline_ready = self._store.get_cursor(self.config.profile_id, event_type.value)
        page = await client.fetch_notifications(event_type, cursor="", page=1, page_size=20)
        items = list(getattr(page, "items", []) or [])
        if not baseline_ready:
            for item in items:
                event = self._normalize_event(item, event_type)
                if event is None:
                    continue
                self._store.remember_incoming_baseline(
                    event.event_key,
                    event_type.value,
                    {
                        "event_key": event.event_key,
                        "kind": event.kind,
                        "post_id": event.post_id,
                        "comment_id": event.comment_id,
                        "root_comment_id": event.root_comment_id,
                        "sender_id": event.sender_id,
                        "sender_name": event.sender_name,
                        "text": event.text,
                    },
                )
            self._store.set_cursor(self.config.profile_id, event_type.value, "", baseline_ready=True)
            self.ctx.logger.info("小黑盒 %s 通知历史基线已建立", event_type.value)
            return
        # Newest-first scans stop at the first known event. If an entire page is
        # new, continue to older pages (bounded to 200 notifications per type).
        for page_number in range(1, 11):
            if page_number > 1:
                if not bool(getattr(page, "has_more", False)):
                    break
                page = await client.fetch_notifications(
                    event_type, cursor="", page=page_number, page_size=20
                )
                items = list(getattr(page, "items", []) or [])
            found_known_event = False
            for item in items:
                event = self._normalize_event(item, event_type)
                if event is None:
                    continue
                if self._store.has_incoming(event.event_key):
                    found_known_event = True
                    continue
                self._store.claim_incoming(
                    event.event_key,
                    event_type.value,
                    {
                        "event_key": event.event_key,
                        "kind": event.kind,
                        "post_id": event.post_id,
                        "comment_id": event.comment_id,
                        "root_comment_id": event.root_comment_id,
                        "sender_id": event.sender_id,
                        "sender_name": event.sender_name,
                        "text": event.text,
                    },
                )
            if found_known_event or not bool(getattr(page, "has_more", False)):
                break
        self._store.set_cursor(self.config.profile_id, event_type.value, "", baseline_ready=True)
        # Claims are durable before work starts. Revisit pending events even if
        # their notification is no longer present in the current API page.
        for row in self._store.list_pending_incoming(kind=event_type.value, limit=50):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            event = IncomingEvent(
                event_key=str(payload.get("event_key") or row.get("event_key") or ""),
                kind=str(payload.get("kind") or event_type.value),
                post_id=str(payload.get("post_id") or ""),
                comment_id=str(payload.get("comment_id") or ""),
                root_comment_id=str(payload.get("root_comment_id") or ""),
                sender_id=str(payload.get("sender_id") or ""),
                sender_name=str(payload.get("sender_name") or ""),
                text=str(payload.get("text") or ""),
            )
            if not event.event_key or not event.post_id or not event.text.strip():
                self._store.mark_incoming_done(event.event_key, state="ignored")
                continue
            try:
                await self._process_event(client, event)
            except Exception as exc:  # noqa: BLE001
                attempts = int(row.get("attempts") or 0)
                retry_delay = min(3600, 60 * (2 ** min(attempts, 6)))
                self._store.defer_incoming(
                    event.event_key,
                    f"processing_error:{type(exc).__name__}",
                    delay_seconds=retry_delay,
                )
                self.ctx.logger.warning(
                    "小黑盒通知回复暂缓重试：event=%s error_type=%s",
                    event.event_key,
                    type(exc).__name__,
                )

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
        if self._store is None:
            return
        action_key = f"reply:{event.event_key}"
        existing = self._store.get_outgoing(action_key)
        if existing:
            if existing.get("status") == OutgoingStatus.SUPPRESSED.value and str(existing.get("error") or "") == "cooldown":
                self._store.defer_incoming(
                    event.event_key,
                    "send_cooldown",
                    delay_seconds=max(30, int(self.config.poll_interval_seconds)),
                    increment_attempt=False,
                )
                return
            # Any attempted notification reply is terminal. Never repeat a
            # rejected write or an uncertain send for the same event.
            self._store.mark_incoming_done(event.event_key)
            return
        if self._generator is None:
            self._defer_notification(event.event_key, "reply_generator_unavailable", delay_seconds=300)
            return
        if not self._within_quota("notification_reply", int(self.config.notification_replies_per_day)):
            now = time.time()
            next_day = self._day_start() + 24 * 60 * 60
            self._store.defer_incoming(
                event.event_key,
                "daily_quota",
                delay_seconds=max(60, next_day - now),
                increment_attempt=False,
            )
            self.ctx.logger.info("小黑盒通知回复达到每日配额，已保留待处理：%s", event.event_key)
            return

        context = await client.fetch_thread_context(event.post_id, root_comment_id=event.root_comment_id)
        if context is None:
            self._defer_notification(event.event_key, "thread_context_missing", delay_seconds=300)
            return
        reply = await self._generator.reply(
            post_title=str(getattr(context, "title", "")),
            post_body=str(getattr(context, "body", "")),
            comment=event.text,
        )
        if not reply:
            self._defer_notification(event.event_key, "empty_generated_reply", delay_seconds=300)
            return
        allowed, reason = is_publishable(reply)
        if not allowed:
            self.ctx.logger.info("小黑盒回复被内容边界拦截：%s", reason)
            self._store.mark_incoming_done(event.event_key, state="ignored")
            return
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
                "reply_strategy": "post_context_and_comment",
                "post_title": normalize_text(str(getattr(context, "title", "")), 120),
                "post_body_fingerprint": content_fingerprint(
                    normalize_text(str(getattr(context, "body", "")), 900)
                ),
                "comment_fingerprint": content_fingerprint(normalize_text(event.text, 500)),
            },
        )
        if not self._store.begin_outgoing(action):
            # The same event already has an audited action. Its state is either
            # handled by a dedicated retry path or intentionally terminal.
            self._store.mark_incoming_done(event.event_key)
            return
        self._store.mark_incoming_done(event.event_key, state="dispatched")
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
        self._finish_incoming_after_dispatch(event.event_key, action_key)

    def _defer_notification(self, event_key: str, reason: str, *, delay_seconds: float) -> None:
        if self._store is None:
            return
        row = self._store.get_incoming(event_key)
        attempts = int(row.get("attempts") or 0) if row else 0
        delay = min(3600, max(delay_seconds, 60 * (2 ** min(attempts, 6))))
        self._store.defer_incoming(
            event_key,
            reason,
            delay_seconds=delay,
        )
        self.ctx.logger.info(
            "小黑盒通知已延后重试：event=%s reason=%s delay_seconds=%s attempt=%s",
            event_key,
            reason,
            int(delay),
            attempts + 1,
        )

    def _finish_incoming_after_dispatch(self, event_key: str, action_key: str) -> None:
        if self._store is None:
            return
        outgoing = self._store.get_outgoing(action_key) or {}
        status = str(outgoing.get("status") or "")
        if status == OutgoingStatus.SUPPRESSED.value and str(outgoing.get("error") or "") == "cooldown":
            return
        self._store.mark_incoming_done(event_key)

    def _day_start(self) -> float:
        local = time.localtime()
        return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))

    def _within_quota(self, kind: str, limit: int) -> bool:
        if self._store is None:
            return False
        if limit <= 0:
            return False
        return self._store.count_since(kind, self._day_start()) < limit

    def _cooldown_ready(self, *, exclude_action_key: str = "") -> bool:
        if self._store is None:
            return False
        previous = self._store.last_outgoing_at(exclude_action_key=exclude_action_key)
        if previous is None:
            return True
        return time.time() - previous >= max(0, int(self.config.send_cooldown_seconds))

    async def _dispatch_comment(self, client, *, action_key: str, route: RoutingTarget, content: str) -> None:
        assert self._store is not None
        if str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            return
        if not self._cooldown_ready(exclude_action_key=action_key):
            self._store.update_outgoing(action_key, OutgoingStatus.SUPPRESSED, error="cooldown")
            return
        # Persist the send boundary before touching the external API. If the
        # process dies after this point, reconciliation may read back the
        # thread, but it must never blindly issue the same write again.
        self._store.update_outgoing(action_key, OutgoingStatus.SENDING, error="send_started")
        try:
            result = await client.send_comment(route, content)
            if not result.confirmed:
                self._store.update_outgoing(action_key, OutgoingStatus.SEND_UNKNOWN)
                return
            verify_route = route
            if not route.root_comment_id and result.external_comment_id:
                # The post-tree endpoint exposes a root-specific comment under
                # current_comment. Use the returned main-comment ID for
                # readback instead of querying the post's empty top-level list.
                verify_route = RoutingTarget(
                    profile_id=str(getattr(route, "profile_id", self.config.profile_id)),
                    post_id=route.post_id,
                    root_comment_id=str(result.external_comment_id),
                    parent_comment_id=str(result.external_comment_id),
                    notification_id=str(getattr(route, "notification_id", "")),
                )
            verified = await self._verify_comment(client, verify_route, content)
            status = OutgoingStatus.VERIFIED if verified else OutgoingStatus.SEND_UNKNOWN
            self._store.update_outgoing(
                action_key,
                status,
                external_id=result.external_comment_id,
                error="" if verified else "readback_not_found",
            )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", ""))
            status = OutgoingStatus.FAILED if category in {
                "credential_invalid",
                "forbidden",
                "rate_limited",
                "upstream_rejected",
                "response_shape",
                "http",
            } else OutgoingStatus.SEND_UNKNOWN
            detail = getattr(exc, "details", {})
            detail_keys = sorted(str(key) for key in detail) if isinstance(detail, dict) else []
            self._store.update_outgoing(
                action_key,
                status,
                error=_error_label(exc),
            )
            self.ctx.logger.error(
                "小黑盒评论发送失败或结果未知：type=%s category=%s status=%s message=%s detail_keys=%s",
                type(exc).__name__,
                category,
                getattr(exc, "status_code", ""),
                str(exc)[:240],
                detail_keys,
            )

    async def _verify_comment(self, client, route: RoutingTarget, content: str) -> bool:
        try:
            # The dedicated /bbs/app/comment/user route is unstable for this
            # account. The thread tree is already required for content-aware
            # replies and contains the same comment data, so use it as the
            # readback source to avoid false send_unknown states and retries.
            context = await client.fetch_thread_context(
                route.post_id,
                root_comment_id=route.root_comment_id,
            )
            comments = list(getattr(context, "comments", []) or [])
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("小黑盒评论读回核验失败：%s", _error_label(exc))
            return False
        expected = normalize_text(content, 240)
        for item in comments:
            if not isinstance(item, dict):
                continue
            candidate = normalize_text(_comment_text(item), 240)
            if candidate and candidate == expected:
                return True
        return False

    async def _reconcile_unknowns(self, client) -> None:
        if self._store is None:
            return
        rows: list[dict[str, object]] = []
        for status in (OutgoingStatus.SENDING, OutgoingStatus.SEND_UNKNOWN):
            rows.extend(self._store.list_outgoing(status=status, limit=30))
        for row in rows:
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

    async def _proactive_once(
        self,
        client,
        *,
        post_window_active: bool | None = None,
        browse_window_active: bool | None = None,
    ) -> None:
        if not self.config.enabled or str(self.config.mode).lower() not in {"auto", "dry_run"}:
            return
        if post_window_active is None:
            post_window_active = _time_window_active(
                self.config.post_active_start_time,
                self.config.post_active_end_time,
            )
        if browse_window_active is None:
            browse_window_active = _time_window_active(
                self.config.feed_active_start_time,
                self.config.feed_active_end_time,
            )
        if post_window_active and self._within_quota("proactive_post", int(self.config.posts_per_day)):
            await self._create_proactive_post(client)
        if not browse_window_active:
            return
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
        allowlist_values = _normalize_selection_values(
            self.config.community_allowlist,
            max_items=MAX_ALLOWLIST_ITEMS,
        )
        allowlist = {_normalize_selection_name(value) for value in allowlist_values}
        if not allowlist:
            return False
        values = {str(item.get(key) or "") for key in ("community_id", "topic_id", "section_id", "link_tag")}
        values.update(str(value) for value in item.get("section_names", []) if value)
        if not {_normalize_selection_name(value) for value in values}.intersection(allowlist):
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
        if not _time_window_active(
            self.config.post_active_start_time,
            self.config.post_active_end_time,
        ):
            return
        raw_targets = _normalize_selection_values(self.config.post_topic_ids, max_items=None)
        targets = raw_targets[:MAX_POST_TARGETS]
        if not targets:
            return
        if len(raw_targets) > MAX_POST_TARGETS:
            self.ctx.logger.warning("小黑盒单条帖子最多选择 2 个分区，已使用前 2 个配置")
        try:
            topic_ids = await self._resolve_post_topic_ids(client, targets)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("小黑盒发帖目标解析失败：%s", type(exc).__name__)
            return
        if not topic_ids:
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
        if str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            self.ctx.logger.info(
                "小黑盒主动发帖处于 dry_run，已记录草稿但未调用外部发帖接口：%s",
                action_key[:28],
            )
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
                prompt = f"maibot 的小黑盒生活分享配图：{title}。{body}。保持身份参考图中的人物形象一致。"
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
                hashtags=["游戏日常", "maibot"],
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

    async def _resolve_post_topic_ids(self, client, targets: list[str]) -> list[str]:
        """Resolve friendly config options while retaining old numeric IDs."""

        numeric_ids = [item for item in targets if item.isdigit()]
        names = [item for item in targets if not item.isdigit()]
        if names:
            missing = [name for name in names if name not in self._topic_id_cache]
            if missing:
                self._topic_id_cache.update(await client.resolve_topic_names(missing))
            numeric_ids.extend(self._topic_id_cache.get(name, "") for name in names)
        return list(dict.fromkeys(item for item in numeric_ids if item.isdigit()))

    async def _retry_pre_send_proactive_posts(self, client) -> None:
        """Retry an action only when no external post request was started."""

        if self._store is None or not _time_window_active(
            self.config.post_active_start_time,
            self.config.post_active_end_time,
        ):
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
        dm_summary = (
            self._dm_store.summary(self.config.profile_id)
            if self._dm_store is not None
            else {"available": False}
        )
        return {
            "success": True,
            "enabled": bool(self.config.enabled),
            "mode": str(self.config.mode),
            "profile_id": str(self.config.profile_id),
            "reply_capabilities": {
                "post_comments": "已启用：读取原帖标题、正文和收到的评论后生成回复",
                "mentions_and_replies": "已启用：处理小黑盒通知中心的提及、评论和楼中楼",
                "direct_messages": "当前版本未纳入发布范围：默认关闭，不发送私信",
            },
            "direct_message": {
                "enabled": bool(self.config.dm_enabled),
                "mode": str(self.config.dm_mode),
                "poll_interval_seconds": int(self.config.dm_poll_interval_seconds),
                "daily_quota": int(self.config.dm_daily_quota),
                "allowlist_size": len(list(self.config.dm_user_allowlist or [])),
                "state": dm_summary,
            },
            "settings_help": {
                "mode": "dry_run 只记录计划；auto 才会真实发送",
                "feed_interval_seconds": "推荐 600 秒，即每 10 分钟浏览一次；这是插件默认建议，不是小黑盒官方固定检查时间",
                "feed_active_window": f"AI 浏览和主动评论只在服务器本地时间 {self.config.feed_active_start_time}-{self.config.feed_active_end_time} 运行；与发帖时间窗口独立",
                "post_active_window": f"主动发帖和发送前失败重试只在服务器本地时间 {self.config.post_active_start_time}-{self.config.post_active_end_time} 运行",
                "posts_per_day": "建议测试阶段保持 0",
                "notification_replies_per_day": "建议从 1 开始，确认链路后再增加",
                "dm_user_allowlist": "私信功能暂不启用；保留字段仅用于兼容旧配置",
                "dm_mode": "当前发布版不提供私信发送，保持 dry_run",
            },
            "message": "小黑盒运营插件已加载" if not self.config.enabled else "小黑盒运营插件运行中",
        }

    @Tool("xiaoheihe_request_login", brief_description="生成小黑盒扫码登录信息")
    async def handle_request_login(self, **kwargs):
        if self._runtime is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            await self._runtime.request_qr()
            payload = self._webui_login_payload(
                {"state": "waiting_scan", "message": "二维码已生成，请使用小黑盒 App 扫码"}
            )
            return {"success": True, **payload}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": type(exc).__name__}

    @Tool("xiaoheihe_check_login", brief_description="检查小黑盒扫码登录状态")
    async def handle_check_login(self, **kwargs):
        if self._runtime is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            result = await self._runtime.check_qr()
            safe_result = {
                key: result.get(key)
                for key in ("state", "message", "nickname")
                if key in result
            }
            payload = self._webui_login_payload(safe_result)
            return {"success": True, **payload}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": type(exc).__name__}

    @Tool(
        "xiaoheihe_check_content",
        brief_description="检查一段内容是否符合 maibot 的小黑盒发布边界",
        parameters=(
            [
                ToolParameterInfo(
                    name="content",
                    param_type=ToolParamType.STRING,
                    description="需要检查的拟发布文本",
                    required=True,
                )
            ]
            if ToolParameterInfo is not None and ToolParamType is not None
            else None
        ),
    )
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

    @Command(
        "xhh_status",
        description="可在 MaiBot 聊天中输入 /xhh_status，查看小黑盒运营插件状态。",
        pattern=r"^/xhh_status$",
        permission="operator",
    )
    async def handle_command(self, **kwargs):
        result = await self.handle_status(**kwargs)
        return True, result["message"], 2

    @Command(
        "xhh_login",
        description="可在 MaiBot 聊天中输入 /xhh_login，生成小黑盒扫码登录信息。",
        pattern=r"^/xhh_login$",
        permission="operator",
    )
    async def handle_login_command(self, **kwargs):
        result = await self.handle_request_login(**kwargs)
        if not result.get("success"):
            return False, f"小黑盒登录二维码生成失败：{result.get('error') or result.get('message', 'unknown')}", 1
        return (
            True,
            "小黑盒登录二维码已生成，请打开 MaiBot 的‘小黑盒运营’插件工作台扫码；扫码后使用 /xhh_login_check 检查状态。",
            2,
        )

    @Command(
        "xhh_login_check",
        description="可在 MaiBot 聊天中输入 /xhh_login_check，检查扫码登录状态。",
        pattern=r"^/xhh_login_check$",
        permission="operator",
    )
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


def _normalize_selection_values(value: object, *, max_items: int | None) -> list[str]:
    result: list[str] = []
    raw_values = value if isinstance(value, list | tuple | set) else str(value or "").replace("，", ",").split(",")
    for item in raw_values:
        token = str(item or "").strip()
        if token and token not in result:
            result.append(token)
        if max_items is not None and len(result) >= max_items:
            break
    return result


def _normalize_selection_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _normalize_topic_ids(value: object) -> list[str]:
    """Backward-compatible helper for old callers that supplied numeric IDs."""

    return [item for item in _normalize_selection_values(value, max_items=None) if item.isdigit()]


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


def _error_label(exc: BaseException) -> str:
    """Persist a useful, bounded error label without storing raw payloads."""

    category = str(getattr(exc, "category", "") or "").strip()
    status_code = str(getattr(exc, "status_code", "") or "").strip()
    message = normalize_text(str(exc), 160).strip()
    if not message or message.casefold() == type(exc).__name__.casefold():
        message = ""
    parts = [category or type(exc).__name__]
    if status_code and status_code != "0":
        parts.append(f"http_{status_code}")
    if message:
        parts.append(message)
    return ":".join(parts)[:500]


def _safe_status_data(value: object) -> str:
    """Keep probe diagnostics short and free of raw response payloads."""
    if not isinstance(value, dict):
        return normalize_text(str(value or ""), 300)
    allowed = {}
    for key in ("source", "accepted_count", "next_cursor", "has_more", "errors"):
        if key in value:
            allowed[key] = value[key]
    return normalize_text(str(allowed), 500)


def _comment_text(value: object) -> str:
    """Extract comment text from a thread-tree item without assuming one schema."""

    if not isinstance(value, dict):
        return str(value or "")
    for key in ("text", "content", "comment_a_text", "comment_text", "message"):
        candidate = value.get(key)
        if candidate not in (None, ""):
            return str(candidate)
    nested = value.get("comment")
    if isinstance(nested, dict):
        return _comment_text(nested)
    return ""


def create_plugin():
    return XiaoheiheOperatorPlugin()

