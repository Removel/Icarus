# 客户端共享配置与标题编辑实施记录

2026-10-23。需求与跨应用协议见 [共享契约](../../../../../spec/2026-10-23-client-settings-and-session-titles.md)。

已检查 ConfigModel、config_loader、ToolExecutionSettings、SessionApi、SessionStore、会话标题生成及对应测试。

- SessionApi 增加 rename_session，与自动标题生成共用 title_lock；SessionStore.save_title 增加显式 overwrite 参数，自动生成默认仍不覆盖。改名不追加 RuntimeUpdate，不重写会话正文。
- config/settings_service.py 对现有配置文件提供 EditableSettings 投影，使用 ConfigModel 原校验。默认路径与 get_config 一致；显式 ICARUS_SETTINGS_PATH 同样有效。写入只更新 agent、skill、model_settings、model_roles，其他字段原样保留；API Key 不进入读取响应。
- 文件摘要作为 revision，拒绝明显的外部修改冲突，临时文件与目标位于同目录后原子替换。不引入设置数据库、热切换运行中任务或多租户抽象。
- 聚焦测试覆盖手动标题优先级、重开持久化、消息历史不变、配置密钥脱敏、其他字段保留、冲突和无效角色不落盘。Gateway 负责请求参数和错误映射。

仍待实现：凭据写入专用接口、跨端 settings.changed 通知与 TUI 设置表单；这些不改变当前共用同一文件的行为。
