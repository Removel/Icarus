# 新建对话时整理人格：WebUI 接入计划

日期：2026-11-08。状态：已实现；基于 PR17，验证结果见文末。

## 目标和依赖

用户继续使用原来的“新建对话”，应用在后台请求整理上一段历史。不增加结束按钮、确认弹窗或必须等待的整理步骤；旧会话仍可回来继续。

依赖[Gateway 协议计划](../../../../gateway/docs/spec/2026-11-08-persona-consolidation/plan.md)和[Agent 基础版计划](../../../../agent/docs/spec/2026-10-20-persona-plugin/plan-conversation-end.md)。
本应用只维护动作来源与请求时机，不在浏览器提炼候选、保存人格或维护处理游标。

## 当前基础

- `packages/chat-app/src/ChatApp.tsx` 的 `newChat` 通过 `chat.guard.requestLeave` 执行草稿清理与空白路由跳转。
- `packages/chat-app/src/useChatSession.ts` 的 `createSession` 在首次发送时才调用 `session.create`，并有丢失响应后的 ID 核对机制。
- 普通历史导航、组件卸载和订阅清理具有独立路径，不能在这些公共路径上无条件触发整理。

## 行为决策

1. 用户选择现有新建入口时，捕获当时的工作区与旧 session ID；只在 `requestLeave` 允许离开、执行原跳转的回调里发出整理请求。
2. 尽力调用 `session.consolidate_persona`，随即继续原草稿清理和空白页导航，不 `await` 整理结果。所有入口（含已有快捷键）复用同一动作，避免遗漏或双发。
3. 没有有效旧会话、已经在空白页或连接不可用时不请求；用户取消已有草稿确认时不请求。保持现有草稿确认，不新增人格相关确认。
4. 请求参数用捕获的旧身份；导航后不能从新选中的会话或全局历史推断来源，也不能发送新会话 ID。
5. 拒绝、跳过、超时、旧 Gateway 返回 method-not-found 均不能设置阻止聊天的 error/busy/submitting，不影响首条消息发送及既有丢响应核对逻辑。异步错误必须被接住，不产生未处理 Promise rejection。
6. 本版不展示整理进度，不因失败弹窗；如沿用现有诊断日志，只记录受控错误，不记录对话正文。客户端断线丢失触发不自动补发，后续自然操作或应用 API 可重试。
7. 普通选择历史会话、切换工作区、路由恢复、刷新、组件卸载、取消任务不触发。新建不意味着旧会话关闭；无需禁用旧会话输入。
8. 当前创建的新会话立即可用，不保证获得此次仍在后台生成的人格结果；已启动 runtime 不热刷新。

沿用既有 `busy/submitting` 新建保护；本轮不开放执行中的导航。整理请求自身不会设置这些状态，后台模型进行中也不阻止后续新聊天；服务端负责处理其它页签、客户端或竞态下的 busy 跳过。

实现时可以在 `useChatSession.ts` 暴露一个轻量请求方法，由 `newChat` 成功离开的回调调用；不得为此重写会话创建或统一导航框架。

## 实施步骤

- [x] 新增 `test/packages/chat_app/test_persona_consolidation.py`，覆盖成功新建、离开取消、空白页和普通历史导航。
- [x] 修改 `ChatApp.tsx`、`useChatSession.ts` 和 Gateway fixture；无新增产品按钮或状态面板。
- [x] 慢响应、RPC 拒绝、method-not-found 不阻塞空白页和首条发送，不产生未处理错误。
- [x] 捕获旧身份，所有新建入口复用同一动作；服务端覆盖重复请求合并与旧会话继续输入。
- [x] 补充断线时新建、快捷键、窄屏和接受草稿离开后新建；重连不补发整理。
- [x] 完整浏览器回归草稿保护、切换历史、首条创建与丢回复核对；环境差异见下文。

## 验收与回退

先运行新增聚焦测试和受影响导航/聊天测试，再 `make test-webui`，执行现有类型/构建检查及 `git diff --check`。
真实模型能力由 Agent 侧验收，浏览器测试只用本地 fixture，不访问生产人格。

完成条件：现有新建动作发出且仅发出正确来源请求；新聊天响应速度不依赖模型；普通导航无误触发；旧会话可继续；没有新增结束操作。
回退可仅移除调用，恢复原新建交互；不清除浏览器或服务端历史，不重置服务端已成功游标。

## 实施验证

PR17 工作区，未合并 PR18。使用 `apps/agent/.venv/windows/Scripts/python.exe apps/webui/scripts/test.py test/packages/chat_app/test_persona_consolidation.py -q`：9 passed，退出码 0。
`pnpm --dir apps/webui typecheck` 和 `pnpm --dir apps/webui build` 均退出码 0。
Windows 完整 `scripts/test.py -q`：371 passed、13 skipped、7 failed，退出码 1；此轮包含最初 5 个整理用例，补充的 4 个已在上述聚焦验证通过。
7 个失败均位于未改动的 `test/test_lifecycle_scripts.py`：6 项在 POSIX 工具查找前置条件失败（requires_node、requires_corepack、rejects_unknown_arguments、builds_with_corepack、keeps_nested_pnpm、accepts_system_pnpm），1 项 Node shell fixture 在 Windows 无法执行（launcher_execs_node）。随后使用 WSL Agent 私有环境在 `apps/webui` 执行 `python -m pytest -c pytest.ini test/test_lifecycle_scripts.py -q`：10 passed，退出码 0。没有修改安装脚本或放宽测试。
13 项跳过按现有套件策略保留，不算通过。多标签页共享服务端的独立浏览器组合未单独模拟；相同来源重复请求由 Agent 聚焦测试覆盖。
所有浏览器验证使用本地 Gateway fixture，不调用真实模型。

PR18 `78cd11f` 组合副本：Git 自动组合无冲突；整理、过程消息、可靠性及新建/草稿回归 29 passed，退出码 0，组合源码类型检查通过。隔离副本复用已安装依赖，但 `@icarus` 工作区依赖指向组合后的源码；没有修改 PR18 分支。
