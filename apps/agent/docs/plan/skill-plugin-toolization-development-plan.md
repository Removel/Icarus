# SkillPlugin 完整 Skill 生产与演化开发计划

## 目标

基于已经完成的 SkillPlugin 工具化实现，增量修正 Produce/Evolve：

- Workspace Skill 使用 `<current-workspace>/skills`，全局 Skill 使用 `$ICARUS_DATA_DIR/skills`；
- Producer/Evolver 在 Job 专属 Draft 中创建或修改完整 Skill 目录；
- 支持 `SKILL.md`、脚本、参考资料、模板和二进制资源；
- 生成 Agent 获得受控的读取、Draft 写入、资源复制和命令验证能力；
- Repository 校验完整目录并事务式发布，保持正式目录的唯一写入权；
- 生成 Agent 继续获得完整脱敏对话证据，但内部轨迹不进入 Blackboard；
- 删除固定 120 秒 Job 总超时；
- 保持主 Agent 对外恰好五个 Skill Tool，以及现有权限、搜索和后台 Job 协议。

## 不变边界

- 不修改 `ReActAgent` 的无状态模型或 `BaseAgent` 公共调用参数；
- 不给 Plugin Runtime、EventBus 或 Blackboard 增加 Skill 专用逻辑；
- 不新增主 Agent 可见 Tool；
- 不恢复自动召回、Embedding、使用次数排名或轮后自动维护；
- 不让 Producer/Evolver 直接写正式 Skill 根目录；
- 不把受限命令执行描述成不可绕过的操作系统沙箱；
- 不恢复未完成 Job 的执行栈；Runtime 恢复时仍统一标记为 `interrupted`。

## 目标链路

```text
主 Agent 调用 skill_produce / skill_evolve
→ SkillPlugin 完成权限、名称、作用域和上下文前置检查
→ SkillJobManager 保存 queued Job 并立即返回 job_id
→ 后台 Job 创建专属 Draft
→ Producer/Evolver 获得完整脱敏证据和受控内部 Tool
→ 生成 Agent 在 Draft 中完成并验证整个 Skill
→ SkillRepository 校验 Draft 与并发快照
→ Repository 事务式发布到正式 Skill 根目录
→ 清理 Draft，保存 Job 终态并通知原任务
```

## 实现原则

- Draft 只是临时目录，不新增持久化领域模型或全局 Job-to-Draft 映射。
- 私有生成 Agent、模型连接和 ToolRegistry 长期复用，不按 Job 重建。
- 当前 Draft 通过调用范围内的 `ContextVar` 绑定；并发 Job 互相隔离，模型不能传入或选择 Draft 根目录。
- `ToolRegistry` 只出现在 Factory 组装层，Producer、Evolver 和 Repository 继续依赖明确的领域接口。
- 文件 Tool 强制路径边界；命令执行只做实用型风险限制，最终成品仍由 Repository 兜底校验。
- 先运行最小测试，再运行 SkillPlugin 测试，最后运行 Agent 全量测试和静态检查。

## 任务一：修正 Workspace Skill 根目录

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/factory.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/test_path_metadata.py`
- Skill Factory、Scanner 和 Runtime 集成相关测试

**实现内容**

- Factory 不再丢弃 `workspace_path`。
- Workspace Skill 根目录改为 `Path(workspace_path).resolve() / "skills"`。
- 全局 Skill 根目录继续使用 `persistence.resolver.global_skills_dir`。
- 删除 `DataPathResolver.workspace_skills_dir(identity)`，避免把 Workspace 源文件和 Icarus 运行状态混为一体。
- `$ICARUS_DATA_DIR/workspaces/<workspace_key>` 继续只保存元数据、Session、日志和 Job 状态。
- 保持 Workspace 同名 Skill 覆盖全局 Skill 的可见性规则。

**测试重点**

- Factory 将当前 Workspace 的 `skills` 目录传给 Scanner 和 Repository；
- 两个作用域扫描、覆盖和 Produce 双作用域冲突检查仍正确；
- Persistence 路径测试不再断言运行状态目录中存在 Skills。

## 任务二：将 Repository 扩展为完整目录与 Draft 发布

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/repository.py`
- `apps/agent/src/agent_orchestration/plugins/skill/coordinator.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_repository.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_coordinator.py`

