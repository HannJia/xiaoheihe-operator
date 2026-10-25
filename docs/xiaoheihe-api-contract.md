# 小黑盒请求契约核对记录

本文件记录插件当前隔离出的请求边界。小黑盒未提供稳定的公开自动化 API，线上写入仍以 dry-run 和读回核验为准。

## 已固定的评论写入边界

- 地址：`https://api.xiaoheihe.cn/bbs/app/comment/create`
- 方法：`POST`
- Content-Type：`application/x-www-form-urlencoded;charset=utf-8`
- 表单：`is_cy`、`link_id`、`reply_id`、`root_id`、`text`
- 主评论使用 `reply_id=-1`、`root_id=-1`。
- 楼中楼使用收到评论的真实 `reply_id` 和 `root_id`。
- 网页端查询字段使用 `web_version=3.0`、`version=999.0.4`、`client_type=web`、`x_app=heybox_website`。
- 评论写入不附加通用的 `heybox_id` 和 `device_id` 字段。
- `_time`、`nonce`、`hkey` 和 v15 `_rnd` 在请求边界统一生成。

## 结果与幂等边界

写请求超时、连接中断或响应形状无法确认时记录为 `send_unknown`，不自动重发。真实发送前先把动作标记为 `sending`；重启后只读帖子树核验，不根据未知结果再次写入。通知事件和动作键使用 SQLite 唯一键，已尝试的通知不会重复回复。

## 参考来源

请求契约交叉参考 `SengokuCola/xiaoheihe_adapter`、`Insect-rippler/XIAOHEIHE_MCP`、`674537331/astrbot_plugin_xiaoheihe_adapter`、`AaronZ345/xiaoheihe-cli`、`lumingfei004/dph-heybox`、`Whereis-Alice/astrbot_plugin_xhhrobot`、`fbbDirge/dsh-xhh` 和 `k1m0206/better-XiaoHeiHe`。具体固定提交和许可证记录见 `work/reference-audit/`（仅用于本地审计，不随插件发布）。
