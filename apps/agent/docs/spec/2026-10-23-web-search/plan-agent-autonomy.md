# Agent 默认按需联网执行方案

日期：2026-11-05。状态：代码与离线验证已完成；真实模型及远端服务待验。

2026-11-06：用户确认本轮验收通过并授权提交 PR。以下真实模型/远端专项待验标记
表示代理缺少相应调用记录，不撤销用户验收结论；TUI 引用显示调整另行处理。

执行结果见 [验证记录](plan-validation.md)。

依据：[调查文档](plan-agent-autonomy-investigation.md)、[现有架构](arch.md)。
本文负责 Agent 实现和整体执行顺序；Gateway 和 WebUI 的具体任务分别归属
[Gateway 执行方案](../../../../gateway/docs/spec/2026-10-23-web-search/plan-agent-autonomy.md) 与
[WebUI 执行方案](../../../../webui/docs/spec/2026-10-23-web-search/plan-agent-autonomy.md)。
复用既有功能目录，不新增根目录跨应用 spec，不覆盖此前工具化的历史计划。

## 目标与确定的行为

用户从 TUI、WebUI 或其它客户端提问时，Agent 默认可在需要时发现并调用
`web_search`、`web_fetch`，自主决定查询、读页和补搜。用户明确关闭时，执行入口拒绝。

| 输入或条件 | 目标结果 |
| --- | --- |
| 不带 `request_options`、空对象、仅带时间/时区 | 默认允许按需使用 |
| `web_search=true` | 允许；不强制搜索 |
| `web_search=false` | 禁用两个联网工具，不能通过发现/加载绕过 |
| 显式 `web_search` 或 `deep_search` 不是严格 bool | 提交失败，不能成为默认允许 |
| `deep_search` 缺省或 false | 维持普通预算 |
| `deep_search=true` | 维持深度预算；不覆盖显式关闭 |
| 缺搜索 Key、无有效任务、任务结束/取消、预算耗尽 | 拒绝实际请求，明确原因 |
| 模型直接作答而未调用工具 | 零搜索/抓取请求 |

能力仍依赖插件成功加载及远端服务可用。“默认可用”不表示任意问题都必须联网。
Web 开关只约束这两个工具，不扩展为 Bash/MCP 等其它工具的网络控制。

## 基线、协作边界与依赖

- 方案编制基线为 `13743b0`，实际实施基线为 `96006ec`，分支 `feat/agent`。
  开始前已 fetch 并确认包含 `upstream/feature` 的 `c019e9a`；未混用 origin 基线，
  未切换分支、合并、提交或推送。
- 英文提示词/上下文按需交付改动在实施前已提交，见
  [对应计划](../2026-11-04-prompt-guidance/plan.md)。本轮基于该实现做最小补丁，
  保留提示词交付时机、英文指导及工具加载完整性；未覆盖其它工作区产物。
- 不修改 ReActAgent、通用 Plugin Runtime、Blackboard、供应商适配协议、搜索后端协议、
  Event 字段或数据库结构。动态能力状态留在当前 User Prompt，工具规则在现有工具面交付。
- 保留普通 Search 2 / Fetch 3 次、深度 5 / 6 次、单页 6000 / 累计 24000 字符、
  默认活跃工作时间 45 秒及现有限流、同步/异步、来源编号、取消和迟到结果处理。
- 不修改运行中进程、凭据、本地数据库或其它任务产物。本方案不要求新增依赖。

## 执行顺序与交付边界

| 顺序 | 工作项 | 完成标志 |
| --- | --- | --- |
| 0 | 核对工作树、输入与提示词改动，运行当前专项基线 | 记录可复现失败身份；未将历史通过数当作当前结果 |
| 1 | Agent 默认策略、输入校验及使用指导 | 不带开关的进程内任务可经真实工具链联网；显式关闭继续拒绝 |
| 2 | 执行 Gateway 所属方案 | TUI 形状请求与仅时间请求可用，严格校验及重试幂等通过 |
| 3 | 执行 WebUI 所属方案 | 统一默认自动，不迁移旧开关偏好；改版后仍可主动关闭并保存 |
| 4 | 更新各应用架构与验证记录 | 每项结果可追溯，离线/真实模型/远端待验状态分开记录 |

