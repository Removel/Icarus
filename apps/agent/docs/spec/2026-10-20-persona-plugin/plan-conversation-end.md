# 新建对话时整理人格：基础版实施计划

| 项目 | 内容 |
|---|---|
| 日期 | 2026-11-08 |
| 状态 | 基础版代码已实现；离线验证见文末，真实模型效果待验收 |
| 当前架构 | [arch.md](arch.md) |
| 专题入口 | [plan.md](plan.md) |
| 代码基线 | PR17 `feat/agent`，`49b188448cf6736b51e648105e4b6bdb85c1e6cb`；未合并 PR18；已确认目标 `upstream/feature`（`Removel/Icarus`）是祖先 |
| 目标 | 复用“新建对话”操作整理上一段历史，不增加结束按钮，不关闭旧会话 |

## 1. 范围与交付边界

基础版流程：保存对话历史 → 用户选择“新建对话” → 后台截取上一会话尚未处理的历史 → 提炼与 JEV 校验 → 保守合并并原子保存 → 后续会话加载。
“周期”是内部划定的一段历史，不表示整条会话结束。旧会话仍可继续，后续只整理新增部分。
本计划取代此前“显式结束并关闭会话”的设计，保留专题文件名；旧方案不再作为实施目标。

- Agent 负责应用层触发、短生命周期后台任务、历史快照及 Persona 整理；Gateway 与 WebUI 的接入分别按[Gateway 计划](../../../../gateway/docs/spec/2026-11-08-persona-consolidation/plan.md)、[WebUI 计划](../../../../webui/docs/spec/2026-11-08-persona-consolidation/plan.md)实施。
- 提供 `SessionApi.request_persona_consolidation(workspace_path, session_id)` 应用 API，快速返回接收或跳过结果；不注册为主 Agent 工具，不提供关闭会话 API。
- 第一版产品入口为 WebUI 现有“新建对话”，不新增按钮。TUI 接入后置，不在本轮修改 Textual 代码。
- 整理是独立模型调用，不继承主 Agent 工具、运行循环或插件图；由 Persona 的普通组件实现，应用层负责调用与资源清理。
- 不在 `AgentCompletedEvent`、普通切换历史会话、切换工作区、断线、空闲卸载、插件 `stop()` 或应用退出时触发整理；也不把所有 `session.create` 调用当作触发。
- 新建聊天与整理独立推进。整理返回 busy、失败或服务不可用，不额外阻断新聊天。WebUI 保留原有执行中禁止新建的交互限制；服务端 busy 跳过处理跨页签、竞态和其它客户端请求，不因此扩大本轮导航改造。
- 相比之前的基础版，增加应用内受控后台执行与增量游标；暂不提供持久化任务队列、定时重试、启动扫描恢复、跨进程写入、复杂冲突仲裁、演化管理界面。

运行假设沿用当前单用户部署、全局 `persona_id` 资产。共享多用户人格的隔离不在本次实现范围；不能把本计划作为多用户隔离已经具备的依据。

## 2. 改造前的代码基础

- `plugins/persona/plugin.py`：启动时加载人格和 overlay；每任务交付 `persona-observations`；保留攻击检测、漂移检测和关系档案贡献。
- `plugins/persona/tools.py`：主 Agent 生成候选并调用 `persona_evolve`，工具校验后立即写入。
- `plugins/persona/judge.py`：已有 grounded / in_scope / worth_persisting 三项判据；默认 `bounded_recent` 只保留最近 8 条、合计 4000 字符，不能直接当作整个周期证据。
- `plugins/persona/store.py`：已有文件解析、预算和临时文件加 `os.replace`；当前是追加与 FIFO 裁剪，没有周期提交记录、去重和冲突处理。
- `application/controllers/session_api.py`、`repositories/session_store.py`：持久化 `user.message` 和公开更新；`services/update_pipeline.py` 异步处理后续更新。
- `plugins/runtime_update/plugin.py`：持久化的用户纠正为 `user.correction`；公开助手消息可能包含同任务多个步骤。
- `application/session_title.py`：已通过 `LLMFactory` 和公共模型接口完成独立调用，可复用该调用方式，不能导入具体厂商实现。

## 3. 固定行为决策

