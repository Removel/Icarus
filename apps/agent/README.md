# Icarus Agent Runtime

`apps/agent` 提供 Icarus 的模型接入、Agent 执行、Tool、Plugin Runtime、多 Session 管理和本地
持久化能力。它是 Python 运行库，由 Gateway 在同一进程中加载，不提供独立启动命令。

## 当前能力

- OpenAI 与 Anthropic 协议模型接入；
- 无状态 ReAct Agent、文本与 thinking 流式输出和 Tool 调用；
- Manifest 驱动的 Plugin 发现、依赖解析、生命周期和状态恢复；
- Blackboard 上下文、历史提交和自动 Compact；
- 设备级 AgentRuntime 与多个相互隔离的 SessionRuntime；
- Session 创建、恢复、提交、取消、状态查询和卸载；
- 使用 `SessionStore` 持久化 Session 元数据和公共 Conversation，包括 step 级完整 thinking 与
  脱敏、限长的 Tool 输出预览；
- 使用文件保存 Plugin State、Trace、日志和图片 Asset；
- Skill 发现、搜索、生产和演化；
- 通过 FastMCP 连接外部 MCP Server，并以固定的 list/search/execute 工具发现和调用其 Tools。
- Blackboard 多 Region 当前状态、只读 Region Tool 与完整可重放 Agent Run 历史；
- 通过 MemoryPlugin 接入自建 Mem0：默认 5 秒内有界自动召回，并提供 8 个显式记忆 Tool。
- 通过 KnowledgePlugin 接入自建 OpenKB：提供 5 个按需知识 Tool，不开放删除能力。
- 通过 ProcessPlugin 在 Session 内启动和管理开发服务器等长期命令，提供一个参数化
  `background_process` Tool，并在 Session 卸载前收束完整进程组。

## 安装依赖

运行环境：

```bash
./apps/agent/scripts/install.sh
```

开发和测试环境：

```bash
./apps/agent/scripts/install.sh --dev
```

依赖安装在 `apps/agent/.venv`，安装器同时执行本地 editable 安装。公开包为 `icarus`（框架）和
`agent_app`（产品实现），不要再导入 `apps.agent.src.*`。Gateway 的独立 `.venv` 也安装同一项目。

## 模块与插件契约

- `icarus.core`：Message、Event、BasePlugin、BaseTool、BaseAgent、能力与 Hook 契约。
- `icarus.runtime`：发现、装配、注册表、单插件串行 worker、会话设施、记录器和有界扇出。
- `agent_app`：11 个插件、厂商适配、配置和 Controller/Service/Repository。
- manifest v2 按稳定事件 ID 和能力契约声明；插件用 `@handles` 绑定方法，用 `build_tools()` 导出工具。
- 生命周期统一为 start、恢复、自检、就绪；结束时 quiesce、drain、快照、反序 stop。
- Skill 的 `conversation.read` 请求复用总线定向投递；host-only 状态/输入/任务端口不提供给兄弟插件。
  能力调用目前要求无界插件 inbox，避免唯一 router 被满队列阻塞；会话每次请求 correlation 不可复用。
- Hook 只改登记位置上的值，参数与结果在运行期校验；记录与日志由独立记录器/插件后端处理。

完整架构和实施偏差见 [框架业务解耦](docs/spec/2026-10-16-framework-business-decoupling/arch.md)。

## 插件自带事件与能力定义

复用内置定义的插件无需新增字段。引入新事件或能力时，manifest 可指定一个轻量类型模块：

```json
{
  "contracts": "persona.contracts",
  "entrypoint": "persona.factory:create_plugin"
}
```

`persona.contracts` 必须显式导出 `EVENTS`（ID→Event类映射）和 `CAPABILITIES`（CapabilityContract类tuple）；
没有对应类型时导出 `{}` 或 `()`。跨插件共用定义须引用同一类型对象，不能各复制一份同名类。

```python
EVENTS = {"persona.changed": PersonaChangedEvent}
CAPABILITIES = (PersonaCurrent,)
```

框架扫描仍只读JSON；先按manifest检查Python依赖、能力供需/版本、冲突和必需发布者，再集中加载可用候选的定义，
完成未知ID、类型冲突和能力版本校验，重新解析受影响依赖后才执行插件工厂。
相同模块每个Host只处理一次；不同模块重复导出同一ID和同一类型可复用，冲突定义不会按先后覆盖。
定义登记不等于插件已提供能力；manifest仍决定发布、消费和provide/require，运行时绑定只来自成功启用的插件。

嵌入式应用和测试可通过 `PluginRuntimeHost(event_catalog=..., capability_catalog=...)` 注入目录，
Host保留类型身份但复制目录条目，扩展加载不会污染调用方或其他会话。省略参数时使用内置目录。
外部契约模块只从显式允许的扩展代码加载；Python import和父包初始化可以执行代码，不是沙箱，
契约模块应保持无连接、无线程、无插件实例构造，父包 `__init__.py` 也应保持轻量。
多个插件共享或重导出外部契约时，声明模块及实际事件/能力/请求响应类型的定义导入根保留到Host结束，
防止其中一个实现失败后重载产生两份类型。

## 配置

复制并填写仓库根 `.example.env` 为根 `.env`：