**实现内容**

- `SkillSnapshot` 从单个 `SKILL.md` 内容 Hash 改为完整目录快照，至少包含目标作用域、目录路径和确定性的目录摘要。
- 目录摘要按排序后的相对路径、文件类型和文件字节计算；符号链接或无法稳定读取的成员使捕获失败。
- 增加扁平 Draft 生命周期方法：
  - 为 Produce 创建空 Draft；
  - 为 Evolve 将完整目标目录复制到 Draft；
  - 校验并发布 Produce Draft；
  - 校验并发布 Evolve Draft；
  - 幂等清理 Draft。
- Draft 创建在目标 Skills 根目录所在文件系统的隐藏临时区域，保证最终目录重命名不跨文件系统。
- 完整目录校验至少包括：
  - 根目录必须存在合法的 `SKILL.md`；
  - YAML `name` 与目标规范化名称一致，`description` 非空；
  - 拒绝绝对路径、路径逃逸、符号链接、设备文件和其他非普通文件；
  - 文件数不超过 256，单文件不超过 20 MiB，总大小不超过 100 MiB；
  - 二进制文件按原始字节处理，不强制 UTF-8。
- Produce 在协调锁内重新检查两个作用域，随后用目录重命名发布，不覆盖现有目标。
- Evolve 在协调锁内重新比较完整目录摘要；Workspace 目标原位替换，全局目标发布为 Workspace 覆盖。
- Evolve 使用“旧目录改名为备份 → 新目录改名为目标 → 删除备份”的事务式替换；中途失败时尽力回滚，不能把半写入文件树标记为成功。

**测试重点**

- 多文本文件和二进制资源 Produce 后逐字节一致；
- Evolve 保留未修改文件，并支持增加、修改和删除文件；
- 全局 Evolve 只创建 Workspace 覆盖，不改全局目录；
- 路径逃逸、符号链接、特殊文件和容量超限全部失败；
- 完整目录任一文件并发变化都会触发快照冲突；
- 发布失败能够恢复原目录并清理临时目录。

## 任务三：实现生成 Agent 的受控内部 Tool

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/skill/generation_context.py`
- `apps/agent/src/agent_orchestration/plugins/skill/generation_tools.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_generation_context.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_generation_tools.py`

**实现内容**

- `generation_context.py` 使用 `ContextVar` 保存一次生成调用的只读上下文：
  - 当前 Draft 根目录；
  - 当前 Workspace 根目录；
  - 全局 Skills 根目录；
  - Workspace Skills 根目录。
- Context 由 Producer/Evolver 调用边界设置并在 `finally` 中恢复；模型参数不包含根目录选择权。
- 同一长期生成 Agent 的并发调用依赖 `ContextVar` 隔离；同步线程执行复用现有 `copy_context()` / `asyncio.to_thread()` 传播。
- 私有 Registry 首期注册少量内部 Tool：
  - `read`：通过逻辑根选择器浏览目录或读取上述允许根目录内的 UTF-8 文本；
  - `write`：只按 Draft 相对路径创建或完整覆盖文本文件；
  - `copy`：通过逻辑根选择器从允许读取的位置复制文本或二进制文件到 Draft 相对路径；
  - `remove`：只删除 Draft 内的文件或空目录；
  - `bash`：固定在 Draft 中执行受限验证命令。
- `read/copy` 拒绝常见凭据路径、越界路径和越界符号链接。
- `write/remove` 使用安全相对路径，并拒绝通过已有符号链接写出 Draft。
- `bash` 不接受自定义工作目录，执行前拒绝明显的：
  - 网络和远程访问命令；
  - 包管理器安装命令；
  - 提权、进程控制和破坏性系统命令；
  - `..`、绝对路径及明显指向 Draft 外的重定向。
- `bash` 使用最小环境变量白名单，不继承 Token、Cookie、代理和凭据；设置单次命令超时与输出上限，超限时终止整个进程组。
- 拒绝结果以普通 `ToolExecutionResult` 返回，让 Agent 可以改用静态检查；不宣称该命令检查能阻止所有子进程绕过。

**测试重点**

- 两个并发 Context 使用各自 Draft，结束后不泄漏 Context；
- 所有文件写入、复制和删除都不能逃出 Draft；
- 允许从 Workspace 和全局 Skills 读取正常材料，拒绝外部路径与明显凭据文件；
- 二进制复制保持字节一致；
- Bash 固定 cwd、清理敏感环境、限制超时和输出；
- 明显网络、安装、破坏和越界命令被拒绝；
- 工具异常统一返回失败，不使 Job 进程崩溃。

## 任务四：改造 Producer、Evolver 与生成 Prompt

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/producer.py`
- `apps/agent/src/agent_orchestration/plugins/skill/evolver.py`
- `apps/agent/src/agent_orchestration/plugins/skill/generation_prompt.py`
- `apps/agent/src/agent_orchestration/plugins/skill/factory.py`
- 对应 Producer、Evolver 和 Prompt 测试

