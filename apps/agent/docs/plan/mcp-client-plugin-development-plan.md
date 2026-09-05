# MCP Client Plugin Development Plan｜MCP 客户端插件实施计划

## 目标

基于 `apps/agent/docs/arch/mcp-client-plugin-design.md`，为 Icarus Agent 增加通用 MCP Client
能力，使 Browser MCP、Blender MCP、Unity MCP 及其他符合 MCP Tools 契约的外部服务只需增加
`mcpServers` 配置即可被现有 ReActAgent 使用。

第一阶段固定向模型暴露：

```text
mcp_tool_list
mcp_tool_search
mcp_tool_execute
```

FastMCP 负责 MCP 协议、传输和连接；Icarus 负责 Plugin 生命周期、Server 配置、Tool Catalog、
文本检索、参数校验、结果转换、Session 隔离和现有观测链路。

## 当前状态

- 已加入 FastMCP 4.0.3、通用 `mcpServers` 配置和内置 MCPPlugin；
- 已实现三个固定 Tool、不可变 Catalog、确定性文本搜索、执行前 Schema 校验和按需连接；
- 已实现 stdio 与 Streamable HTTP，并通过本地真实 Server 冒烟；
- 已实现 MCP 文本、结构化内容和图片结果转换；
- 已把 Tool Result 图片接入现有 Session Asset、ImagePart 和双 Provider 转换；
- 已复用 `AgentToolCompletedEvent`、`TaskErrorEvent` 和 `RuntimeUpdatePlugin` 反馈错误；
- 已保持 `ToolRegistry` 冻结、ReActAgent 无 MCP 依赖和单次 Run 工具快照不变。

## 实施原则

- 使用 `fastmcp-slim[client]==4.0.3`，不自行实现 JSON-RPC、初始化协商或 Transport；
- FastMCP 和 MCP 协议类型只存在于 MCPPlugin 的 Backend 与转换边界；
- MCPPlugin 只注册三个固定 Tool，不动态修改 `ToolRegistry`；
- Server 和 Tool Catalog 可以动态变化，模型工具集合保持稳定；
- Server 只在真实 `list/search/execute` 操作时按需连接，不做主动健康检查；
- Catalog 仅在首次使用、失效通知或重连后刷新，不按 Agent Run 主动刷新；
- `execute.arguments` 与原 MCP Tool 参数结构完全一致；
- 执行前使用 Catalog 保存的 JSON Schema 校验，不信任模型回传 Schema；
- MCP 图片复用现有 Session Asset 和 `ImagePart`，不建立 MCP 专用媒体体系；
- 错误复用现有 Tool Result、Event、Hook 和 RuntimeUpdate，不新增 MCP 错误层级；
- 保持同步与异步 Tool 接口行为一致；
- 每个 SessionRuntime 独立持有 MCPPlugin、Client 和 Catalog；
- 不实现没有首期调用方的 Resources、Prompts、Roots、Sampling、Elicitation 或 MCP Apps。

## 交付顺序

```text
依赖与配置
  ↓
Catalog 与文本检索纯逻辑
  ↓
FastMCP Backend 与 Session 级 Client 生命周期
  ↓
三个固定 MCP Tool 与 Plugin 注册
  ↓
通用多模态 Tool Result
  ↓
集成验证与文档收口
```

连接、Catalog 和 Tool 三层先以文本结果完成闭环；图片结果作为通用 Tool 能力随后接入，避免
协议连接问题与 Provider 消息问题互相干扰。

## 任务一：增加 FastMCP 依赖与通用 MCP 配置

### 更新文件

- `apps/agent/requirements.txt`
- `apps/agent/src/model_config/config_model.py`
- `apps/agent/src/model_config/__init__.py`
- `apps/agent/src/model_config/config_loader.py`
- `apps/agent/settings.json`
- `apps/agent/test/model_config/test_config_loader.py`

### 开发内容

1. 增加并精确锁定依赖：

   ```text
   fastmcp-slim[client]==4.0.3
   jsonschema>=4,<5
   ```

   `jsonschema` 只用于执行前校验目标 MCP Tool 的 `inputSchema`，不自行实现 JSON Schema。
2. 在 `ConfigModel` 增加顶层 `mcp_servers` 字段，并接受 JSON 键名 `mcpServers`。默认值为空字典，
   使旧配置和测试构造器保持兼容。
