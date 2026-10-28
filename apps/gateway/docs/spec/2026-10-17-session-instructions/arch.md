# Session 创建指令｜当前 RPC

> 2026-10-17，按当前Gateway实现与真实socket测试编写。Agent所有者设计/实现见
> [提示词插件](../../../../agent/docs/spec/2026-10-17-prompt-plugin/arch.md)，验证见
> [验证记录](../../../../agent/docs/spec/2026-10-17-prompt-plugin/plan-validation.md)。

## 请求与行为

只在 `session.create` 支持 `instructions`：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "session.create",
  "params": {
    "workspace_path": "/absolute/workspace",
    "session_id": "example",
    "load_runtime": false,
    "instructions": "当前会话的静态调用方指令"
  }
}
```

- omitted/None：无外部指令；空字符串可表示显式空内容。StrictStr拒绝数字/数组等强转。
- load_runtime=False仍在Agent应用入口校验并同事务持久化；真正运行时的完整bundle预算在加载时检查。
- 两层校验采用Agent实际配置：max_section_tokens默认4096，字符与UTF-8 bytes双约束；不截断。
- Gateway提前验参失败返回JSON-RPC -32602；应用/启动期已知PromptError映射业务错误，data.code为安全的prompt_too_large/prompt_invalid/prompt_source_failed。
- 返回的SessionStatus不含指令；不新增公共RuntimeUpdateType，history/notifications不带指令或标记。
- submit/steer/get等共享参数模型不增加该字段；重复create沿用session_exists，不覆盖原记录。

## 所有权

Gateway只验证参数、调用AgentRuntime/SessionApi并映射错误。私有数据的原子保存、每次load恢复和提示词装配均由Agent拥有。
ConnectionHub、runtime.update wire、订阅过滤、历史cursor不变，现有WebUI/TUI无需为私有初始化记录适配。

真实E2E位于 `apps/gateway/test/test_prompt_e2e.py`：临时外部插件静态/动态贡献、真实网络、生产模型适配器、本地确定性服务和Gateway重启；另有受限真实远端模型烟测证据。