**删除文件**

- `apps/agent/src/agent_orchestration/plugins/skill/generation_models.py`
- `apps/agent/src/agent_orchestration/plugins/skill/generation_parser.py`
- 对应的模型与 Parser 测试

**实现内容**

- 不再要求模型一次性返回 `GeneratedSkill {content}`；最终产物以 Draft 目录为准。
- Prompt 明确提供操作、目标名称、作用域、用户 instructions、完整脱敏对话证据、Draft 路径和完成条件。
- Evolve 不再把单个旧 `SKILL.md` 塞入输出协议；Agent 直接读取已经复制好的完整 Draft。
- 继续将对话证据序列化到当前 `input_prompt`，并保持 `history_messages=[]`，避免未配对 ToolCall 形成非法模型历史。
- `tools=[]` 改为显式内部 Tool allowlist：`read/write/copy/remove/bash`。
- Producer/Evolver 在调用范围内绑定 generation context，Agent 结束后立即恢复。
- 删除 `asyncio.wait_for(..., 120)` 和 `timeout_seconds` 参数；Job 不设置总超时。
- Agent 正常结束后只需确保最终回复为非空文本；Draft 是否完整由 Repository 决定。
- Factory 创建一个长期私有 ToolRegistry，注册上述内部 Tool 后交给长期复用的生成 AgentFactory；不为每个 Job 重建 Registry 或模型连接。
- 注入测试用 `generation_agent_factory` 时保持现有测试替换点，但明确该 Factory 必须能处理内部 Tool allowlist。

**测试重点**

- Producer/Evolver 收到完整脱敏证据、Draft 信息和五个内部 Tool；
- 未配对的当前 Skill ToolCall 不进入 `history_messages`；
- Agent 可以经过多轮 ToolCall 后结束，而不是一次性 JSON 输出；
- Agent 完成后 Draft 内容由调用者继续提交；
- 没有固定总超时，取消仍能向上传播；
- Context 在成功、失败和取消路径都被恢复。

## 任务五：改造 Job 生命周期与子 Run 轨迹

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/job_manager.py`
- `apps/agent/src/agent_orchestration/plugins/skill/jobs.py`
- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_job_manager.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_jobs.py`
- `apps/agent/test/agent_orchestration/plugins/skill/test_plugin.py`

**实现内容**

- Job 执行调整为 `prepare Draft → generate/evolve → publish → cleanup → notify`。
- Draft 只保存在活动后台 Task 的局部变量中，不进入持久化 Job JSON。
- 任何成功、失败或取消路径都在 `finally` 中尝试清理 Draft；成功发布后的清理错误只记录日志。
- 生成阶段保持可取消；进入 Repository 发布临界区后等待发布或回滚完成。
- 启动内部 Agent 前建立 Hook Context：
  - 沿用原 `task_id`；
  - 将 Job 中现有 `run_id` 解释为 `parent_run_id`；
  - 增加 `skill_job_id`、`agent_kind=skill_generation`、`operation`、`skill_name`；
  - 清空父 `run_id`，由内部 `ObservableAgent` 为生成过程创建新的子 `run_id`。