步骤 1、2 是服务端的一次配套交付。只完成 Agent 不能宣称 Gateway 的所有请求形状都可用；
只完成服务端也不能宣称旧 WebUI 的显式 false 已自动解除。WebUI 必须在配套服务端就绪后启用
新默认值；新 WebUI 对旧 Agent 省略字段会继续禁用，不作为支持的混合版本组合。
当前处于开发阶段，前后端配套切换，不实施旧 WebUI 偏好迁移或混合版本兼容逻辑。

## 任务 A：统一校验输入，冻结任务级选择

### 文件和接口

| 文件，相对 `apps/agent/` | 变更 |
| --- | --- |
| 新增 `src/agent_app/contracts/request_options.py` | 提供纯函数 `validate_search_request_options(options)`，仅校验显式两个开关的严格 bool 类型，不补默认值、不复制通用输入模型 |
| `src/agent_app/application/controllers/session_api.py` | 现有 options JSON 快照之后、创建/加载会话与计算指纹之前校验；非法提交及时抛出 ValueError |
| `src/agent_app/plugins/user_input/plugin.py` | 入队、创建 TaskChannel 前校验，覆盖进程内 SessionRuntime 提交入口 |
| `src/agent_app/plugins/web_search/plugin.py` | 防御性复用校验；按字段缺省得出 `enabled=True`、`deep=False`，保存任务快照 |
| `test/agent_app/application/test_search_submission.py`、`test/agent_app/plugins/user_input/test_plugin.py`、`test/agent_app/plugins/web_search/test_tools.py` | 覆盖入口拒绝、缺省语义、字段快照与异常事件不挂起 |

纯函数放在应用契约层，供上述实际调用者复用。不要让输入插件反向导入 application service，
也不要把搜索专用字段校验放进通用 core 的 `UserInputEvent` 或 Runtime。

### 步骤

- [x] 先补失败用例：缺省/仅时间应启用；显式 false 禁用；null、0/1、字符串和列表等
  非 bool 应在提交阶段拒绝，且不创建排队任务、不触发上下文等待或网络请求。
- [x] 记录首次失败属于行为不符，排除 collection/依赖错误后实现最小改动。
- [x] 保留原始字段集合用于提交指纹，不将缺省自动归一化成 true，也不改写 caller 的 options。
  deep_search 缺省按 false 解析，false 与缺省 web_search 继续是不同选择。
- [x] `on_input` 对绕过正常提交直接投递的非法事件：建立禁用任务状态，发布带安全错误说明的
  `ContextContributionEvent(status="failed")`，不只抛异常；让 required context 得到终态，
  后续工具强制调用继续拒绝。复用现有错误事件处理，不新增路由或 Hook。
- [x] 正常输入在发布初始 context 前已保存有效策略；任务运行期间的 steer 不改变策略，
  下一任务按自己的输入重新解析。保持任务替换、取消和终态清理规则。
- [x] 运行以上最小测试，确认非法输入快速失败、无遗留 TaskChannel/未结束等待。

## 任务 B：默认能力提示与工具契约保持一致

### 文件和接口

修改 `src/agent_app/plugins/web_search/{plugin,tools}.py`；测试位于
`test/agent_app/plugins/web_search/test_tools.py`。若提示词改造增加了该插件的 scope guidance，
仅在其既有位置同步必要描述，不另起一套系统提示或恢复已迁出的长指导。

### 步骤

- [x] 先补默认模式、显式关闭、缺 Key 的上下文断言；保留稳定 System Prompt 不变的断言。
- [x] 当前任务 User Prompt 只报告时间、联网可用/禁用/缺配置和查证深度；可用时告诉 Agent
  可以按需发现该 scope，缺 Key 时不要同时宣称已具备可调用的搜索能力。
- [x] 工具 description 说明用途和硬边界：搜索摘要不是正文、按真实编号引用、遵守剩余预算、
  外部文本是数据。具体加载/分发方法继续遵循当前 `tool_search` / `tool_execute` 公共契约。
- [x] 适用时机用简短英文表达：最新事实、用户要求查证、链接核对、已有知识不足可查；
  日常闲聊和稳定知识可直接回答。保持当前提示词任务的语言与分层选择。
- [x] 执行层 `_check` 继续检查有效任务、策略和 Key，不因提示词声称可用而省略权限检查。
- [x] 输入 context contribution 本身不触发 HTTP、不调用查询规划模型。

