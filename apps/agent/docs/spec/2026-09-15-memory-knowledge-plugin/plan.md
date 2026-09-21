# Memory、Knowledge 与 Blackboard Development Plan｜记忆、知识与黑板实施计划

## 目标

基于 `apps/agent/docs/spec/2026-09-15-memory-knowledge-plugin/arch.md` 和 `apps/agent/docs/spec/2026-08-15-plugin-eventbus-blackboard/arch.md`，按三个可独立验收、独立提交的核心功能完成实现：

1. Blackboard 多 Region 当前状态板；
2. MemoryPlugin 与自建 Mem0；
3. KnowledgePlugin 与自建 OpenKB。

每个阶段必须依次完成代码、定向测试、该阶段 E2E、Agent 全量测试、运行证据审查和独立 Git commit。后一阶段不得依赖尚未提交的前一阶段工作。最终再执行跨功能真实对话、持久化记录审查和全仓回归。

## 实施前审查结论

### 合理性

- Blackboard 继续是普通 Plugin，Region 更新仍走 EventBus；不向 Plugin Runtime 增加 Memory/Knowledge 分支。
- Memory 和 Knowledge 与 Skill、MCP 同级；Coordinator、Adapter、HTTP Client 是 Plugin 内普通对象。
- ReActAgent、model_provider、ToolExecutor 公共协议保持不变。
- Memory 复用现有 `TaskContextInputEvent` 与 `PREPARING_CONTEXT`，Knowledge 复用现有 Tool 路径。

### 需要做的减法

- 第一阶段不提供没有调用方的 `blackboard/region_view` Capability。两个 Blackboard Tool 直接持有同一个只读 Region Store；未来出现 Plugin 读取调用方时再增加 Capability。
- 不修改 `PluginRegistration` 增加通用清理回调。Region Registration Handle 由 owner Plugin 持有，并在现有 `stop()` 路径释放。
- 不为 Memory 建立第二套 Context Event、写入队列、后台 MemoryAgent 或 LLM 判断器。
- 不为 Knowledge 建立 MCP Server、Region、自动召回、后台 Job 或进度协议。
- 不为外部 HTTP 服务引入新的全局框架；两个 Adapter 直接使用显式依赖的 `httpx` Client，并通过现有 `BaseTool.ainvoke -> asyncio.to_thread` 和自动召回后台任务保持同步/异步行为一致。
- 不提前实现多个并行活动 Task 的 Region namespace；当前 Session FIFO 保持单活动 Task。

### 对现有代码的修改边界

| 现有模块 | 必要修改 | 明确不改 |
| --- | --- | --- |
| BlackboardPlugin | Region Store、readiness、只读 Tool、完整 Run History | EventBus 路由和 ReActAgent |
| AgentPlugin/TaskChannel | 仅复用既有 Context 入口 | 不增加 Memory 分支 |
| SessionRuntime | 注入 Plugin 配置并把两个 Plugin 加入标准图 | 不负责调用外部服务 |
| Plugin Runtime | 只消费新的 Manifest 声明 | 不解释 Region 或后端协议 |
| ConfigModel | 沿用 `runtime.plugin_config`，Secret 从环境读取 | 不增加 Mem0/OpenKB 强类型顶层配置 |
| Persistence | Blackboard Session State 版本升级 | 不新增 Memory/Knowledge 本地事实库 |

## 阶段一：Blackboard 多 Region

### 代码范围

新增：

- `apps/agent/src/agent_orchestration/plugins/blackboard/regions.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/tools.py`
- 对应 `apps/agent/test/agent_orchestration/plugins/blackboard/` 测试。

更新：

- `blackboard/events.py`：增加 `BlackboardRegionUpdatedEvent`；
- `blackboard/state.py`：只增加 `input_id / required_regions / completed_regions` 等协调字段；
- `blackboard/prompt_composer.py`：接收有界 Region 紧凑投影；
- `blackboard/plugin.py`：Region 生命周期、readiness、完整 Run History 提交和持久化；
- `blackboard/factory.py` 与 `manifest.json`：提供 `region_registry`、两个只读 Tool 和事件声明；
- 必要的 Manifest、SessionRuntime 集成测试。

### 实现内容

