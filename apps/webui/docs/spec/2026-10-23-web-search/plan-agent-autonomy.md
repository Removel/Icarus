# WebUI 默认自动联网执行方案

日期：2026-11-05。状态：实现及离线验收完成；全套有 7 个基线可复现失败，见 [验证记录](plan-validation.md)。

整体目标及顺序见 [Agent 执行方案](../../../../agent/docs/spec/2026-10-23-web-search/plan-agent-autonomy.md)，
服务端字段规则见 [Gateway 执行方案](../../../../gateway/docs/spec/2026-10-23-web-search/plan-agent-autonomy.md)。
沿用 [原联网搜索计划](plan.md) 的进度、来源和历史组件。

## 产品行为

打开聊天即可让 Agent 按需联网，不再需要先点击开启。保留当前输入区按钮，名称改为
“自动联网”，默认选中；点击取消表示显式禁用，再点击恢复自动。深度查证设置保持独立，
不强制搜索，也不覆盖关闭选择。

当前处于开发阶段，直接切换新默认，不迁移旧联网开关偏好。使用含义明确的
`icarus.chat.autoWebSearch` 布尔偏好，默认 true；不再读取旧 `icarus.chat.webSearch`，
无需迁移分支或批量清空 localStorage。

| 保存值/操作 | 界面行为 | 新任务请求 |
| --- | --- | --- |
| 无新偏好，包括仅存在旧版开关值 | 默认选中“自动联网” | 省略 web_search |
| 改版后保存 true | 选中自动 | 省略 web_search |
| 改版后保存 false | 关闭 | 显式 web_search=false |
| 用户关闭/恢复自动 | 保存新的自动联网偏好 | 分别发送 false / 省略字段 |

旧版 true/false 都不影响新默认。改版后用户主动关闭的选择仍然保存，刷新不会重新开启。
自动模式不需要发送 true，显式关闭发送 false。

## 基线与依赖

编制时 HEAD 为 `13743b0`，实际实施基线为 `96006ec`。实施前已核对工作树及 upstream/feature，
保留已提交的提示词改动和其它用户文件。Agent/Gateway 配套修改及离线验收已完成；
不新增跨版本能力探测 RPC，不修改浏览器到搜索服务的连接方式。

## 任务 A：默认选择和请求快照

### 文件和接口

| 文件，相对 `apps/webui/` | 变更 |
| --- | --- |
| `packages/chat-app/src/webSearch.ts` | `SearchPreference.web_search` 改为可选 boolean；保留 deep_search 和时间字段 |
| `packages/chat-app/src/useChatSession.ts` | 改用新偏好 key，默认自动；首次创建会话携带首条消息、普通提交、排队均按相同规则保存 requestOptions |
| `packages/chat-app/src/DswComposer.tsx` | 联网按钮文案/可访问名称改为“自动联网”，说明由 Agent 按需使用，可显式关闭 |
| `test/packages/chat_app/test_web_search.py` | 覆盖新默认、主动关闭的持久化、快照、重试及按钮表现 |

### 步骤

- [x] 先写失败浏览器用例：无旧偏好时默认选中，实际 session.submit 请求省略 web_search，
  同时保留本地时间、时区和 deep_search 快照。
- [x] 使用 `icarus.chat.autoWebSearch`，默认 true，不读取或迁移旧 key；构造请求时
  true 映射为字段省略，false 映射为显式 false。
  可用小型纯函数统一 requestOptions 构造，放在现有模块，不新增状态框架或服务抽象。
- [x] 覆盖首次创建会话附带首条消息、正常提交、排队提交全部构造路径；取消 dispatch 中
  默认塞入 web_search=false 的兜底。兜底使用 `{deep_search: false}`，不得读取重试时的最新按钮
  状态覆盖已有任务选择。
- [x] 在用户创建 Submission 时捕获开关/深度；第一次发送前生成一次时间快照；随后排队发送、
  重连、结果未知重试复用同一对象及 submission_id，不重新补开关或时间。
- [x] session.steer 沿用既有请求格式，不在运行中尝试改变当前任务联网策略。
- [x] 明确区分按钮“允许自动联网”和“正在搜索”；aria-pressed、视觉状态与禁用原因一致，
  不把深度偏好或搜索进度当成自动允许状态。

## 任务 B：浏览器行为与历史回归

仍使用 `test/packages/chat_app/test_web_search.py` 的 mock Gateway，扩充实际发送参数断言：

- [x] 默认自动；旧 key 无论 true/false 都不影响默认值，无需编写迁移逻辑。
- [x] 改版后关闭发送 false，刷新仍关闭；恢复自动后省略字段，刷新仍自动。
- [x] 第一条关闭、第二条排队自动，发送前再次关闭，第二条仍保留原自动快照；反方向同样覆盖。
- [x] 创建新会话的首条消息按默认自动发送，不能漏过首次提交路径。
- [x] 断线/结果未知重试保留 options、local_time、time_zone、deep_search 和 submission_id；
  正常切换偏好不能改变已提交或排队的快照。
- [x] 显式关闭 + deep_search=true 仍发送 false，界面不声称强制联网。
- [x] 既有 Search、直接 Fetch、补搜累计来源、空结果/失败、停止、历史回放测试继续通过。
- [x] 更新涉及按钮名称的选择器与可访问断言；浅色/深色下自动和关闭状态仍可区分。

浏览器 mock 只能证明请求和界面行为。真实 Agent 的默认搜索能力由 Agent/Gateway 对应
生产运行链测试验收；不得把 mock 的搜索进度推送当作真实联网成功。

## 验证与记录

通过专用脚本启动独立 dev server 执行最小专项，避免复用未知外部进程：

```powershell
& apps/agent/.venv/windows/Scripts/python.exe apps/webui/scripts/test.py test/packages/chat_app/test_web_search.py -q
```

通过后执行 `make test-webui`；在 `apps/webui/` 运行 `pnpm typecheck`，并对修改的 TS/TSX
执行以下格式检查；回仓库根目录编译修改的 Python 测试，再 `git diff --check`。

```powershell
pnpm exec prettier --check packages/chat-app/src/webSearch.ts packages/chat-app/src/useChatSession.ts packages/chat-app/src/DswComposer.tsx
```

```powershell
& apps/agent/.venv/windows/Scripts/python.exe -m compileall -q apps/webui/test/packages/chat_app/test_web_search.py
git diff --check
```

如果 Make 入口在 Windows 依赖 WSL，使用仓库已支持环境执行，不改写根测试入口绕过失败。

更新 `plan.md` 的本轮默认行为及 `plan-validation.md` 的实际结果，保留旧交付历史。
记录当前失败基线和修复后结果，不用旧调查通过数代替本轮浏览器/全套结果。

## 验收与回退

无需手动开启即可发出自动策略请求；改版后的显式关闭和任务快照均保留。
引用与搜索状态由既有事件呈现，不引入前端搜索调用、额外搜索规划或密钥传输。

回退前等待/核对待确认提交，配套恢复旧默认和发送规则，保留原 localStorage 及历史数据。
不通过清空用户偏好实现回退，不修改已经捕获的 Submission。2026-11-06 用户验收通过并
授权提交 PR；不合并或部署，TUI 引用显示调整留待后续 WebUI 分支处理。