3. 配置模型只保存 Server 名到原始对象的映射，不把全部 MCP 配置格式复制成 Icarus 类型。
   MCPPlugin 内的配置解析器负责以下最小规则：
   - `enabled` 缺省为 `true`；
   - `enabled: false` 的 Server 被忽略；
   - `command` 与 `url` 必须且只能存在一个；
   - `command` 对应 stdio，`url` 对应 HTTP；
   - 兼容 `type`、`transport`、`args`、`env`、`cwd`、`headers` 等常见字段；
   - Server 名非空，并拒绝会导致 `tool_ref` 无法唯一解析的名称；
   - 未识别的扩展字段保留给 FastMCP，不在 ConfigModel 层丢弃。
4. 在 `settings.json` 增加空的 `mcpServers` 示例，不预置实际外部 Server。
5. `${ENV_NAME}` 只在 MCP Client 构造前展开：
   - 支持 `env` 和 `headers` 字符串值；
   - 环境变量缺失时在实际连接时返回明确错误；
   - 不把展开后的配置写回 ConfigModel 或日志。

### 定向测试

- 旧 settings 不含 `mcpServers` 时得到空配置；
- 常见 stdio 和 HTTP JSON 能直接解析；
- `enabled` 缺省启用，显式 `false` 被过滤；
- `command` 与 `url` 同时存在或同时缺失时失败；
- 常见附加字段被保留；
- 环境变量只在连接准备阶段展开，缺失变量不泄漏其他环境内容。

## 任务二：实现 MCP Tool Catalog 与确定性搜索

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/mcp/models.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/catalog.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/config.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/__init__.py`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_config.py`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_catalog.py`

### 开发内容

1. 定义不依赖 FastMCP 类型的内部不可变模型：
   - `MCPServerConfig`；
   - `MCPServerInfo`；
   - `MCPToolDescriptor`；
   - `MCPToolCatalogSnapshot`；
   - `MCPCallResult` 及首期需要的内容描述。
2. `MCPToolDescriptor` 保存：
   - `tool_ref`；
   - Server 名；
   - MCP 原始 Tool 名；
   - 标题、描述和完整 `input_schema`；
   - annotations 和允许保留的非敏感 metadata。
3. 为 `tool_ref` 定义唯一、稳定且可校验的生成规则。它只用于 Icarus Catalog 查找；调用
   FastMCP 时始终使用 Descriptor 保存的原始 Tool 名。
4. Catalog 使用完整 Snapshot 原子替换，不原地增删 Tool。`generation` 只在实际内容变化时增加；
   相同 `tools/list` 结果复用当前 Snapshot。
5. 实现稳定分页：
   - 先按 Server 名和 Tool 原始名称排序；
   - `page` 从 1 开始；
   - `page_size` 使用代码默认值和硬上限；
   - 空页返回空数组和正确的总数信息。
6. 实现纯文本搜索：
   - 使用 Unicode `casefold()` 和简单分词；
   - 匹配 `tool_ref`、原始 Tool 名、Server 名、标题和描述；
   - 工具名精确匹配高于工具名子串，名称高于标题，标题高于描述；
   - 多关键词按命中数和字段权重排序；
   - 同分时按稳定 Tool 名排序；
   - 不调用模型、Embedding、网络或外部索引。
7. `list/search` 每个返回项都带完整 `input_schema`。分页和结果上限防止一次将整个大 Catalog
   写入模型上下文。

### 定向测试

- Tool 顺序与输入顺序无关；
- `tool_ref` 唯一且能查回原 Server/Tool；
- 相同 Catalog 不增加 generation；
- Tool 增删或 Schema 变化时原子替换并增加 generation；
- 中英文、大小写、精确名称、子串和描述关键词匹配；
- 多结果排序稳定；
- 分页默认值、边界、末页和超限保护；
- Catalog 输出保持 JSON 可序列化且不含 FastMCP 对象。

## 任务三：封装 FastMCP Backend 和 Client 生命周期

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/mcp/backend.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/client_manager.py`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_backend.py`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_client_manager.py`

### 开发内容

1. 定义最小 `MCPClientBackend` Protocol：

   ```python
   async def connect() -> MCPServerInfo
   async def list_tools() -> tuple[MCPToolDescriptor, ...]
   async def call_tool(name, arguments) -> MCPCallResult
   async def close() -> None
   ```