### 3.1 复用新建对话，不关闭旧会话

- 客户端仅在现有“新建对话”动作经过离开确认、实际执行时，携带当时的工作区与旧 session ID 请求整理；用户取消离开时不触发。
- 当前没有旧 session ID、已在空白页、没有新增可用历史时不执行提炼。服务端不能从全局“最近活跃会话”推断上一会话，多标签页互不混用。
- 有排队、执行中任务或其它在途工作时跳过，原因 `busy`；不取消任务、不挂起聊天、不等任务完成后自动补跑。下次从该会话新建对话时再尝试。
- 不保存会话结束状态，不拒绝旧会话的普通输入或 steer。runtime loaded / unloaded 与整理范围无关。
- 请求接口快速返回 `accepted`、`already_running` 或 `skipped`，附 session ID、请求 ID（已接收时）和受控原因；后台完成结果区分 `applied`、`no_change`、`failed`。
- 失败不清除未处理范围；再次从该旧会话新建对话，或通过应用 API 手动重试，会重新尝试。第一版不增加用户可见的重试按钮或常驻状态面板。

### 3.2 历史快照契约

新增 `agent_app/contracts/persona.py`，只定义本次调用需要的不可变数据，不导入具体插件、数据库实体或 UI 类型。
快照复用 `SessionIdentity`，包含稳定 `cycle_id`、`persona_id`、起止序号 `start_sequence` / `end_sequence` 和有证据编号的角色文本。
`start_sequence` 为上次成功处理的游标（首次为 0），处理范围为 `(start_sequence, end_sequence]`；`cycle_id` 由工作区、session、persona 和起止序号确定，相同范围重试不生成新值。

应用层历史所有者从 `SessionStore` 的已持久化记录投影快照，Persona 不自行访问 Blackboard 内部状态或读取数据库。

- 使用 `user.message` 和 `user.correction` 中的用户文本，保留来源序号与任务归属；投影契约明确这是已保存的用户可见文本，不冒充模型收到的全部原始输入。
- 助手文本仅作为理解纠正的上下文：成功任务取最后一条完整 `assistant.message`，按序还原；失败/取消任务的助手输出不当作已完成的行为证据。
- 保留失败任务中的用户明确纠正及任务状态，不因助手失败而丢掉用户表达。
- 只有范围内新增消息产生候选；必要时可读取游标之前的少量上下文，但须标注仅供理解，不能重复从旧片段生成候选。依赖边界外证据而无法可靠判断时跳过，不凭空补全。
- 排除 System Prompt、插件贡献、工具输出、思考内容、图片和运行时包装。用户粘贴的材料仍是待判断数据，不能只凭角色为 user 就认定为用户自身偏好。
- 不使用 Blackboard 压缩摘要替代原始证据；证据编号须能回查到本周期持久化记录。
- 长度超出整理输入预算时返回 `input_too_large`，保留周期供后续人工处理；基础版不静默截断、不声称支持任意长对话。分段提炼后置。

### 3.3 应用内后台任务与历史落盘顺序

应用服务持有后台 task 的强引用、完成回调和异常处理；不能只调用一次 `asyncio.create_task` 后丢弃。
同一工作区/session/persona 至多一个任务；应用内并发和等待数量有固定上限，容量不足返回 `skipped/capacity`，不持续堆积。
任务与旧会话插件实例解耦，卸载原 runtime 不取消已取得快照的整理。

1. 请求入口校验会话存在、未删除，开关启用，应用正在接受请求；快速检查忙碌与任务容量，注册任务后立即返回。
2. 后台任务通过更新队列的应用层屏障，确认此前更新已处理；等待时不持有 mutation lock，避免与 UpdatePipeline 死锁。
3. 在每会话 mutation lock 内复查在途任务、加载/卸载冲突、历史完整性和持久化错误；忙碌或冲突则跳过。无运行中 runtime 时仍检查持久化的任务终态，不能把断线任务当作正常完成。
4. 固定已落盘游标，与 Persona 已成功游标比较；没有新增可用内容则跳过。取得不可变范围快照后释放会话锁，提炼和审核不阻止后续输入。
5. 快照之后旧会话新增的消息留到下一次处理；同会话并发请求返回 `already_running`，不能生成重叠范围的两个任务。
6. 整理失败保留受控错误与未推进的游标；API 手动重试或再次自然触发时重试。停机取消并等待现有任务、释放模型/JEV 客户端，然后关闭存储，不在停机时创建新任务。

