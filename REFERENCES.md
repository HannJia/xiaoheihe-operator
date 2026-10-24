# 参考项目与实现边界

## 直接代码来源

- [`674537331/astrabot_plugin_xiaoheihe_adapter`](https://github.com/674537331/astrabot_plugin_xiaoheihe_adapter)，MIT License，`vendor_xiaoheihe/` 中的客户端和协议适配代码基于其公开版本整理、改写并补充脱敏、签名、重试和状态核验逻辑。对应提交记录见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)。

## 接口和行为参考

- [`opxqo/xiaoheihe-cli`](https://github.com/opxqo/xiaoheihe-cli)：参考小黑盒网页端发帖字段、正文图片块格式和公开接口行为；本插件没有复制其代码。
- [MaiBot 官方插件开发指南](https://docs.mai-mai.org/plugin/) 与 [发布插件](https://docs.mai-mai.org/plugin/submission)：参考 Manifest v2、生命周期、配置、能力声明、WebUI 和插件市场流程。

## 运行时依赖

- `maimai-drawpic-plugin`：通过正式 v2 API 生成和恢复配图任务。
- `org.orge-8.life-frequency`：通过只读 API 获取生活轨迹。
- `org.mai-mai.self-identity`：通过只读 API 获取默认身份参考图。

主动运营策略、萧鱼儿内容边界、SQLite 状态、幂等发送、配额、冷却、回读核验和小黑盒业务流程均由本项目独立实现。