2. `FastMCPClientBackend` 在内部构造 FastMCP 4 Client：
   - stdio 使用 `StdioTransport(command, args, env, cwd)`；
   - HTTP 使用 `StreamableHttpTransport(url, headers, auth)` 或等价公共 API；
   - 使用 FastMCP 默认协议模式完成新旧 Server 协议协商；
   - Tool 调用使用 `call_tool_mcp()` 保留完整协议结果；
   - 不访问 FastMCP 下划线私有属性或 `client.session`；
   - FastMCP 类型在返回前全部转换为 MCPPlugin 内部类型。
3. 使用 FastMCP 的公开 Message Handler 接收 `tools/list_changed`，回调只将对应 Server Catalog
   标记为 stale，不在通知处理期间执行 `tools/list`。
4. `MCPClientManager` 为每个启用 Server 保存独立 Runtime：
   - 原始配置和脱敏后的连接摘要；
   - Backend；
   - 当前 Catalog Snapshot；
   - stale 标记；
   - 单个异步锁或共享连接 Future；
   - 最近一次真实操作观察到的错误。
5. Session 启动时只建立 Server Runtime，不创建外部连接。第一次 `list/search/execute` 才连接。
6. 同一 Server 的并发首次请求共享一次连接和 `tools/list`；不同 Server 可以并发加载，单个
   Server 失败不阻塞其他 Server。
7. Catalog 存在且未 stale 时直接复用。stale 时由下一次真实操作刷新；并发刷新只允许一个
   `tools/list` 在途。
8. 不实现主动 Ping 或后台健康任务。连接关闭、协议异常或传输异常发生后：
   - 本次操作返回实际异常；
   - 丢弃不能继续使用的 Backend；
   - 不自动重放已经发起的 Tool Call；
   - 下一次操作重新建立 Backend 并刷新 Catalog。
9. 为满足 Icarus 同步与异步 Tool 契约，增加 MCPPlugin 内部受控执行桥接，使 FastMCP Client
   始终在其所属事件循环上使用：
   - 异步 `ainvoke` 不阻塞调用方事件循环；
   - 同步 `invoke` 可等待同一套 Manager 操作；
   - 不在活动事件循环中调用 `asyncio.run()`；
   - 桥接对象由 MCPPlugin 创建和关闭，不成为新的 Plugin。
10. `close()` 幂等关闭所有 Backend。Runtime 停止后拒绝新调用，并确保 stdio 子进程和 HTTP
    Client 被 FastMCP 释放。

### 定向测试

- 构造 stdio 与 Streamable HTTP Transport 时参数正确；
- FastMCP 初始化结果转换为内部 ServerInfo；
- FastMCP Tool 和 CallToolResult 不泄漏出 Backend；
- SessionRuntime/Plugin 启动不连接 Server；
- 首次操作才连接并加载 Catalog；
- 同 Server 并发首次访问只连接和列举一次；
- 不同 Server 并发时部分成功、部分失败；
- `tools/list_changed` 只标记 stale；
- stale Catalog 在下一次访问时只刷新一次；
- 调用异常后不自动重放，下一次操作重新连接；
- 同步和异步入口得到一致结果；
- 重复关闭安全，停止后没有遗留 Client 或 stdio 进程。

## 任务四：实现三个固定 Tool 并注册 MCPPlugin

### 新增文件

- `apps/agent/src/agent_orchestration/plugins/mcp/tools.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/factory.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/manifest.json`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_tools.py`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_plugin.py`

### 更新文件

- `apps/agent/src/application/session_runtime.py`
- `apps/agent/src/model_config/config_model.py`
- `apps/agent/settings.json`
- `apps/agent/test/application/test_session_runtime.py`
- 必要时更新 Plugin Runtime 的内置 Manifest 集合测试

### 开发内容

1. `MCPPlugin` 持有 Client Manager，并在 `stop()` 中关闭；`consume()` 不解释现有业务 Event。
2. Factory 从 Session 配置获得 `mcpServers`，并使用 Persistence Runtime 与 Session identity 构造
   当前 `PersistenceSession`，供后续图片结果落盘。
3. Manifest 固定声明：
   - `mcp_tool_list`；
   - `mcp_tool_search`；
   - `mcp_tool_execute`；
   - Persistence `runtime`、`session` 和 `redactor` Capability；
   - `python_requires` 中的 FastMCP 与 JSON Schema 依赖；
   - 无状态范围和新增 Event。
4. 将 `mcp` 加入默认 `required_plugin_ids`，三个入口保持稳定。没有启用的 Server 时 `list/search`
   返回空目录，`execute` 返回目标 Server 未配置；MCPPlugin 依赖缺失或 Factory 自身无效会阻止
   Session Ready。某个配置的外部 Server 连接失败不影响 Session Ready。