无需改造通用 Plugin Runtime。后台执行、队列屏障、调度上限和快照选择都属于应用层。

### 3.4 提炼、审核和保守合并

独立提炼器的输入为周期快照、当前人工人格和完整已有演化记录；输出严格校验的候选列表，允许 `[]`。
每个候选仅含 `section: voice | relationship`、`note`、`reason`、`evidence_ids`；证据必须属于本周期，且至少有一条用户表达。

- 优先提炼用户明确希望持续生效的表达纠正或有清楚证据的稳定习惯；助手自述、用户沉默和一次正向回应不单独证明长期偏好。
- 不保留一次性格式要求、临时情绪、人物事实、敏感细节、工具行为、权限、身份、边界或义务变更。
- `relationship` 只表示相处风格，不保存人物关系事实。当前是全局人格，不能把指向特定第三人的要求泛化到所有关系。
- 框架维护的提炼指令用英文，候选和证据保留原语言；明确历史与旧记录是评估数据。
- 独立模型通过 `LLMFactory` 公共接口调用，无工具；新增扁平 `evolution_model_role`，默认 `perception`，仍可选已有模型配置，不增加厂商分支。
- 新增 `evolution_enabled=false` 显式开关；关闭时返回 `skipped/evolution_disabled`，不推进游标、不创建完成收据，新建对话照常继续。
- 增加独立提炼超时、输入预算与候选数量上限；建议默认 30 秒、12000 保守 token 单位、最多 5 条。实际请求须满足所选模型的上下文与输出预算。
- 复用现有 JEV 三项判据和阈值。演化校验显式接收候选对应的用户证据及必要上下文，避免旧 `bounded_recent` 裁剪掉早期纠正；超预算显式失败。
- JEV 除原判据外看到已有记录和人工人格，以识别语义重复、冲突。任一判据未通过则跳过该候选；格式错误、超时、服务不可用则整个整理失败，本轮不写入任何候选。
- 本地先去除规范化文本完全相同的候选，再做语义审核；与人工人格或旧记录冲突时保留旧内容。基础版只添加，不自动修改、删除或重写旧项。
- 提交前按人格加进程内锁，重读人工人格与 overlay；若自审核以来任一文件版本已变化，返回 `persona_changed` 供下次触发或手动重试，不实现复杂并发重审。
- 继续执行条目和 token 预算；整理提交不再为接纳新候选而自动 FIFO 淘汰旧记录，容量不足的新候选跳过，返回计数。

`persona.md` 始终只读。无候选、全部被判不合格、重复或容量跳过时，结果均为 `no_change`，不是失败。

### 3.5 最小持久化与重试

不新增会话结束表，也不增加持久化任务队列。Persona 将新增条目和来源处理进度写入同一个 `evolved.yaml`，一次临时文件加原子替换完成；空结果也推进进度。
来源键包含工作区与 session ID（persona 由文件目录限定）；保存 `processed_through` 及最近一次周期的 cycle ID、起止序号、`applied/no_change`、处理时间和计数。
同一来源进度单调前进。相同或更早的截止范围返回已处理结果；即使“写入成功但调用方没有收到结果”，也不会重复追加。
每个来源保留最新进度即可，不为每次点击无限追加完成收据；进度不能随人格条目淘汰删除。

- overlay 升级为带完成收据的格式，兼容读取 v1 条目，首次写入整体升级；保留旧条目 ID、来源和人工编辑内容。
- 来源进度与人格条目共用文件字节上限，超限明确失败，不能丢弃游标腾空间；大量历史来源的归档后置。
- 读路径可保留现有启动降级行为，但写路径遇到损坏文件必须拒绝写入，不能按空 overlay 覆盖旧数据。
- 提炼/校验/写盘失败不推进游标、不保存完成收据。进程内可保留失败快照供手动重试；重启丢失的未提交快照可在下次触发时，按成功游标重新截取更大的未处理范围。
- 应用重启不扫描和自动恢复未完成任务；下次自然触发或显式 API 重试时恢复处理。第一版不承诺每段对话最终都会整理，忙碌、断线和任务容量都可能使一次触发跳过。
- 本方案只保证单进程、同文件原子替换与后续触发重试；不承诺跨进程互斥或断电后的强持久性。

