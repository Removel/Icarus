# 新建对话时整理人格：Gateway 接入计划

日期：2026-11-08。状态：已实现；基于 PR17，验证结果见文末。

## 目标和边界

为客户端现有“新建对话”操作提供一个快速、可选的整理请求，由 Agent 在后台处理上一会话的历史。
Gateway 只校验和转发请求，不分析对话、不判断人格、不持有后台模型任务，也不关闭旧会话。

依赖[Agent 基础版计划](../../../../agent/docs/spec/2026-10-20-persona-plugin/plan-conversation-end.md)；调用方见[WebUI 接入计划](../../../../webui/docs/spec/2026-11-08-persona-consolidation/plan.md)。
Agent 先交付请求接口，Gateway 再接入，WebUI 最后启用调用。各文档分别维护本应用实现，不新增根级跨应用实施细节。

## 当前基础

- `src/protocol/methods.py` 使用 `SessionParams` 校验工作区及 session ID，显式转发各个 RPC。
- 当前 `session.create` 只创建会话，不知道客户端此前选中了哪一条会话；不能隐式寻找“最近活跃会话”作为整理来源。
- WebUI 新建动作首先进入空白页，实际创建会话延迟到发送首条消息；整理请求须独立于 `session.create`。

## 协议

新增 `session.consolidate_persona`，参数复用 `SessionParams`：`workspace_path`、`session_id` 均指向旧会话。
响应为应用请求接收结果：

| 字段 | 含义 |
|---|---|
| `session_id` | 请求的来源会话 |
| `status` | `accepted`、`already_running` 或 `skipped` |
| `request_id` | 接收的应用内任务标识；未接收时为空 |
| `reason` | 跳过原因，如 busy、evolution_disabled、capacity、no_new_messages；名称与应用层统一 |

`accepted` 只表示请求已交给应用内任务，不表示人格已更新，也不承诺任务能跨重启恢复。
同来源重复请求由 Agent 合并。无新增内容的检查可能在后台完成，不能要求快速返回前必须扫描完整历史。
接口不等待模型调用，无须为模型耗时延长 WebSocket RPC 超时；本版不新增完成推送、任务轮询 API 或共享 RuntimeUpdate 类型。
不存在、已删除或参数非法按现有业务错误/参数错误契约返回；忙碌或关闭演化是正常跳过，不影响 `session.create`、`session.submit`。

## 实施步骤

- [x] 在 `test/test_persona_consolidation.py` 覆盖参数校验、旧身份转发、接收/合并/跳过、非法来源、不等待模型任务。
- [x] 注册新方法，调用应用 API，增加最小响应模型；没有修改通用 RuntimeUpdate 类型。
- [x] 保持 `session.create`、历史选择、unsubscribe、unload 的语义；旧客户端照常工作。
- [x] 使用真实应用层与本地阻塞模型 fixture 验证后台持有任务，独立创建和新提交不等待模型。
- [x] 与 WebUI fixture 对齐并执行应用回归。

## 验收与回退

先运行受影响测试文件，再 `make test-gateway`，使用 Gateway 私有环境执行编译检查，最后 `git diff --check`。
不得为协议测试调用真实付费模型或生产人格文件。记录实际命令、退出码、失败身份及未执行检查。

验收包括：旧会话仍可提交；新建和新消息不等待整理；重复请求不重复落盘；客户端断开不把已接收任务绑定到连接生命周期。
回退先停用客户端调用，再撤回协议转发；不删除 Agent 保存的人格与来源进度。

## 实施验证

基线 PR17 `49b188448cf6736b51e648105e4b6bdb85c1e6cb`，未合并 PR18。
Windows 私有环境：`python -m pytest apps/gateway/test/test_persona_consolidation.py -q`，4 passed；完整 `python -m pytest apps/gateway/test -q`，81 passed，退出码 0。
WSL `make test-gateway`：80 passed、1 failed（退出码 2），失败为 `test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart` 在 `session.unload` 等待 10 秒超时；单文件复跑同样超时。在临时目录加载 PR17 HEAD 的原始 Agent/Gateway 源码并执行相同文件，仍为 1 failed、1 passed（退出码 1），同一用例同一位置超时，确认是基线问题。不修改该无关卸载逻辑或放宽超时。
Windows Gateway 源码与测试 `compileall` 通过，退出码 0。
所有模型与外部 HTTP 响应均为本地 fixture，未执行真实付费模型调用。