5. `SessionRuntime` 将顶层 `mcpServers` 注入 MCPPlugin Factory 配置，不让 MCPPlugin读取全局
   ConfigModel。
6. 实现 `mcp_tool_list`：
   - 无必填参数；
   - `server`、`page`、`page_size` 可选；
   - 未指定 Server 时并发加载所有启用 Server；
   - 返回稳定分页、完整 Schema 和各 Server 的实际失败；
   - 只读操作允许并行执行。
7. 实现 `mcp_tool_search`：
   - `query` 是唯一必填参数；
   - `server`、`limit` 可选；
   - 从已加载或按需加载的 Catalog 执行本地文本搜索；
   - 返回多个候选及其完整 Schema；
   - 只读操作允许并行执行。
8. 实现 `mcp_tool_execute`：
   - `tool_ref` 必填；
   - `arguments` 可选并默认为 `{}`；
   - 从当前 Catalog 解析原 Server 和原 Tool 名；
   - Catalog stale 时在请求发送前刷新；
   - 使用 `jsonschema` 校验 `arguments`；
   - 参数校验通过后原样传给 Backend；
   - 不让模型传回 Schema、Transport 或 Catalog 版本；
   - 默认按串行工具处理，不根据不可信 MCP annotation 自动声明可并行；
   - 不自动重放失败调用。
9. 三个 Tool 捕获预期配置、连接、Schema 和 MCP 错误，返回现有
   `ToolExecutionResult(success=False, error=...)`；错误文本保留真实异常类型和有效信息，同时通过
   Persistence Redactor 去除 Secret。
10. 不新增 MCP 专用 Agent Response 或 Error Event。现有 ReActAgent、AgentPlugin、
    `AgentToolCompletedEvent`、`TaskErrorEvent`、RuntimeUpdatePlugin 和 TUI Tool 状态投影继续工作。

### 定向测试

- Manifest 与 Factory 返回的三个工具完全一致；
- 无 Server 配置时三个工具仍安全返回空目录或明确失败；
- disabled Server 不出现在 list/search，也不能 execute；
- list/search 返回的完整 Schema 可直接指导 execute 参数；
- search 返回多个候选时不自动选择；
- execute 只接受 Catalog 生成的有效 `tool_ref`；
- 无参数 Tool 缺省为 `{}`；
- 参数无效时 Backend 未被调用；
- 参数有效时键、值和嵌套结构原样传给 Backend；
- Server 业务错误、协议错误和连接错误均沿现有 Tool 失败链路到达 Agent；
- Tool started/completed 与现有 RuntimeUpdate 顺序和结构不回归。

## 任务五：接入通用多模态 Tool Result

### 更新文件

- `apps/agent/src/agent_orchestration/tools/types.py`
- `apps/agent/src/agent_orchestration/capability/react_agent.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/runtime.py`
- `apps/agent/src/model_provider/impl/openai_llm.py`
- `apps/agent/src/model_provider/impl/anthropic_llm.py`
- `apps/agent/src/agent_orchestration/plugins/mcp/result_converter.py`
- `apps/agent/test/agent_orchestration/tools/test_tools.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent.py`
- `apps/agent/test/agent_orchestration/capability/test_react_agent_stream.py`
- `apps/agent/test/agent_orchestration/plugins/persistence/test_images.py`
- `apps/agent/test/model_provider/impl/test_openai_llm.py`
- `apps/agent/test/model_provider/impl/test_anthropic_llm.py`
- `apps/agent/test/agent_orchestration/plugins/mcp/test_result_converter.py`

### 开发内容

1. 为 `ToolExecutionResult` 增加：

   ```python
   images: tuple[ImagePart, ...] = ()
   ```

   保持现有位置参数、`success/output/error` 和无图片调用兼容。`as_dict()` 只序列化图片的 Asset
   引用和 MIME，不包含二进制或 Base64。
2. 为 `PersistenceSession` 增加 `import_image_bytes()`：
   - 对解码后的字节设置固定大小上限；
   - 根据实际文件签名识别 PNG/JPEG/GIF/WebP；
   - Server 声明的 MIME 只用于一致性检查，不作为唯一信任来源；
   - 复用现有 SHA-256 文件名、去重、原子写入和 `0600` 权限；
   - 将现有 `import_image(path)` 改为读取后委托该方法。