1. 定义不可变 `RegionDefinition`、`RegionInput`、`RegionOutput`、`RegionState`、`RegionSnapshot`。
2. `RegionRegistry` 在 Factory 阶段注册，BlackboardPlugin `start()` 时冻结；重复名称、owner、状态和预算严格校验。
3. `BlackboardRegionUpdatedEvent` 只携带 owner 载荷；Blackboard 从事件来源补齐 owner、从接受时间补齐 `updated_at`。
4. 新 UserInput 为所有 input Region建立当前 `input_id` 初始快照，并固化 required Region。
5. 只有 owner、当前 `task_id/input_id`、合法状态和合法预算的完整替换才生效；旧结果只记录为拒绝，不覆盖状态。
6. readiness 同时等待现有 required ContextContribution 和 required Region；成功、空、失败、超时都可完成。
7. Prompt 只自动加入有界 compact view，不展开 `data`。
8. `blackboard_list` 和 `blackboard_read` 只读本地 Snapshot；`refs` 只筛选公开的 `output.refs/data.items`。
9. Blackboard 按 `apps/agent/docs/spec/2026-09-20-agent-run-history-steering/arch.md` 提交完整、协议闭合的 Run 消息。
10. `context_tokens` 对实际提交消息做确定性保守估算，不把 Task 累计 Usage 当成当前请求大小。
11. Session State 升级并兼容恢复旧 v1；input Region 不持久化，session Region 持久化。

### 测试门槛

- Region 注册、冻结、释放、重复/非法定义；
- owner-only、旧 input、已清理 Task、非法状态与预算拒绝；
- required/optional readiness、空/失败/超时终态、只发布一次 Context；
- input/session 生命周期和恢复；
- compact view 与两个只读 Tool 的筛选和预算；
- 完整 Run History 在成功、取消和可提交失败时遵守统一消息闭合规则；
- 原有 ContextContribution、压缩、图片和双终态清理回归；
- SessionRuntime E2E：注册 Region owner 测试 Plugin，输入等待 Region，Agent 看到 compact view，Tool 可读且不可写，恢复后 Conversation 正确。

提交门槛：上述定向测试、`make test-agent`、compile 和 `git diff --check` 全部通过；检查 Trace/Session State 实际内容后提交 `feat(agent): add multi-region blackboard`。

## 阶段二：MemoryPlugin 与 Mem0

### 代码与源码范围

新增 `plugins/memory/`：`models.py`、`backend.py`、`mem0_http_adapter.py`、`coordinator.py`、`plugin.py`、`tools.py`、`factory.py`、`manifest.json` 及镜像测试。

将锁定版本的 Mem0 fork 作为普通源码导入 `apps/mem0/`，不保留嵌套 `.git`，增加 Icarus Compose/启动配置、`MODIFICATIONS.md` 并保留上游许可证。

### 实现内容

1. 稳定内部模型只暴露 `ref/content/relevance/scope/created_at/updated_at`。
2. Adapter 实现 search/get/history/add/update/expiration/delete，隔离 Mem0 Payload。
3. `user_id/agent_id` 来自 Plugin 必填配置；Global 与 Workspace 映射到 `run_id`。
4. 自动召回订阅 UserInput，后台执行单次 OR 搜索；从事件时间计算 1s 绝对截止。
5. 同一不可变、最多 3 条且序列化 `items <= 6000` 字符的 Snapshot 先发布 `TaskContextInputEvent`，再发布完成 Region。
6. 副流程只取得 Reader；8 个主 Agent Tool 才能读写。
7. Stop/Restore 使用 expiration；Delete 明确不是 purge；所有维护操作先校验归属。
8. Secret 只读取固定环境变量，错误与 Trace 脱敏。
9. Mem0 PostgreSQL/history/backups 全部 bind mount 到 `$ICARUS_DATA_DIR/services/mem0`。
10. Adapter 持有的 `httpx.Client` 在 Plugin `stop()` 中幂等关闭；不新增通用 HTTP Service 或全局连接池。

### 测试门槛

- Fake HTTP 契约覆盖全部 8 个 Tool、鉴权、Payload、响应归一化和错误；
- 自动召回命中/空/失败/超时/迟到/取消/唯一终态；
- TaskContext 注入严格先于 Blackboard 放行；
- Global/Workspace OR 隔离、停止引用默认排除、归属校验；
- 1s 延迟测试记录 p50/p95/p99，确定性测试 p95 不超过 1s；
- SessionRuntime E2E：假 Mem0 写入、下一轮自动召回、Agent Prompt/Trace/Conversation 检查；
- 真实自建 Mem0 Smoke：启动服务、写入、召回、纠正、停止、恢复、删除；使用现有 API Key 和 Flash 模型配置，不输出 Secret。

