# 小黑盒运营插件

这个插件把 maibot 的生活轨迹、游戏话题和绘图能力接入小黑盒。它使用 MaiBot 官方插件 Runner、独立 SQLite 状态库和小黑盒客户端，不修改 QQ 主链数据。

## 默认安全状态

`config.toml` 默认是 `enabled = false`、`mode = "dry_run"`。完成账号登录、只读接口验收和低额度测试前，不应开启真实自动运营。

主要限制包括每日发帖/评论/通知回复配额、账号级冷却、社区白名单、内容重复键、敏感/技术内容拦截、发送结果未知状态以及认证失效和限流退避。

## 配置说明

MaiBot WebUI 的插件配置页会显示每个字段的中文用途说明。第一次验收应保持 `enabled = true`、`mode = dry_run`、`feed_interval_seconds = 3600`、`posts_per_day = 0`、`proactive_comments_per_day = 1`、`notification_replies_per_day = 1`。确认请求契约和读回核验后，再由管理员明确切换到 `auto`。

私信功能不属于当前发布范围，默认关闭。小黑盒私信发送依赖独立的客户端聊天界面，当前没有稳定、可复用的公开发送契约；插件不会把私信误判成评论，也不会盲目重试发送。未来方案和取舍见 [私信方案记录](docs/PRIVATE_MESSAGE_OPTIONS.md)。

主动运营默认 fail-closed：`community_allowlist` 为空时不主动评论，`post_topic_ids` 为空时不主动发帖；通知回复仍只处理账号相关通知。

评论回复会先读取原帖标题、正文和收到的评论，再交给内容生成器，因此 maibot 在自己发布的帖子下回复时会围绕该帖内容作答。当前已支持小黑盒通知中心里的提及、评论和楼中楼回复；私信保持关闭。当前开发状态和已知限制见 [项目状态](docs/PROJECT_STATUS.md)。

## 登录

启用插件后，打开 MaiBot WebUI 中的“小黑盒运营”工作区即可直接生成二维码、扫码并轮询确认状态。二维码由插件内置的无依赖编码器生成，原始登录 URL 不显示在页面上。

也可以在 MaiBot 聊天中使用：

- `/xhh_login`：生成登录会话，并提示打开 WebUI 工作台扫码。
- `/xhh_login_check`：检查二维码状态。
- `/xhh_status`：查看插件运行模式。

凭证只写入 MaiBot 数据目录下本插件专用的 `credentials/`，日志不会打印 Cookie、Token 或二维码内容。

## 联动

生活轨迹通过 `org.orge-8.life-frequency.get_current_context` 只读 API 获取；身份参考图通过 `org.mai-mai.self-identity.get_default_reference_image` 获取；主动帖子配图通过 `maimai-drawpic-plugin` 的 v2 API 提交、恢复和读取。`org.orge-8.life-frequency`、`org.mai-mai.self-identity` 和 `maimai-drawpic-plugin` 都必须按 `_manifest.json` 中的依赖安装。

## 外部数据处理

主动帖子使用的生成图片和身份参考图（仅在插件 API 返回 base64 图片数据时读取）会通过小黑盒的上传接口发送到其使用的腾讯云 COS（通常为 `*.myqcloud.com`）以便在帖子正文中展示。插件不会根据插件 API 返回的任意本地 `path` 或 `file_path` 读取文件；需要传图的依赖应返回 base64 或图片字节。登录凭证仍只保存在 MaiBot 数据目录的本插件专用目录中。

## 验证

插件目录的测试覆盖 SQLite 幂等、内容边界、通知解析、绘图 API、媒体签名和 Runner 加载。真实账号验收必须先保持低额度，并确认发帖/评论读回后再扩大范围。

## 参考项目与许可证

本插件的业务逻辑、内容边界、SQLite 状态和 MaiBot 适配代码由本项目独立实现。

- MaiBot 官方插件开发指南：用于 Manifest v2、生命周期、配置模型、能力声明、插件 API、WebUI 和插件市场发布规范。
- `674537331/astrabot_plugin_xiaoheihe_adapter`：`vendor_xiaoheihe/` 中的小黑盒客户端代码基于其 MIT 许可版本整理和改写，具体来源与提交版本见 `THIRD_PARTY_NOTICES.md`。
- `AaronZ345/xiaoheihe-cli`：参考评论/回复的 dry-run、发送结果核验和发帖图片块行为；不复制其代码。
- `SengokuCola/xiaoheihe_adapter`：参考通知、主动浏览和上下文总结的流程边界；不复制其代码。
- `Insect-rippler/XIAOHEIHE_MCP`：参考评论上下文、搜索、发布和签名测试的接口分层；不复制其代码。
- `lumingfei004/dph-heybox`、`Whereis-Alice/astrbot_plugin_xhhrobot`、`fbbDirge/dsh-xhh`：参考调度、自动浏览、私信状态隔离和生成防护；不复制其代码。
- `k1m0206/better-XiaoHeiHe`：交叉核对网页端请求参数与 v15 签名；不复制其代码。
- `maimai-drawpic-plugin`：通过已安装插件公开的 `drawpic_submit_v2`、`drawpic_request_v2`、`drawpic_task_v2` 和 `drawpic_image_v2` API 联动，不复制其实现。
- `org.orge-8.life-frequency` 与 `org.mai-mai.self-identity`：作为 MaiBot 插件间只读 API 依赖，分别提供生活轨迹和身份参考图。
- 图片上传：生成图和身份参考图会通过小黑盒接口上传到其腾讯云 COS；上传主机和媒体 URL 会经过 HTTPS、域名和公网地址校验。

本项目使用 MIT License；第三方许可和来源说明见 `THIRD_PARTY_NOTICES.md`。

## 安装与发布

插件仓库根目录必须保留 `_manifest.json`、`plugin.py`、`LICENSE` 和 `README.md`。发布版本时，Git tag、GitHub Release 和 `_manifest.json` 的版本号必须保持一致。插件市场提交方式参见 MaiBot 官方“发布插件”文档。

## 当前发布范围

- 已纳入：扫码登录、推荐流定时浏览、游戏话题筛选、图文发帖、评论和楼中楼回复、配额/冷却/幂等/读回核验、生活轨迹和生图插件联动。
- 默认关闭：真实自动运营、主动发帖和私信功能。真实写入应先使用低额度配置验收。
- 暂不纳入：手机模拟器或实体手机代理。相关方案只记录在文档中，不会在插件市场版本中偷偷启用。
