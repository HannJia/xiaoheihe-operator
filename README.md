# 小黑盒运营插件

这个插件把萧鱼儿的生活轨迹、游戏话题和绘图能力接入小黑盒。它使用 MaiBot 官方插件 Runner、独立 SQLite 状态库和小黑盒客户端，不修改 QQ 主链数据。

## 默认安全状态

`config.toml` 默认是 `enabled = false`、`mode = "dry_run"`。完成账号登录、只读接口验收和低额度测试前，不应开启真实自动运营。

主要限制包括每日发帖/评论/通知回复配额、账号级冷却、社区白名单、内容重复键、敏感/技术内容拦截、发送结果未知状态以及认证失效和限流退避。

主动运营默认 fail-closed：`community_allowlist` 为空时不主动评论，`post_topic_ids` 为空时不主动发帖；通知回复仍只处理账号相关通知。

## 登录

启用插件后，打开 MaiBot WebUI 中的“小黑盒运营”工作区即可直接生成二维码、扫码并轮询确认状态。二维码由插件内置的无依赖编码器生成，原始登录 URL 不显示在页面上。

也可以在 MaiBot 聊天中使用：

- `/xhh_login`：生成登录会话，并提示打开 WebUI 工作台扫码。
- `/xhh_login_check`：检查二维码状态。
- `/xhh_status`：查看插件运行模式。

凭证只写入 MaiBot 数据目录下本插件专用的 `credentials/`，日志不会打印 Cookie、Token 或二维码内容。

## 联动

生活轨迹通过 `org.orge-8.life-frequency.get_current_context` 只读 API 获取；身份参考图通过 `org.mai-mai.self-identity.get_default_reference_image` 获取；主动帖子配图通过 `maimai-drawpic-plugin` 的 v2 API 提交、恢复和读取。

## 验证

插件目录的测试覆盖 SQLite 幂等、内容边界、通知解析、绘图 API、媒体签名和 Runner 加载。真实账号验收必须先保持低额度，并确认发帖/评论读回后再扩大范围。

## 参考项目与许可证

本插件的业务逻辑、内容边界、SQLite 状态和 MaiBot 适配代码由本项目独立实现。

- MaiBot 官方插件开发指南：用于 Manifest v2、生命周期、配置模型、能力声明、插件 API、WebUI 和插件市场发布规范。
- `674537331/astrabot_plugin_xiaoheihe_adapter`：`vendor_xiaoheihe/` 中的小黑盒客户端代码基于其 MIT 许可版本整理和改写，具体来源与提交版本见 `THIRD_PARTY_NOTICES.md`。
- `opxqo/xiaoheihe-cli`：仅参考小黑盒正文图片块使用 `type: "img"`、发帖字段和公开接口行为，不复制其代码。
- `maimai-drawpic-plugin`：通过已安装插件公开的 `drawpic_submit_v2`、`drawpic_request_v2`、`drawpic_task_v2` 和 `drawpic_image_v2` API 联动，不复制其实现。
- `org.orge-8.life-frequency` 与 `org.mai-mai.self-identity`：作为 MaiBot 插件间只读 API 依赖，分别提供生活轨迹和身份参考图。

本项目使用 MIT License；第三方许可和来源说明见 `THIRD_PARTY_NOTICES.md`。

## 安装与发布

插件仓库根目录必须保留 `_manifest.json`、`plugin.py`、`LICENSE` 和 `README.md`。发布版本时，Git tag、GitHub Release 和 `_manifest.json` 的版本号必须保持一致。插件市场提交方式参见 MaiBot 官方“发布插件”文档。
