# 小黑盒请求契约核对记录

本文件记录插件当前隔离出的请求边界。小黑盒未提供稳定的公开自动化 API，线上写入仍以 dry-run 和读回核验为准。

## 已固定的评论写入边界

- 地址：`https://api.xiaoheihe.cn/bbs/app/comment/create`
- 方法：`POST`
- Content-Type：`application/x-www-form-urlencoded;charset=utf-8`
- 表单：`is_cy`、`link_id`、`reply_id`、`root_id`、`text`
- 主评论使用 `reply_id=-1`、`root_id=-1`。
- 楼中楼使用收到评论的真实 `reply_id` 和 `root_id`。
- 2026-11-12 按官网公开脚本核对：x_app=heybox_website、x_client_type=weboutapp、web_version=3.0、version=999.0.4，带账号 heybox_id 和稳定 device_id。
- 评论使用 v16 _rnd：AES-256-GCM、12 字节随机 IV、AAD 为 heybox-rnd:v16，明文为时间字符串、nonce、带尾斜杠路径的 JSON 数组；密钥为官网客户端公开协议常量，不是用户凭证。hkey 和 v16 必须使用同一路径。
- 原 v15 评论组合返回“评论功能暂不可用”；当前参数加 v16 已通过空文本校验和一次经用户明确授权的真实评论/读回验收。点赞契约维持原 v15，未批量改变其他写入端点。
- HTTP 403 只记录 forbidden，不据此删登录凭证；401 或业务明确 relogin 才触发认证失效处理。
- 直接依据：官网 index-C10NdQWW.js 中的 Uq、PW、aSe、Lke，以及 safe-route-CRoAfWje.js、index-Dl9lYtpi.js 的评论提交。

## 帖子点赞边界（0.5.0）

- 地址：`https://api.xiaoheihe.cn/bbs/app/profile/award/link`
- 方法：`POST`，表单 `link_id`、`award_type`；`award_type=1` 点赞，`0` 取消。插件只发送 `1`，重复发送不会叠加。
- 读回：帖子详情里的 `is_award_link` 从 `0` 变成 `1`。
- 来源：参考 `lumingfei004/dph-heybox` 的抓包记录，不是小黑盒公开文档；每天的点赞数量由 `likes_per_day` 限制。

## 结果与幂等边界

写请求超时、连接中断或响应形状无法确认时记录为 `send_unknown`，不自动重发。真实发送前先把动作标记为 `sending`；重启后只读帖子树核验，不根据未知结果再次写入。通知事件和动作键使用 SQLite 唯一键，已尝试的通知不会重复回复。

## 参考来源

请求契约交叉参考 `SengokuCola/xiaoheihe_adapter`、`Insect-rippler/XIAOHEIHE_MCP`、`674537331/astrbot_plugin_xiaoheihe_adapter`、`AaronZ345/xiaoheihe-cli`、`lumingfei004/dph-heybox`、`Whereis-Alice/astrbot_plugin_xhhrobot`、`fbbDirge/dsh-xhh` 和 `k1m0206/better-XiaoHeiHe`。具体固定提交和许可证记录见 `work/reference-audit/`（仅用于本地审计，不随插件发布）。
