# 阶段性说明与正式回答分离

日期：2026-11-07。沿用当前修复分支及 PR #17 基线。实施验证阶段未新增分支或提交；随后用户授权本地提交，记录见 [统一实施入口](plan.md#2026-11-07-本地提交与运行更新)。

## 问题与实现

一次 ReAct task 包含多轮模型响应，`assistant.message` 只表示某轮完整文本，不能等同于最终回答。旧 WebUI 将所有这类消息渲染为正文，导致“我先查一下”等工具前说明带着复制、点赞、朗读按钮留在正文中。

- Agent 应用的 `RuntimeUpdatePlugin` 从已有统一 `Message.tool_calls` 添加布尔 `has_tool_calls`，保留原始文本与事件类型；不暴露工具参数，不修改 Provider、ReActAgent、提示词或 Gateway 路由。Agent 所有权说明见 [thinking 架构补充](../../../../agent/docs/spec/2026-09-21-agent-thinking-experience/arch.md)。
- WebUI 记录执行 step 与布尔标记，任务内按 step 区分工具前说明。旧历史缺少新字段时使用同一步的 tool 事件兼容，不按文本关键词猜测；不同 task 互相隔离。
- 新到达的未完成文本在过程区随 delta 直接展示，没有正式回答操作。一轮完成且没有工具调用时转为正文；带工具调用的文本保留为过程说明。若应用引导后又开始后续 step，带新标记的先前候选回答也归回过程区。
- 原始 Transcript、历史事件和文本保持完整，归类发生于视图；过程说明不伪装成 thinking。引用继续复用 `ChatContent` 和当前 task 的 sources，手动折叠状态沿用既有所有权。
- 取消/失败时保留已取得的末轮部分文本，不把明确带工具调用的说明提升成回答。未闭合的部分结果不显示完整回答操作。
- IndexedDB 的展示快照格式升级到 2，旧快照先失效再从服务历史重建，防止没有 step 的旧缓存先画出错误气泡。只使浏览器缓存失效，不清除服务端消息或数据库。

## 流式显示边界

收到文本 delta 时尚不能知道本轮将否调用工具；提前把它作为正式回答显示就仍会短暂闪出中间说明。本实现选择在过程区立即展示这些 token，完整响应确认无工具后再呈现正文，沿用最终正文的渐进呈现效果。此举会让正文首次出现等待本轮完成，但过程文本持续可见，不额外等待整个 task.finished。

升级 WebUI 与 Agent 后可在 `assistant.message` 到达时立即准确分类。旧服务的实时完整消息没有标记，只能在同一步工具事件到达后归类；旧历史回放能利用已记录工具事件归类。无法在缺失事实时提前保证旧服务的工具前说明绝不闪现，不用延时或关键词掩盖这一限制。

## 回归与验收

- 已观察修复前浏览器回归 5 failed / 2 passed；Agent 新增投影回归 1 failed / 13 passed。
- 首轮修复后浏览器 7 passed，Agent 投影目录 19 passed。
- 后续补充手动折叠/网页引用、旧格式缓存失效，共 9 项新增浏览器回归。扩大聊天与性能回归时曾得到 99 passed / 3 failed：一项是过程预览未继承未完整公式的流式渲染，已修正；两项是旧测试仍寻找正文气泡，已改为验证过程预览，同时保留虚拟列表、滚动及变宽检查。最终全量覆盖这些改动。
- Windows 下 `UV_OFFLINE=true python apps/webui/scripts/test.py -q`：**388 passed / 13 skipped / 7 failed**，退出 1；本次聊天与性能回归全部通过。7 个失败节点与 11 月 6 日 HEAD 基线核对相同，均位于未改动的 `test_lifecycle_scripts.py`：缺少 Unix 命令或 Windows 无法将无扩展名假 node 当作可执行文件。未隐藏或跳过这些测试。
- `pnpm typecheck`、`pnpm lint`、`pnpm format:check`、`pnpm build` 均退出 0；390 px 与 1440 px 截图已查看，中间说明只有过程展示，正文只有一组回答操作。
- Agent `make typecheck-agent` 检查 274 个源码文件成功；RuntimeUpdate 目录 19 passed。`make test-agent` 全量为 **1363 passed / 1 failed**，耗时 25 分 21 秒，退出 1；唯一失败是持久化测试仍精确断言旧字段。全量进程收集测试后，已将该断言补充为包含 `has_tool_calls=False`，聚焦复测 **1 passed / 30 deselected**。全量进程使用此前收集的旧断言，故仍报告该项失败；没有为仅更新测试预期而再次运行完整套件，不将本次全量标记为全绿。
- Gateway `test_app.py`：4 passed，确认 true/false 均经真实 WebSocket 原样透传。`make test-gateway`：76 passed / 1 failed，失败为 `test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart` 在 `session.unload` 等待 10 秒超时；单文件重跑仍 1 failed / 1 passed。用独立测试进程载入 HEAD 的原始 `RuntimeUpdatePlugin`（验证没有新字段，工作区不改动）作对照，相同用例仍在同一卸载阶段超时，1 failed / 1 passed。没有修改无关会话卸载行为。
- Agent/Gateway 的源码、测试与共享包显式 compileall 退出 0；最终 `git diff --check` 退出 0。原始投影对照脚本与日志仅在 Gateway 忽略的私有环境目录中，不纳入提交。
- 提交前联合复验 Agent RuntimeUpdate 目录与应用 checkpoint：**20 passed / 30 deselected**，退出 0，包含修正后的持久化断言。
- 本轮不调用真实模型或搜索，不修改运行服务、用户 Session 或记忆数据。用户随后授权本地提交；未推送或创建 PR。