- 内部 Agent 的 LLM 和 Tool Hook 继续进入当前 Session 的 `trace.jsonl`，可以按 `skill_job_id` 或子 `run_id` 关联。
- 内部消息、ToolCall 和 Tool Result 不写入 Blackboard；仅终态摘要继续通过 `TaskContextInputEvent` 通知原任务。
- 保持现有 Job 状态机、终态持久化、通知结果记录和恢复为 `interrupted` 的规则。

**测试重点**

- Produce/Evolve 完整 Draft 生命周期和状态迁移；
- 生成失败、发布失败、取消和清理失败的终态正确；
- 发布阶段不会被退出清理打断；
- Trace 中生成 Run 有独立 `run_id`，同时保留 parent run、task 和 job 关联；
- Blackboard 对话中不存在内部生成轨迹，只出现最终通知。

## 任务六：Factory、文档与残留清理

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/factory.py`
- `apps/agent/src/agent_orchestration/plugins/skill/__init__.py`
- `apps/agent/docs/arch/plugin-event-flow-current-state.md`
- `apps/agent/docs/todo/agent-core.md`
- `apps/agent/docs/todo/development-roadmap.md`
- 相关 Runtime 与应用集成测试

**实现内容**

- Factory 使用真实 Workspace Skill 根目录并组装私有生成工具。
- 主 Agent 的 `PluginRegistration.tools` 仍恰好是五个 Skill Tool；内部生成 Tool 不进入 Manifest、公共 Capability 或共享主 Agent Registry。
- Repository、Producer 和 Evolver 不导出内部 ToolRegistry。
- 删除单文件生成模型、Parser、`content_hash` 语义、旧 Workspace Skills 路径和 120 秒总超时的残留引用。
- 更新当前状态与 Roadmap，使其描述完整目录 Draft、事务式发布和子 Run Trace。
- 保持 `allow_produce=False`、`allow_evolve=False` 默认值，不新增无明确需求的配置项。

**残留检查**

```bash
rg -n "SkillGenerationParser|GeneratedSkill|timeout_seconds=120|workspace_skills_dir|tools=\[\]" \
  apps/agent/src apps/agent/test
```

预期无生产代码残留；若测试夹具需要表达空 Tool 场景，必须与 Skill 生成链路无关。

## 任务七：分层验证与真实 Smoke Test

**最小测试**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill/test_generation_context.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_generation_tools.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_repository.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_producer.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_evolver.py \
  apps/agent/test/agent_orchestration/plugins/skill/test_job_manager.py -q
```

**Skill 与集成测试**

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/skill \
  apps/agent/test/agent_orchestration/plugins/persistence \
  apps/agent/test/agent_orchestration/plugin_runtime \
  apps/agent/test/application -q
```

**全量检查**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

存在可用模型凭据时，使用临时 Workspace 和临时 `ICARUS_DATA_DIR` 做真实 Smoke Test：

1. 临时开启 `allow_produce`，生成包含 `SKILL.md`、脚本和二进制资源的 Workspace Skill；
2. 确认 Job 可查询、主 Agent 收到终态通知、Trace 有独立生成子 Run；
3. 临时开启 `allow_evolve`，修改脚本、增加文件并删除旧文件；
4. 确认原目录只在发布成功后整体变化，失败路径不留下半成品；
5. 确认 Workspace Skill 写入 `<workspace>/skills`，全局测试数据只写临时 `$ICARUS_DATA_DIR/skills`。

## 完成标准

- 主 Agent 对外仍只有五个 Skill Tool；
- Produce/Evolve 能用完整脱敏上下文和受控工具完成多文件 Skill；
- Workspace 与全局 Skill 路径符合新定义；
- Repository 能校验并事务式发布文本和二进制完整目录；
- Evolve 的完整目录并发变化不会被覆盖；
- 生成 Agent 不能通过文件 Tool 写入 Draft 外或直接发布正式 Skill；
- 明显危险、联网、安装和越界命令会在执行前被拒绝；
- Skill Job 不再受固定 120 秒总超时限制；
- 生成轨迹可在 Session Trace 中按 Job 关联，但不污染 Blackboard；
- Skill、Runtime 和 Agent 全量测试、compileall 与 `git diff --check` 全部通过。