3. `MCPResultConverter` 将 FastMCP Backend 的内部结果转换为 ToolExecutionResult：
   - TextContent 保留为文本；
   - `structuredContent` 保留为 JSON 兼容结构；
   - ImageContent 解码并保存为 Session Asset；
   - 图片 EmbeddedResource 走相同 Asset 入口；
   - 文本 EmbeddedResource 和 ResourceLink 保存为结构化结果；
   - AudioContent 与未知类型明确说明未支持，不静默丢弃；
   - MCP `isError` 形成失败 ToolExecutionResult；
   - `_meta` 只保留经过脱敏且允许进入模型的内容；
   - 同一信息同时存在于 content 与 structuredContent 时避免无意义重复。
4. ReActAgent 将 ToolExecutionResult 转为语义正确的内部消息：

   ```text
   Message(role="tool", tool_call_id=..., content=[TextPart, ImagePart...])
   ```

   Tool Result 的文本部分继续包含 `success/output/error`，图片仍然关联原 `tool_call_id`。
5. Anthropic Adapter 将 Tool Message 中的 `TextPart` 和 `ImagePart` 转换为同一个
   `tool_result.content` 多内容数组。
6. OpenAI Adapter 保持每个 Tool Call 都有合法的文本 Tool Message。对于连续的一批 Tool Result：
   - 先输出全部带 `tool_call_id` 的文本 Tool Message；
   - 再在 Provider 请求转换阶段追加一条带来源说明和图片的多模态 User Message；
   - 不把兼容消息写回 Icarus Message、Blackboard 或 Session State；
   - 多个并行 Tool Result 的图片按 Tool Result 顺序聚合，不能插入两个 Tool Result 中间。
7. 不修改 MCPPlugin 或 ReActAgent 来判断具体模型厂商；协议差异只留在 Provider Adapter。
8. 现有 Blackboard Message 序列化已经支持 `ImagePart`，补测试确认 Tool Message 图片能够持久化
   和恢复，不增加新的 Session State 版本。

### 定向测试

- `ToolExecutionResult` 无图片时保持原有 `as_dict()` 和相等行为；
- 字节导入与路径导入得到相同 Asset 命名和 MIME；
- 无效 Base64、超限图片、声明 MIME 与签名不一致时明确失败；
- MCP 文本、结构化、图片、EmbeddedResource 和 ResourceLink 转换；
- Base64 和原始 bytes 不进入 Tool 文本、Hook Trace 或 RuntimeUpdate；
- ReAct 四种入口都将图片保留在对应 Tool Message；
- 多个并行 Tool Call 的结果顺序和图片归属稳定；
- Anthropic 生成合法的多模态 `tool_result`；
- OpenAI 先完成整批 Tool Result 配对，再追加临时多模态承载消息；
- Blackboard Tool Message 图片状态可 round trip；
- 用户输入图片的现有行为不回归。

## 任务六：生命周期、故障隔离和整体集成验证

### 更新文件

- `apps/agent/test/agent_orchestration/plugins/mcp/test_integration.py`
- `apps/agent/test/agent_orchestration/plugin_runtime/test_host.py`
- `apps/agent/test/application/test_session_runtime.py`
- `apps/agent/test/application/test_agent_runtime.py`
- `apps/agent/test/agent_orchestration/plugins/runtime_update/test_plugin.py`
- `apps/agent/README.md`
- `apps/agent/docs/arch/mcp-client-plugin-design.md`

### 开发内容

1. 使用 Fake Backend 完成不依赖外部程序的端到端测试：

   ```text
   SessionRuntime.start
   → MCPPlugin 被发现并注册三个 Tool
   → Agent 调用 search/list
   → Agent 按返回 Schema 调用 execute
   → Backend 收到原 Tool 名和原始 arguments
   → Tool Result 回填 Agent
   → SessionRuntime.stop 关闭 Backend
   ```

2. 验证一个外部 Server 失败时：
   - SessionRuntime 保持可用；
   - 其他 Server 的 list/search 结果仍然返回；
   - 失败通过 `ToolExecutionResult` 回填 Agent；
   - `AgentToolCompletedEvent` 和现有 `tool.completed` RuntimeUpdate 正常产生；
   - AgentPlugin 继续按现有规则发布并过滤重复的 nonfatal `tool_execution_failed`。
3. 验证取消和停止：
   - Agent Task 取消传播到等待中的 MCP Tool；
   - 取消不触发自动重放；
   - Plugin `quiesce` 后拒绝新 MCP 操作；
   - Plugin `drain/stop` 不留下连接或子进程；
   - 一个 Backend 关闭失败不阻止其他 Backend 清理，最终错误按现有 Runtime 收束规则汇总。