提交门槛：定向测试、真实 Smoke、`make test-agent`、compile、diff 和数据落盘检查全部通过；提交 `feat(agent): integrate mem0 memory plugin`。

## 阶段三：KnowledgePlugin 与 OpenKB

### 代码与源码范围

新增 `plugins/knowledge/`：`models.py`、`backend.py`、`openkb_http_adapter.py`、`plugin.py`、`tools.py`、`factory.py`、`manifest.json` 及镜像测试。

将锁定版本的 OpenKB fork 作为普通源码导入 `apps/openkb/`，增加 `OPENKB_CONFIG_DIR`、Icarus 启动配置和 `MODIFICATIONS.md`，保留上游许可证。

### 实现内容

1. 注册固定 `knowledge_query/list/read/upload/recompile` 五个 Tool。
2. Adapter 只实现 `/query /list /page /add /recompile`，不提供任意 path/method 或删除。
3. Query 固定 `stream=false/save=false`；不接 `/chat`。
4. Upload 只允许 Workspace 内普通文件，执行 resolve、symlink、格式与容量检查。
5. Recompile 使用 document/all_documents 二选一，歧义返回候选。
6. OpenKB config/kbs/backups 全部位于 `$ICARUS_DATA_DIR/services/openkb`；fork 支持 `OPENKB_CONFIG_DIR`。
7. Secret 固定从环境读取，ToolResult 和 Trace 不回显正文、Token 或远端堆栈。
8. Adapter 持有的 `httpx.Client` 在 Plugin `stop()` 中幂等关闭，与 Memory 不共享领域 Client。

### 测试门槛

- Fake HTTP 契约覆盖五个接口、Bearer 鉴权、错误归一化；
- 上传路径逃逸、symlink、目录、格式和容量边界；
- 重编译二选一、单文档、多候选、显式全库；
- ToolRegistry 和 Backend 均无删除能力；
- SessionRuntime E2E：上传测试文档、list/read/query/recompile，确认不创建 Region、不阻塞输入启动；
- 真实自建 OpenKB Smoke：使用现有 API Key 和 Flash 模型完成上传、编译、查询与重编译；检查数据只进入 `$ICARUS_DATA_DIR/services/openkb`。

提交门槛：定向测试、真实 Smoke、`make test-agent`、compile、diff 和数据落盘检查全部通过；提交 `feat(agent): integrate openkb knowledge plugin`。

## 外部源码、配置与合规收口

与阶段二、三实现一起完成，不留到发布后补：

- `apps/mem0/LICENSE`、`apps/openkb/LICENSE`；
- 两个 `MODIFICATIONS.md`；
- 根 `THIRD_PARTY_NOTICES.md`；
- 根 README、`apps/agent/README.md`、根 `.example.env`；
- 普通 clone 含完整源码，无 `.gitmodules`、嵌套 `.git` 或 named volume 事实源；
- README 区分已实现能力与未来扩展。

## 最终系统验收

1. 从全新临时 `ICARUS_DATA_DIR` 启动 Mem0、OpenKB 和 Icarus。
2. 使用 Flash 主模型触发完整对话：
   - 写一条记忆；
   - 下一轮自动召回并在 1s 门闩内进入 Agent；
   - 主动纠正/停止/恢复记忆；
   - 上传知识文档并查询；
   - 读取与重编译知识；
   - 用 `blackboard_list/read` 查看 Memory 当前状态。
3. 审查 RuntimeUpdate、Trace、Blackboard Session State、`icarus.db` 和服务数据目录：
   - 完整 Run History 保留已闭合 Tool Group 和已应用 Runtime Context；
   - Trace 能重建完整执行；
   - input Region 不跨 Session 恢复；
   - Secret 未写入日志、状态或 ToolResult；
   - Mem0/OpenKB 数据未写入源码目录或用户 Home。
4. 执行 `make test-agent`、`make test-gateway`、`make test-tui`、compile 和 `git diff --check`。
5. 对三个提交逐一审查职责、依赖方向、同步/异步一致性、错误降级、资源关闭和无关改动。
6. 建立需求到代码/测试/运行证据清单；只有每一项都有直接证据才视为完成。

## 停止条件

- 某阶段未通过自己的 E2E，不提交、不进入下一阶段。
- 真实服务因外部凭据或环境失败时，先保留 Fake 契约与本地服务证据；不得把未执行的真实 Smoke 描述成通过。
- 发现设计必须扩大到 ReActAgent、Plugin Runtime 业务分支或跨应用不可拆要求时，先停下修正文档，不以临时旁路继续实现。