### 3.6 后续会话的人格加载

- 人工人格和已校验、已提交的长期表达共同构成启动时固定的人格资产，在现有 `persona` 静态层装配；演化段明确服从人工设定及当前用户任务，不授予权限。
- 这一步只读取已持久化的风格配置，不把原始对话、理由、证据、用户事实或实时插件状态搬进稳定 System Prompt；动态 Plugin context 继续进入当前 User。
- 删除逐轮 `persona-observations`，删除主 Agent 的 `persona_evolve` 工具与 `persona-05-evolution` 指导；保留正常的空 completed 贡献，避免 required contributor 等待超时。
- 保留关系档案、攻击防御、漂移重锚的原有调用时机和动态交付；漂移参考可包含启动时生效的演化风格。
- 运行中的 SessionRuntime 不刷新人格。整理尚未成功时创建的新 runtime 使用当时已提交的版本，不等待整理。
- 基础版不持久化每个会话的人格版本：卸载后重载按当前文件重新冻结；跨重载保持原版本属于后续增强，验收不混淆这两种保障。
- 有效人格仍会随每次模型请求进入上下文。收益来自删除额外的逐轮观察交付和限制内容体积，不承诺人格只消耗一次 token。

## 4. 实施步骤

以下步骤已落实到源码及聚焦测试。提炼器测试合并在 `test_consolidator.py`；原 `test_tools.py` 随旧工具删除。历史读取复用现有 `SessionStore`，没有新数据库表。

| 顺序 | 文件与职责 | 测试及完成条件 |
|---|---|---|
| 1 | 新增 `contracts/persona.py`；修改 `application/services/history_projector.py` | 新增 `test/agent_app/application/test_persona_snapshot.py`：角色、纠正、任务状态、证据序号、无注入背景、超长显式失败 |
| 2 | 新增 `application/services/persona_consolidation.py`；修改 `controllers/session_api.py`；按需扩展 `session_store.py` 只读快照接口 | 新增 `test_persona_consolidation.py`：请求快速返回、后台强引用、任务数量上限、同来源去重、忙碌跳过、落盘屏障、旧会话仍可输入、停机清理；不新增结束状态或数据库表 |
| 3 | 新增 `plugins/persona/extractor.py`、`consolidator.py`；修改 `judge.py`、`prompts.py`、`config.py`，按需调整工厂装配 | 新增 `plugins/persona/test_extractor.py`、`test_consolidator.py`，扩展 `test_judge.py`：空结果、证据引用、早期纠正、禁止范围、JEV 不可用、独立模型与客户端关闭 |
| 4 | 修改 `plugins/persona/store.py` | 扩展 `test_store.py`：v1 兼容、结果与来源游标原子保存、空结果推进、同/旧范围不重复、旧会话续聊只处理新增、来源隔离、文件变更拒绝、损坏拒写、容量不足保留旧项 |
| 5 | 修改 `plugins/persona/plugin.py`、`manifest.json`；删除旧 `tools.py` 及仅服务旧工具的状态；更新两份 settings | 调整 `test_tools.py`、`test_prompt_contribution.py`、`test_config.py`、`test_integration.py`：旧工具不可用、无观察贡献、静态人格冻结、后续 runtime 看到更新、其它 Persona 能力仍工作 |
| 6 | 应用 API 与真实 SessionRuntime 的离线集成验证；实现后更新 `arch.md` 与本计划状态 | 对话、请求后台整理、校验、写盘、后续 runtime 加载；新聊天不等待整理、旧会话继续与增量处理、失败后再触发、卸载/stop 不触发、无新增不调用模型 |
| 7 | 按 Gateway、WebUI 所属计划依次接入和验收 | 无新增按钮；现有新建操作才请求旧会话整理；离开取消、RPC 失败、忙碌、断线、多标签页与普通历史导航不影响新聊天 |

