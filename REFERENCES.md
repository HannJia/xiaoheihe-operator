# 参考项目与实现边界

## 直接代码来源

- [`674537331/astrabot_plugin_xiaoheihe_adapter`](https://github.com/674537331/astrabot_plugin_xiaoheihe_adapter)，MIT License，`vendor_xiaoheihe/` 中的客户端和协议适配代码基于其公开版本整理、改写并补充脱敏、签名、重试和状态核验逻辑。对应提交记录见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 接口和行为参考

- [k1m0206/better-XiaoHeiHe](https://github.com/k1m0206/better-XiaoHeiHe)：交叉核对评论创建接口使用的域名、请求参数和签名版本；本项目独立实现请求构造，没有复制该项目代码。

- [`AaronZ345/xiaoheihe-cli`](https://github.com/AaronZ345/xiaoheihe-cli)：参考评论/回复的 dry-run、发送结果核验和发帖图片块行为；本插件没有复制其代码。
- [`SengokuCola/xiaoheihe_adapter`](https://github.com/SengokuCola/xiaoheihe_adapter)：参考通知、主动浏览和上下文总结流程；本插件没有复制其代码。
- [`Insect-rippler/XIAOHEIHE_MCP`](https://github.com/Insect-rippler/XIAOHEIHE_MCP)：参考评论上下文、搜索、发布与签名测试分层；本插件没有复制其代码。
- [`lumingfei004/dph-heybox`](https://github.com/lumingfei004/dph-heybox)、[`Whereis-Alice/astrbot_plugin_xhhrobot`](https://github.com/Whereis-Alice/astrbot_plugin_xhhrobot)、[`fbbDirge/dsh-xhh`](https://github.com/fbbDirge/dsh-xhh)：参考调度、自动浏览、私信状态隔离和生成防护；本插件没有复制其代码。
- [`k1m0206/better-XiaoHeiHe`](https://github.com/k1m0206/better-XiaoHeiHe)：交叉核对网页端请求参数和 v15 签名；本插件没有复制其代码。
- [MaiBot 官方插件开发指南](https://docs.mai-mai.org/plugin/) 与 [发布插件](https://docs.mai-mai.org/plugin/submission)：参考 Manifest v2、生命周期、配置、能力声明、WebUI 和插件市场流程。

## 运行时依赖

- `maimai-drawpic-plugin`：通过正式 v2 API 生成和恢复配图任务。
- `org.orge-8.life-frequency`：通过只读 API 获取生活轨迹。
- `org.mai-mai.self-identity`：通过只读 API 获取默认身份参考图。

主动运营策略、maibot 内容边界、SQLite 状态、幂等发送、配额、冷却、回读核验和小黑盒业务流程均由本项目独立实现。

## 当前发布边界

私信方案仅作为后续设计记录，不属于当前插件市场版本。当前版本保持私信关闭，避免依赖未确认的发送接口或手机自动化环境。
