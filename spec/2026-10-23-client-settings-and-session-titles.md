# WebUI 与 Gateway 的共享设置和会话标题

2026-10-23。跨应用契约记录。

WebUI 通过 Gateway 使用 Agent 已有会话标题存储及配置文档，不新建浏览器模型配置库，也不直接调用模型供应商。

- `session.rename(workspace_path, session_id, title) -> { title }`：非空、最多 80 字符，归一化空白；持久化覆盖标题，不修改消息历史。标题生成与改名共用会话 title_lock，生成不得覆盖手动标题。
- `session.generate_title` 优先概括为 4–8 个中文字或 3–5 个英文词。新生成结果在 Agent 内强制限长：含宽字符时最多 12 字符；其余最多 6 词且 40 字符，去除常见标题前缀。该限制不改变手动标题及既有持久化标题。
- `settings.get({}) -> { revision, settings }`：返回 agent、skill、model_settings、model_roles 的可编辑快照，不返回 API Key。
- `settings.update({ revision, settings })`：验证原配置模型、保留未编辑的其他配置字段、文件原子替换，返回新快照。revision 不符返回 settings_conflict；当前任务继续使用已加载配置，新建或重新加载会话读取新配置。
- 运行旧版本的 Gateway 可能返回 -32601。客户端提示重启服务，不能将接口缺失展示为保存成功。

接口由 Gateway 适配；持久化、校验和标题写入由 Agent 所有。供应商差异仍在 model_provider。知识库/记忆只调整 WebUI 入口，不变更它们的服务协议。

WebUI 交互、现有能力与图片上传后续顺序见 [WebUI 补齐规划](../apps/webui/docs/spec/2026-10-23-dswui-chat-integration/plan-refinements.md)。Agent 的配置职责和验证见 [Agent 实施记录](../apps/agent/docs/spec/2026-10-23-client-settings/plan.md)。