表中测试路径以 `apps/agent/test/agent_app/` 为根；源码路径以 `apps/agent/src/agent_app/` 为根。
同步更新 `resources/default_settings.json` 和 `apps/agent/settings.json`；删除 `evolution_min_turns_between_accepts` 等仅供逐轮工具使用的配置及对应测试，不删除攻击/漂移仍需的轮次与窗口。
旧配置字段的迁移同步文档说明，不能因 `extra="forbid"` 让仓库默认配置无法启动。

## 5. 验收与验证顺序

- [x] 普通回复和用户纠正不触发人格写入；无 `persona_evolve`、`persona-observations`；仅独立整理可写入，新建后继续旧会话期间可能完成先前范围的整理。
- [x] 新建操作不等待模型整理；快照只含已落盘的完整范围；在途工作跳过；旧会话始终可以继续输入和读取历史。
- [x] 早期用户纠正能够被引用和审核；没有变化正常完成。临时要求、事实、权限排除已进入提示和审核判据，实际效果见下方未完成项。
- [x] 相同来源重复触发不重复生效；空结果推进游标；失败不推进；旧会话续聊只处理新增范围。本版不额外读取游标前的上下文。
- [x] 提炼、JEV、写盘失败均保留旧人格；错误可区分；空结果与全部被拒绝不是服务失败。
- [x] 运行中人格快照不变；后续新 runtime 加载更新；保留关系档案/攻击/漂移功能与测试。
- [x] 新建触发时创建的新 runtime 不等待整理，也不热刷新；只有成功提交后启动的 runtime 才保证加载该结果。
- [x] WebUI 现有新建入口完成接入；没有结束按钮、会话关闭状态或新增确认弹窗；普通历史切换不触发，离开取消和 RPC 故障不误触发或阻断聊天。
- [ ] 真实模型效果样例覆盖持续偏好、一次性要求、无变化、与旧项冲突；离线 fixture 通过不能等同于模型提炼质量已验证。

验证顺序：最小新增/受影响测试 → Persona 与应用层相关目录 → `make test-agent` → `make typecheck-agent` → 使用 Agent 私有环境执行 `python -m compileall -q apps/agent/src` → `git diff --check`。
Gateway 接入后运行 `make test-gateway`；WebUI 接入后运行 `make test-webui`，先各自最小受影响测试，再所属应用套件。TUI 未改动不因本计划扩大修改范围。
真实模型/JEV 冒烟使用隔离数据与少量样例，按仓库规则取得执行/费用授权，不暴露密钥、不写生产人格。
记录每项实际命令、退出码、失败身份和未执行原因；不修改无关失败、不预填通过数字。

## 6. 回退与后续增强

实施时使用临时数据验证升级并保留用户数据备份；回退代码前先处理 overlay 格式兼容，旧代码可能把新版文件当作无效 overlay。
不能通过删除人格、来源进度或历史来回退；关闭 `evolution_enabled` 停止接受新整理，停机时取消/等待在途任务后再回退，不恢复旧主 Agent 工具。

后续仅在实际使用需要时增加：持久化后台任务、定时重试与启动恢复、忙碌后自动补跑、长对话分段、跨重载人格版本固定、多用户隔离、复杂冲突合并和 TUI 接入。
本次不判定用户是否“真正结束”，也不按再见、静默或 runtime unload 猜测结束。

## 7. 实施验证记录

2026-11-08：在 PR17 工作区实现，未切换或合并 PR18。聚焦验证覆盖快照投影、严格提炼、JEV 全周期上下文、静态风格冻结、v1 升级、损坏拒写、版本冲突、容量、原子替换失败、来源游标、重复请求、失败重试、后台取消与更新队列屏障。