4. 验证不同 Session：
   - 使用独立 Client、Catalog、stale 标记和连接错误；
   - 一个 Session 的 Server 失败、刷新或关闭不影响另一个 Session；
   - Workspace 缺省 `cwd` 解析到各自 Session 的 Workspace。
5. 在 README 增加最小配置和调用说明，说明：
   - `mcpServers` 可复制常见 MCP 配置；
   - `enabled` 缺省为 true；
   - Server 不在线不影响 Session 启动；
   - 模型通过 list/search 获得 Schema，再通过 execute 调用。
6. 实现完成后更新架构文档的“当前实现状态”，只记录已经通过测试的能力，不把后续扩展写成
   已实现。

### 可选真实 Smoke Test

在本地环境具备对应程序时执行，不纳入默认单元测试：

1. 一个最小 stdio MCP Server：验证发现、文本 Tool 调用和进程退出；
2. 一个 Streamable HTTP MCP Server：验证连接、发现和 Tool 调用；
3. Browser 或 Blender MCP：验证截图进入 Session Asset，并能被当前视觉模型读取；
4. 配置一个未启动的 Unity/Blender Server：验证 Session 正常启动、调用时失败并反馈。

真实 Smoke Test 不输出 Header、Token、环境变量值或图片 Base64。

## 实施验证顺序

每个任务先运行最小测试：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/model_config/test_config_loader.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/mcp/test_config.py \
  apps/agent/test/agent_orchestration/plugins/mcp/test_catalog.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/mcp/test_backend.py \
  apps/agent/test/agent_orchestration/plugins/mcp/test_client_manager.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/mcp/test_tools.py \
  apps/agent/test/agent_orchestration/plugins/mcp/test_plugin.py \
  apps/agent/test/agent_orchestration/plugins/mcp/test_integration.py -q

apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/capability \
  apps/agent/test/agent_orchestration/plugins/persistence/test_images.py \
  apps/agent/test/model_provider/impl -q
```

最终执行仓库约定验证：

```bash
make test-agent
make test-gateway
make test-tui
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test packages
git diff --check
```

若依赖尚未安装，先执行：

```bash
make install-dev
```

## 完成标准

- 用户能把常见 `mcpServers` JSON 复制到 settings，并通过 `enabled` 控制 Server；
- SessionRuntime 启动时不连接任何 MCP Server；
- stdio 和 Streamable HTTP 都通过 FastMCP 4.0.3 建立连接；
- 多个 Session 不共享 Client、Catalog 或失败状态；
- 模型始终只看到 `mcp_tool_list/search/execute` 三个固定工具；
- list/search 返回有限数量候选及完整原始 `inputSchema`；
- search 是确定性文本匹配，不使用语义检索；
- execute 使用 Catalog Schema 校验，并将 arguments 原样传给目标 MCP Tool；
- Tool Catalog 未变化时复用同一 Snapshot，收到变化通知后只在下次真实使用时刷新；
- 不存在主动健康检查或失败调用自动重放；
- 外部 Server 未运行或调用失败时，Agent 获得现有 Tool 失败结果，外部 UI 获得现有
  RuntimeUpdate；
- Browser/Blender 等 MCP 返回的图片进入现有 Session Asset 和 ImagePart 链路，模型能实际读取；
- Secret、原始 bytes 和 Base64 不进入日志、Trace、Conversation Update；
- Resources、Prompts 和反向 MCP 能力仍未实现，但 Backend 和 Catalog 边界允许后续平行扩展；
- Agent、Gateway、TUI 全量测试、compileall 和 `git diff --check` 全部通过。

## 实施结果

- 已完成 FastMCP 4.0.3 Client 依赖、通用 `mcpServers` 配置与三个固定工具；
- 已完成 Session 级按需连接、Catalog Snapshot、文本搜索和执行前 Schema 校验；
- 已完成 Tool 返回图片的 Session Asset、ImagePart 和 Provider 转换；
- 已完成错误、MCP 文本、metadata、URI 和 RuntimeUpdate 参数的敏感信息脱敏；
- 已通过本地 stdio 与 Streamable HTTP Server 的真实发现和调用冒烟；
- Agent 全量测试 440 项通过；
- Gateway 全量测试 11 项通过；
- TUI 全量测试 163 项通过，其中 12 个视觉快照通过；
- `compileall`、`pip check` 与 `git diff --check` 通过。
