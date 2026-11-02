# 工具描述发现与执行资格：当前架构

2026-11-02，当前未提交实现；设计及任务见[plan.md](plan.md)。

`tool_search`按scope/name提供工具目录、描述和输入schema，结果可裁剪，tools_loaded元数据仍作为描述发现统计使用；它不授予执行资格。`tool_execute`直接按当前会话registry查找目标，校验当前schema，通过完整ToolExecutor链执行。调用方传来的loaded_tools不再是拒绝条件，也不会扩大权限。

SessionRuntime在绑定catalog/executor前按应用allowlist裁剪registry。未注册、被裁剪或注销的工具即使曾被搜索/出现在历史中，也不可调用。工具自己的运行策略仍生效，例如web-search本轮未启用时拒绝联网，不因为已注册自动获准。

模型tool_definitions仍在每Run固定为现有常驻工具面，Run中不增删/重排；模型可见性与可执行全集分离，未引入供应商协议分支。ReActAgent仍无业务状态，现有Run中的描述发现元数据不跨Run持久化为权限。

同步、异步、stream、astream已有对应行为测试。真实Session第二轮可不search直接调用已知工具，仍接受插件运行策略控制。通用Prompt和web-search插件提示已同步说明：不了解用法时搜索，已知工具无需每轮重搜。MCP自有连接、授权和tool_ref生命周期不因本次改造自动改变。

原lazy-loading feature的arch.md保留旧阶段记录；这里描述新机制。最终回归与限制见Argus配套[执行记录](../../../../argus/docs/spec/2026-11-02-decision-script-atomic-commit/plan-execution.md)，不把测试文本匹配当实际执行权限证明。