```dotenv
OPENAI_API_KEY=your-api-key
ANTHROPIC_API_KEY=your-api-key
ICARUS_DATA_DIR=/absolute/path/to/icarus-data
ICARUS_MEM0_API_KEY=your-local-service-key
ICARUS_MEM0_POSTGRES_PASSWORD=your-database-password
ICARUS_MEM0_JWT_SECRET=your-jwt-secret
ICARUS_MEM0_LLM_API_KEY=your-memory-model-key
ICARUS_MEM0_AUTH_DISABLED=false
ICARUS_OPENKB_API_TOKEN=your-local-service-token
ICARUS_OPENKB_LLM_API_KEY=your-knowledge-model-key
```

只需配置当前协议使用的 API Key。模型、Plugin 目录与运行参数在 `apps/agent/settings.json` 中设置。
`get_config(settings_path=...)` 或 `ICARUS_SETTINGS_PATH` 可显式覆盖；显式路径不存在会报错。
源码 checkout 默认读取现有 settings，普通 wheel 安装使用随包默认 settings；凭据仍只来自环境。
`model_settings` 为含唯一 `name` 的数组，`model_roles` 分别选择 agent/compaction/skill_generation/title；
旧 thinking/perception 映射在加载时转换，后续统一使用 `get_model(name)`。
`runtime.update_queue_capacity` 统一订阅和 Gateway 发送队列的默认容量；显式构造覆盖仍可独立设置。

配置加载器在 JSON 读取后接受连接环境覆盖：`ICARUS_MEM0_ENDPOINT` → `memory.endpoint`、
`ICARUS_OPENKB_ENDPOINT` → `knowledge.endpoint`、`ICARUS_RELATIONSHIP_ENDPOINT` → `relationship.endpoint`
（均位于 `runtime.plugin_config`）。只修改对应 endpoint，不改用户/Agent ID、知识库名、timeout、模型角色或
`required_plugin_ids`，也不启用插件或改写 JSON。未设置变量保留文件值；非 loopback 的知识/关系端点仍遵守
原有 HTTPS/Token 校验。控制面向 Gateway 进程注入这些值，Agent 不读取根 TOML、不导入控制面。
关系工具的配置与 HTTP 边界见 [relationship 架构](docs/spec/2026-10-18-relationship-plugin/arch.md)。

Mem0 服务通过 `icarus start mem0` 启动，数据位于 `$ICARUS_DATA_DIR/services/mem0`。
`runtime.plugin_config.memory` 的必填项只有稳定 `user_id` 和 `agent_id`；endpoint、top_k、threshold、
上下文预算与 5 秒 deadline 有默认值。`preserve_input_language` 默认开启，使 Mem0 在保持
`infer=true` 抽取的同时使用输入语言保存事实。显式 `memory_recall` 省略召回参数时继承同一套配置。
Mem0 服务在专用 Key 为空时复用现有 `OPENAI_API_KEY`，使用
`deepseek-v4-flash` 与本地 FastEmbed Embedding。
OpenKB 服务通过 `icarus start openkb` 启动，数据位于
`$ICARUS_DATA_DIR/services/openkb`。`runtime.plugin_config.knowledge` 只要求配置
`knowledge_base`；Endpoint、上传容量限制有默认值。Knowledge 只提供 query/list/read/upload/recompile，
不使用 OpenKB `/chat`，也不注册删除 Tool。

### MCP Server

在 `settings.json` 顶层添加常见的 `mcpServers` 配置即可启用 MCP。stdio Server 使用
`command`，Streamable HTTP Server 使用 `url`；`enabled` 省略时默认为 `true`。

```json
{
  "mcpServers": {
    "browser": {
      "command": "npx",
      "args": ["-y", "browser-mcp"]
    },
    "blender": {
      "url": "http://127.0.0.1:9876/mcp"
    }
  }
}
```

Server 在 Session 启动时不会被连接。Agent 首次调用 `mcp_tool_list`、`mcp_tool_search` 或
`mcp_tool_execute` 时才按需连接；Server 未运行会作为本次 Tool 失败反馈，不阻止其他 Session
能力启动。Header 和环境变量中的 Secret 应写成 `${ENV_NAME}`，不要直接提交到 settings。

Session 与公共 Conversation 保存在 `ICARUS_DATA_DIR/icarus.db`。Plugin State、Runtime Snapshot、
Trace、日志和 Asset 继续按 Workspace/Session 保存为文件。旧 `conversation.jsonl` 不迁移、不兼容
读取，也不与数据库双写；首次启用该版本需要使用不包含旧 Session 数据的新目录。

## 测试

```bash
./apps/agent/scripts/test.sh
make typecheck-agent
make check-package-agent
```

Gateway 的 `test_runtime_e2e.py` 使用真实 TCP/WebSocket、生产插件图和真实 SQLite，覆盖卸载、重启与继续对话；
外部模型/记忆 HTTP 服务使用本地 fixture。真实远端模型 smoke 必须另行提供凭据和隔离数据，不应把离线 fixture 称作远端验证。

架构设计和实施计划按功能聚合在 `apps/agent/docs/spec/YYYY-MM-DD-<feature>/`。架构文档以当前
源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。