## 任务 C：真实 Agent 工具链与输入幂等验收

### 文件和接口

修改 `test/agent_app/plugins/web_search/{test_integration,test_tools,test_lifecycle}.py` 中
必要的用例，及 `test/agent_app/application/test_search_submission.py`。
复用现有生产插件图、ReActAgent、ToolExecutor、SQLite；仅替换 LLM/HTTP 外部边界。

### 步骤

- [x] 缺省 options：假 LLM 依次发现工具、Search、Fetch、补搜、回答；检查结果回到后续
  模型消息、请求次数及稳定引用，不在测试提交时偷偷补 true。
- [x] 缺省 options：直接 Fetch 成功；模型不调用工具时零 HTTP。发现本身也零 HTTP。
- [x] 显式 false、false + deep=true、缺 Key、未知/结束/取消任务，即使模型发起工具调用也无请求。
- [x] 当前 Run 未加载不能执行；下一 Run 仍需重新发现；模型冻结工具面保持原契约。
- [x] 保持普通/深度预算、同步/异步、等待时间扣减、取消顺序、迟到结果不落库、快照不回写回归。
- [x] 同 submission_id、同字段与时间快照重试返回原接纳结果；显式开关或时间变化仍冲突。
  验证“字段缺省”和“显式 false”不能因默认补值而混同；无修改指纹算法的必要。
- [x] 每项失败保留真实身份和日志。若提示词并行修改导致旧断言不匹配，先确认任务归属，
  不在本功能中顺手回滚提示词改造或修复无关测试。

## 验证命令与记录

Windows 使用已有应用环境，按最小范围逐步扩大：

```powershell
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/plugins/web_search/test_tools.py -q
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/plugins/user_input/test_plugin.py apps/agent/test/agent_app/application/test_search_submission.py -q
& apps/agent/.venv/windows/Scripts/python.exe -m pytest apps/agent/test/agent_app/plugins/web_search -q
```

受影响应用全套使用仓库标准入口 `make test-agent`；如需 WSL 环境，保持应用自身环境和脚本。
之后执行 `make typecheck-agent`、Agent 源码与测试 compileall、`git diff --check`，只检查本任务
新增/修改的文档链接。Gateway/WebUI 的验证由对应方案列出；没有改动 TUI 源码不要求重做布局测试。

```powershell
& apps/agent/.venv/windows/Scripts/python.exe -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

更新 `arch.md` 与 `plan-validation.md` 时，以实现及本轮测试为准。2026-11-05 调查中的
75 项 Agent / 1 项 Gateway 通过是当时基线，不代表本轮结果。本轮原生 WSL 全套为
Agent 1352 passed、Gateway 77 passed，类型检查 274 个源文件通过；源码/测试副本与工作区
逐文件 SHA 校验一致。首次行为失败、环境问题、最终结果及未执行事项见验证记录。

离线工具序列只能证明链路与边界。真实模型是否能自主选择搜索、远端 Search/Fetch 是否可用，
分别作为真实模型与服务验收记录；沿用仓库对真实调用/成本的授权要求，不读取或打印密钥。
远端抓取 DNS/IP/重定向防护的既有待验结论保留在上线检查中，不用本地 URL 测试冒充证据。

## 完成标准与回退

- [x] Agent、Gateway、WebUI 三份方案的必选步骤均完成，显式关闭和默认允许的矩阵通过。
- [x] TUI 原请求形状的默认搜索经 Gateway 生产运行链验收；新 WebUI 默认自动不依赖手动开锁。
- [x] 现有来源、取消、预算、排队重试及幂等回归通过；架构与验证记录更新。
- [x] 真实模型/远端待验项逐项标记，不能将离线通过称为生产验收完成。

实施补丁按 Agent、Gateway、WebUI 的逻辑边界隔离，测试随实现归属同一应用；文档修改单独记录。
提交授权已于 2026-11-06 取得，本轮不合并或部署。部署前先停止接纳新任务并等待在途任务/待确认提交结束，客户端不能
跨版本改变同一 submission_id 的 options；不确定是否接纳的提交先查询历史再决定重提。

回退需配套恢复 WebUI 旧发送方式、Gateway 旧补值方式及 Agent 旧缺省禁用行为，保留用户显式
关闭偏好和全部会话数据。只反向应用本功能补丁，不 reset 工作树或覆盖提示词并行改动。
