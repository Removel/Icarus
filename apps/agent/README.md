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
- Blackboard 多 Region 当前状态、只读 Region Tool 与 Product Conversation 投影；
- 通过 MemoryPlugin 接入自建 Mem0：每轮 1 秒内自动召回，并提供 8 个显式记忆 Tool。
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

依赖安装在 `apps/agent/.venv`。

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
Mem0 服务通过 `icarus start mem0` 启动，数据位于 `$ICARUS_DATA_DIR/services/mem0`。
`runtime.plugin_config.memory` 的必填项只有稳定 `user_id` 和 `agent_id`；endpoint、top_k、threshold、
上下文预算与 1 秒 deadline 有默认值。`preserve_input_language` 默认开启，使 Mem0 在保持
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
```

架构设计和实施计划按功能聚合在 `apps/agent/docs/spec/YYYY-MM-DD-<feature>/`。架构文档以当前
源代码和测试为事实依据，用于描述架构与系统设计，不反向限制源代码演进。