| 验证 | 结果 |
|---|---|
| Windows：`python -m pytest apps/agent/test/agent_app/plugins/persona apps/agent/test/agent_app/application/test_persona_consolidation.py apps/agent/test/agent_app/application/test_persona_snapshot.py -q` | 最终复验 125 passed，退出码 0；包含 PR18 可选工具标记兼容用例 |
| Windows：`python apps/agent/scripts/test-contracts.py -q`，`PYTHONUTF8=1` | 118 passed，退出码 0，与 Windows CI 契约入口一致 |
| WSL：`make typecheck-agent` | 最终源码复查：277 个源码文件无错误，退出码 0 |
| WSL：`make test-agent` | 在跨文件系统读取长期缓慢后中断，未取得完整最终结果，不能记为全套通过 |
| WSL：`python -m pytest apps/agent/test/agent_app/application/test_session_runtime.py -q` | 20 passed，退出码 0；覆盖初次 Windows 回归出现的会话运行时失败 |
| Windows：`python -m pytest apps/agent/test -q --tb=short --junitxml=apps/agent/.artifacts/persona-consolidation/agent-windows.xml` | 1271 passed、36 skipped、49 failed，退出码 1；失败身份与基线核对见下文 |
| Windows：Agent 与 Gateway 源码/测试 `compileall` | 退出码 0 |
| Gateway / WebUI | 详见各应用计划的实际记录：Gateway Windows 全套 81 passed；WebUI 聚焦 9 passed，完整回归中的平台脚本失败在 WSL 单独通过 |
| `git diff --check`，新增文档链接、UTF-8 和行末空白 | 通过，退出码 0 |

初次 Windows Persona/应用目录回归为 219 passed、3 failed，三项均在 `test_session_runtime.py`（runtime_update 兼容、agent_run 历史持久化、知识工具）；随后同文件在 Linux 上 20 项通过。Windows 类型检查遇到 POSIX API 类型差异，正式类型结果使用上表 Linux 检查。完整套件不全绿的限制必须随实现交付，不通过修改无关用例消除。

Windows 全套的 49 个失败从 XML 按完整 node ID 提取，在同一解释器中加载临时目录的 PR17 HEAD 原始 Agent 源码逐项复测：49 failed，退出码 1，全部复现。失败分布如下；没有 Persona、新整理服务或新增快照测试失败：

| 测试身份（相对 `apps/agent/test/`） | 失败数与原因 |
|---|---|
| `agent_app/application/test_session_runtime.py` | 2，runtime_update 兼容和知识上传；Linux 同文件 20 项通过 |
| `agent_app/config/test_runtime_environment.py` | 1，Windows 默认编码写入后按 UTF-8 读取 |
| `agent_app/plugins/knowledge/test_plugin.py`、`test_tools.py`、`test_service_config.py` | 9，POSIX 目录描述符与绝对路径假设 |
| `agent_app/plugins/memory/test_service_config.py` | 1，POSIX 绝对路径假设 |
| `agent_app/plugins/prompt/test_workspace_sources.py` | 1，CRLF/LF 断言差异 |
| `agent_app/plugins/skill/test_generation_integration.py`、`test_generation_tools.py`、`test_job_manager.py`、`test_plugin.py`、`test_repository.py` | 30，POSIX 目录/进程接口、Shell fixture 等原有 Windows 兼容问题 |
| `icarus/core/tools/test_result_store.py` | 1，POSIX 文件权限位断言 |
| `icarus/runtime/test_capability_handler_failure.py`、`test_contract_bundles.py` | 2，既有能力超时/插件加载断言 |
| `icarus/test_import_boundaries.py` | 2，Windows 默认 GBK 解码 UTF-8 源码失败 |

本地报告保存在忽略目录 `apps/agent/.artifacts/persona-consolidation/agent-windows.xml` 和 `agent-windows-baseline.xml`；基线比对没有切换分支、替换工作区源码或修改无关测试。36 项跳过仍按原套件策略保留，不计作通过。

PR18 组合检查：`git merge-tree --write-tree` 将本轮代码与 PR18 `78cd11f` 组合，无文本冲突，没有修改任何分支。隔离组合副本的 Persona、应用整理、快照、RuntimeUpdate 与 Gateway 整理回归共 148 passed；WebUI 的整理、过程消息、可靠性及新建/草稿用例共 29 passed，类型检查通过。快照兼容旧消息缺省字段及 PR18 的 `has_tool_calls`，只接收成功任务最后一条无工具调用的助手消息，不回退采用更早的过程文本。组合验证不代替 PR18 同步最终 PR17 基线后的正式门禁。

未执行真实模型/JEV 冒烟：仓库要求单独的执行/费用授权，本次仅调用本地确定性模型和 HTTP fixture。离线通过不能证明长期偏好提炼质量。
