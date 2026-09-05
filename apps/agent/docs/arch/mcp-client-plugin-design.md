# MCP Client Plugin Design｜MCP 客户端插件设计

## 文档定位

本文定义 Icarus 作为 MCP Host 接入外部 MCP Server 的第一阶段架构。目标是让现有 Agent
能够通过统一方式使用 Browser MCP、Blender MCP、Unity MCP 及其他符合 MCP 规范的服务，而不在
Icarus 内实现具体 MCP Server，也不把 MCP 协议细节泄漏到 Agent Kernel、模型接入层或其他业务
Plugin。

本文描述架构、配置、工具契约、生命周期、多模态结果和验收边界，不包含具体实施步骤。

相关文档：

- Plugin Runtime：`apps/agent/docs/arch/plugin-runtime-manifest-lifecycle-design.md`；
- Agent 编排基础：`apps/agent/docs/arch/agent-orchestration-foundation-design.md`；
- Agent 基础能力：`apps/agent/docs/arch/agent-core-capability-completion-design.md`；
- 文件持久化：`apps/agent/docs/arch/file-persistence-observability-design.md`。

## 1. 核心结论

Icarus 使用 `fastmcp-slim[client]==4.0.3` 提供 MCP Client 的协议、传输和连接基础能力。
Icarus 自身只负责：

- 从 `settings.json` 读取 MCP Server 声明；
- 按 Session 管理多个独立 MCP Client；
- 维护 Server Tool Catalog；
- 向模型提供固定的工具发现与执行入口；
- 执行前根据目标 Tool 的 JSON Schema 校验参数；
- 将 MCP 结果转换成 Icarus 的 Tool Result 和多模态消息；
- 复用现有 Hook、Event 和 RuntimeUpdate 机制完成观测与外部反馈。

模型始终只看到三个固定 Icarus Tool：

```text
mcp_tool_list
mcp_tool_search
mcp_tool_execute
```

完整 MCP Tool 集合只保存在 MCPPlugin 的 Catalog 中。`list/search` 将候选 Tool 的名称、
描述和完整 `inputSchema` 返回给模型；模型选择目标 Tool，按照该 Schema 生成原始参数，再通过
`mcp_tool_execute` 调用。

第一阶段不动态修改 Icarus `ToolRegistry`，不在模型 Tool Set 中逐个注册 MCP Tool，也不修改
ReActAgent 的工具快照机制。

## 2. 目标与非目标

### 2.1 目标

- 支持 stdio 和 Streamable HTTP MCP Server；
- 支持任意符合 MCP Tools 契约的 Browser、Blender、Unity 等外部 Server；
- Server 配置尽量兼容行业常见 `mcpServers` JSON，可直接复制后少量修改；
- Server 未启动或不可访问时不阻塞 SessionRuntime 启动；
- 首次实际使用时按需连接并发现 Tool；
- Catalog 未变化时复用同一不可变 Snapshot；
- Tool 数量无论是十个还是数百个，模型侧都只持有三个固定入口；
- `mcp_tool_execute.arguments` 与原 MCP Tool 参数保持一致；
- 执行前使用 Catalog 中可信的 `inputSchema` 校验参数；
- 支持文本、结构化数据和图片 Tool Result；
- MCP 调用错误通过现有工具结果、Agent Event 和 RuntimeUpdate 链路反馈。

### 2.2 非目标

第一阶段不实现：

- Icarus 内部 MCP Server；
- 自行实现 JSON-RPC、MCP 初始化、版本协商或传输协议；
- 将每个 MCP Tool 动态注册为 Icarus `BaseTool`；
- 解除或绕过 `ToolRegistry.freeze()`；
- 基于 Embedding、LLM 或向量数据库的语义 Tool 搜索；
- 定时 Ping、周期性健康检查或后台轮询 `tools/list`；
- 自动重放失败的 MCP Tool 调用；
- Resources 和 Prompts 的模型入口；
- Roots、Sampling、Elicitation、MCP Apps、Background Tasks 或自定义 Extensions；
- MCP Server 跨 Session 共享连接池；
- 自动监听 `settings.json` 并热重建 SessionRuntime。

上述 MCP 能力保留清晰的扩展边界，但没有实际调用方前不增加空壳实现。

## 3. 架构边界

### 3.1 总体结构

```text
ReActAgent
    │
    │ Icarus BaseTool
    ▼
┌───────────────────────────────────────────┐
│ MCPPlugin                                 │
│                                           │
│  mcp_tool_list                            │
│  mcp_tool_search                          │
│  mcp_tool_execute                         │
│          │                                │
│          ▼                                │
│  MCPToolCatalog                           │
│          │                                │
│          ▼                                │
│  MCPClientManager                         │
│    ├─ browser: FastMCP Client             │
│    ├─ blender: FastMCP Client             │
│    └─ unity:   FastMCP Client             │
│          │                                │
│          ▼                                │
│  MCPResultConverter                       │
└───────────────────────────────────────────┘
              │
       stdio / Streamable HTTP
              │
              ▼
     外部 MCP Server
```

### 3.2 分层职责

| 组件 | 职责 | 明确不负责 |
|---|---|---|
| FastMCP | MCP 协议、传输、连接与协议对象 | Icarus Plugin、Tool、权限和 UI 语义 |
| MCPPlugin | 生命周期、Catalog、调用路由和结果转换 | 模型推理和 Provider 协议 |
| MCPClientBackend | 隔离 FastMCP API，提供 Icarus 内部最小接口 | Tool 暴露策略 |
| MCPClientManager | 按 Server 管理 Client、连接和并发首次连接 | 语义检索和 Agent 决策 |
| MCPToolCatalog | 保存全部已发现 Tool 的不可变快照 | 主动健康检查 |
| 三个 MCP BaseTool | 向模型提供固定发现和执行入口 | 动态注册真实 MCP Tool |
| PersistenceSession | 保存 MCP 返回的图片 Asset | 理解 MCP 内容语义 |
| Provider Adapter | 将统一 Message/ImagePart 转为厂商格式 | 连接 MCP Server |
| Event/RuntimeUpdate | 复用现有工具和任务状态对外反馈 | 改变 MCP 主流程 |

MCPPlugin 只有一个注册到 Plugin Runtime 的 Plugin。Client Manager、Catalog、Backend、
Result Converter 和三个 Tool 都是普通组件，不注册成嵌套 Plugin。

### 3.3 对现有架构的影响

- `ReActAgent` 保持无状态且不感知 MCP；
- `model_provider` 不出现 Browser、Blender、Unity 或 FastMCP 分支；
- `ToolRegistry` 继续在 Runtime Ready 前冻结；
- MCPPlugin 在 Manifest 中只声明三个固定工具；
- 动态变化局限在 MCPPlugin 内部的 Server Runtime 和 Catalog；
- Agent Run 继续使用现有不可变 Tool Executor Snapshot。

## 4. FastMCP 依赖边界

### 4.1 依赖

第一阶段精确锁定：

```text
fastmcp-slim[client]==4.0.3
```

Icarus 只作为 MCP Client，不安装 FastMCP 完整 Server 开发依赖。`fastmcp-slim` 与完整包使用
相同导入命名空间：

```python
from fastmcp import Client
```

初次实现使用精确版本，避免开发过程中被小版本变化干扰。完成真实 Server 兼容验证后，再决定
是否放宽为 `>=4.0.3,<5`。

### 4.2 使用范围

FastMCP 的直接使用只允许出现在 MCPPlugin 的 Backend 实现中。核心调用面为：

```text
Client(...)
async with client
client.list_tools()
client.call_tool_mcp(...)
client.close()
```

使用 `call_tool_mcp()` 保留原始 `content`、`structuredContent`、`isError` 和 `_meta`，由
Icarus 自行完成结果转换。FastMCP Client、Transport、异常和 MCP 协议类型不得进入
ReActAgent、ToolRegistry、model_provider 或其他 Plugin 的公共接口。

### 4.3 内部 Backend

Icarus 只定义一个薄边界，不重复实现协议：

```python
class MCPClientBackend(Protocol):
    async def connect(self) -> MCPServerInfo: ...
    async def list_tools(self) -> tuple[MCPToolDescriptor, ...]: ...
    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, object],
    ) -> MCPCallResult: ...
    async def close(self) -> None: ...
```

未来增加 Resources 或 Prompts 时，在同一边界平行增加方法；不把三类能力提前合并成一个含义
模糊的通用 `call(method, params)`。

## 5. 配置设计

### 5.1 通用配置格式

`settings.json` 顶层增加行业常见的 `mcpServers`：

```json
{
  "mcpServers": {
    "browser": {
      "command": "npx",
      "args": ["-y", "@example/browser-mcp"]
    },
    "blender": {
      "url": "http://127.0.0.1:9876/mcp"
    },
    "unity": {
      "enabled": false,
      "command": "node",
      "args": ["/path/to/unity-mcp/server.js"],
      "env": {
        "UNITY_PROJECT_PATH": "${UNITY_PROJECT_PATH}"
      }
    }
  }
}
```

配置目标是允许用户直接复制 Server 提供方给出的常见 JSON。Icarus 仅增加可选的
`enabled`；省略时视为启用。

### 5.2 核心字段

| 连接方式 | 必需字段 | 可选字段 |
|---|---|---|
| stdio | `command` | `args`、`env`、`cwd`、`enabled`、`type` |
| Streamable HTTP | `url` | `headers`、`enabled`、`type`、`transport` |

规则：

- 有 `command` 时按 stdio 处理；
- 有 `url` 时按 Streamable HTTP 处理；
- 两者同时存在或同时缺失时配置无效；
- `cwd` 缺省时使用当前 Workspace；
- `type` 或 `transport` 只用于兼容常见配置，不要求用户填写；
- `${ENV_NAME}` 在构造 FastMCP Client 前解析；
- 不在日志、Trace、Event 或 RuntimeUpdate 中记录真实 Secret；
- Icarus 去除 `enabled` 后，把兼容的 Server 配置交给 FastMCP 解析。

### 5.3 不暴露的策略参数

第一阶段不让用户配置连接超时、执行超时、重连次数、退避、Catalog TTL、心跳、分页上限或
启动模式。这些使用 Icarus 内部统一默认值。只有出现明确业务需求时才增加设置。

`enabled: true` 只表示用户允许 Icarus 使用该 Server，不表示 Server 当前在线或配置已经经过
连接验证。

## 6. Session 与连接生命周期

### 6.1 所有权

每个 `SessionRuntime` 独立拥有一套 MCPPlugin 和 FastMCP Client：

```text
AgentRuntime
├─ SessionRuntime A
│   └─ MCPPlugin → browser/blender Clients
└─ SessionRuntime B
    └─ MCPPlugin → browser/blender Clients
```

第一阶段不跨 Session 共享 Client 或 stdio 子进程。该方案资源占用略高，但保持 Workspace、
凭据、取消、停止和故障隔离清晰。只有真实使用证明开销不可接受时，才设计设备级连接池。

### 6.2 按需连接

SessionRuntime 启动时只读取和校验配置，不连接 Server。首次 `list/search/execute` 使用某个
Server 时：

```text
查找启用配置
→ 创建 FastMCP Client
→ 建立连接和完成协议协商
→ tools/list
→ 生成 Catalog Snapshot
→ 执行当前操作
```

同一个 Server 的并发首次请求共享同一个连接任务，避免启动多个 stdio 子进程。

### 6.3 无主动健康检查

不定时 Ping，不周期调用 `tools/list`。连接状态只由真实操作观察：

- 首次发现；
- `list/search/execute`；
- FastMCP 连接关闭；
- `notifications/tools/list_changed`；
- SessionRuntime 停止。

Server 在 settings 中启用但实际未运行时，SessionRuntime 仍正常启动；Agent 实际使用时收到
本次工具调用失败。

### 6.4 被动恢复

- 连接或调用失败后，关闭并丢弃不可继续使用的 Client；
- 当前调用直接失败，不自动重放；
- 下一次真实操作按需建立新 Client；
- 重连成功后重新执行 `tools/list` 并替换 Catalog；
- 请求是否已经产生外部副作用无法确认时，同样不自动重放。

不自动重放是为了避免重复点击、重复提交、重复创建对象或重复修改场景。

### 6.5 停止

MCPPlugin 停止时：

- 拒绝新操作；
- 收束正在进行的请求；
- 关闭所有 FastMCP Client；
- 由 FastMCP 释放 HTTP 资源和 stdio 子进程。

## 7. Tool Catalog

### 7.1 不可变 Snapshot

每个 Server 保存当前 Catalog Snapshot：

```python
@dataclass(frozen=True)
class MCPToolCatalogSnapshot:
    generation: int
    tools: tuple[MCPToolDescriptor, ...]
    by_ref: Mapping[str, MCPToolDescriptor]
```

`MCPToolDescriptor` 至少保存：

- Icarus 生成并返回给模型的稳定 `tool_ref`；
- Server 名；
- MCP 原始 Tool 名；
- 标题和描述；
- 完整 `inputSchema`；
- MCP Tool annotations；
- 未来扩展需要的非敏感 metadata。

`tool_ref` 是由 Icarus 生成的不透明唯一引用，模型必须原样使用 `list/search` 返回的值，不能
自行拼接。MCP 原始 Tool 名始终单独保存，并在调用 FastMCP 时使用。

### 7.2 懒更新

Catalog 不是每轮主动更新。只有以下情况生成新 Snapshot：

- 首次发现；
- Server 发出 `tools/list_changed` 后，下一次实际使用时；
- 连接失败并在后续操作中重新建立；
- 新 SessionRuntime 读取到变更后的 settings。

收到 `tools/list_changed` 时只标记对应 Catalog 失效，不在通知回调里同步请求 Server。下一次
`list/search/execute` 触发刷新，并原子替换完整 Snapshot。

由于模型侧只有三个固定 Tool，Catalog 更新不影响当前 Agent Tool Set、ToolRegistry 冻结或
Prompt Cache。

## 8. 模型工具契约

### 8.1 `mcp_tool_list`

用于分页浏览 MCP Tool。无必填参数：

```json
{
  "server": "blender",
  "page": 1,
  "page_size": 20
}
```

- `server` 可选；省略时查询所有启用 Server；
- `page` 可选，默认 `1`；
- `page_size` 可选，默认和最大值由代码固定；
- 返回结果按 Server 和 Tool 名稳定排序；
- 返回当前页每个 Tool 的 `tool_ref`、Server、原始名称、描述和完整 `inputSchema`；
- Server 级失败不阻止其他 Server 返回结果，结果中包含经过脱敏的失败信息。

### 8.2 `mcp_tool_search`

只做本地确定性文本匹配：

```json
{
  "query": "create blender objects",
  "server": "blender",
  "limit": 5
}
```

- `query` 必填；
- `server` 可选；
- `limit` 可选并受代码固定上限约束；
- 搜索 Tool 原始名称、Server 名、标题和描述；
- 名称精确匹配优先于名称子串，名称匹配优先于描述匹配；
- 同分结果按稳定名称排序；
- 不使用 Embedding、LLM、向量索引或额外网络搜索；
- 每个匹配项返回完整 `inputSchema`。

搜索返回多个候选是正常结果。Icarus 不代替模型选择，模型根据当前任务、描述和 Schema 决定
调用哪个 Tool。

### 8.3 `mcp_tool_execute`

模型将 `list/search` 返回的 `tool_ref` 原样传入，并按照目标 Tool 的 Schema 生成参数：

```json
{
  "tool_ref": "blender/create_objects",
  "arguments": {
    "object_type": "cube",
    "location": [1, 2, 0],
    "count": 3
  }
}
```

- `tool_ref` 必填；
- `arguments` 对无参数 Tool 可省略并默认为 `{}`；
- `arguments` 的内部结构与原 MCP Tool 参数完全一致；
- 模型不回传 Schema、Transport、Catalog generation 或 Server 连接信息。

执行流程：

```text
tool_ref 查询当前 Catalog
→ 获得 Server、原 Tool 名和 inputSchema
→ 使用 Catalog 中的 Schema 校验 arguments
→ arguments 原样传给 FastMCP call_tool_mcp
→ 将 MCP Result 转换成 ToolExecutionResult
```

Schema 由 Icarus Catalog 保存并作为校验依据，不信任模型回传的 Schema。Server 仍拥有最终业务
校验权。

### 8.4 完整示例

用户请求：

```text
在 Blender 坐标 [1, 2, 0] 创建三个立方体。
```

模型搜索：

```json
{
  "name": "mcp_tool_search",
  "arguments": {
    "query": "create blender objects",
    "server": "blender"
  }
}
```

搜索结果：

```json
{
  "matches": [
    {
      "tool_ref": "blender/create_objects",
      "server": "blender",
      "name": "create_objects",
      "description": "Create multiple objects in the Blender scene",
      "input_schema": {
        "type": "object",
        "properties": {
          "object_type": {"type": "string"},
          "location": {
            "type": "array",
            "items": {"type": "number"}
          },
          "count": {"type": "integer"}
        },
        "required": ["object_type", "location", "count"]
      }
    }
  ]
}
```

模型执行：

```json
{
  "name": "mcp_tool_execute",
  "arguments": {
    "tool_ref": "blender/create_objects",
    "arguments": {
      "object_type": "cube",
      "location": [1, 2, 0],
      "count": 3
    }
  }
}
```

Icarus 校验后调用：

```python
await client.call_tool_mcp(
    name="create_objects",
    arguments={
        "object_type": "cube",
        "location": [1, 2, 0],
        "count": 3,
    },
)
```

## 9. Tool Result 与多模态

### 9.1 复用现有图片能力

不建立 MCP 专用图片体系。复用现有：

```text
Session assets
→ ImagePart
→ Provider Adapter
→ 模型多模态输入
```

`PersistenceSession` 增加通用字节入口：

```python
import_image_bytes(data: bytes, media_type: str | None = None) -> ImagePart
```

现有 `import_image(path)` 和 MCP `ImageContent` 都复用该入口完成格式检测、SHA-256 去重、
原子落盘和安全相对引用生成。Base64 不进入 Trace、RuntimeUpdate 或对话文本。

### 9.2 通用 Tool Result

在不引入完整 Artifact 子系统的前提下，为现有结果增加图片：

```python
@dataclass(frozen=True)
class ToolExecutionResult:
    success: bool
    output: Any | None = None
    error: str | None = None
    images: tuple[ImagePart, ...] = ()
```

MCP 结果转换规则：

| MCP 内容 | Icarus 表达 |
|---|---|
| TextContent | `output` 中的文本 |
| structuredContent | `output` 中的结构化 JSON |
| ImageContent | Session Asset + `images` |
| 图片 EmbeddedResource | Session Asset + `images` |
| 文本 EmbeddedResource | 带 URI/MIME 的结构化文本 |
| ResourceLink | 名称、URI、MIME 等结构化元数据 |
| AudioContent | 返回明确的暂不支持说明，不静默丢弃 |
| 未识别内容 | 返回内容类型说明，不静默丢弃 |

Icarus 内部使用语义正确的 Tool Message：

```text
Message(role="tool", tool_call_id=..., content=[TextPart, ImagePart...])
```

Provider Adapter 负责协议差异。支持 Tool Result 图片的协议直接转换；不支持时，在请求转换阶段
先完整发送同一批 Tool Result 文本，再生成一条仅存在于厂商请求中的多模态承载消息。该兼容消息
不进入 Icarus 对话历史，MCPPlugin 和 ReActAgent 不感知厂商差异。

## 10. 错误与外部反馈

第一阶段不建立 MCP 专用错误分类树。实际出现什么错误，就在必要脱敏后保留其具体类型和信息。

`mcp_tool_list/search/execute` 的失败统一返回：

```python
ToolExecutionResult(
    success=False,
    error=sanitized_error_message,
)
```

“返回给 Agent”仅指现有 ReAct 流程把该 `ToolExecutionResult` 写成 Tool Result Message，使模型
能够解释失败、修正参数、改用其他工具或向用户说明问题；不新增 MCP 专用 Agent Response。

对外反馈复用现有链路：

```text
ToolExecutionResult
→ AgentToolCompletedEvent
→ 必要时现有 TaskErrorEvent
→ RuntimeUpdatePlugin
→ Gateway / TUI / WebUI
```

不新增 MCP 状态 Event 或并行通知体系。现有 `tool.started` 已携带 `mcp_tool_execute` 的参数，
外部系统可以通过 `call_id` 与 `tool.completed` 关联具体 `tool_ref`。日志和 Trace 继续由现有 Hook
体系记录，Secret 和大块二进制必须脱敏或省略。

## 11. Plugin 注册

新增内置目录：

```text
apps/agent/src/agent_orchestration/plugins/mcp/
├── __init__.py
├── manifest.json
├── factory.py
├── plugin.py
├── backend.py
├── client_manager.py
├── catalog.py
├── config.py
├── result_converter.py
└── tools.py
```

Manifest 固定声明：

- `provided_tools`：`mcp_tool_list`、`mcp_tool_search`、`mcp_tool_execute`；
- Persistence 的 Runtime 与 Session Capability，用于保存图片；
- 无 Plugin State；
- 无新增 Event 类型；
- `python_requires` 包含 FastMCP Client 依赖。

MCPPlugin 是默认内置必需 Plugin，三个入口保持稳定。`mcpServers` 只控制具体 Server：没有启用
Server 时 `list/search` 返回空目录，`execute` 返回目标 Server 未配置。MCPPlugin 自身无法构造属于
Runtime 启动失败；单个外部 MCP Server 无法连接只是该工具调用失败，不导致 SessionRuntime 失败。

## 12. 后续扩展入口

### 12.1 Resources

未来增加独立 `MCPResourceCatalog`，并平行提供：

```text
mcp_resource_list
mcp_resource_read
```

它复用 Server Registry、Client Manager、Backend 和 Result Converter，不修改 ReActAgent。

### 12.2 Prompts

未来增加独立 `MCPPromptCatalog`，并平行提供：

```text
mcp_prompt_list
mcp_prompt_get
```

Prompt 获取结果作为当前 User Prompt 的动态输入，不修改稳定 System Prompt。

### 12.3 反向能力

Roots、Sampling 和 Elicitation 通过 FastMCP Client Factory 的回调边界增加。第一阶段不提供
这些回调，Server 请求时按 FastMCP 的不支持行为返回。Sampling 和 Elicitation 在实现前必须另行
设计权限、递归、预算、超时和 UI 交互。

### 12.4 其他暴露策略

完整 Catalog 与模型入口相互独立。未来如果真实场景证明固定网关影响调用准确率，可以在不改
MCP 连接层的前提下增加 `direct` 或 `hybrid` 策略，把部分 Tool 投影成原生 Tool。第一阶段不实现
`DynamicToolProvider`。

## 13. 测试与验收

测试继续放在：

```text
apps/agent/test/agent_orchestration/plugins/mcp/
```

使用 pytest 和原生 `assert`，至少覆盖：

- 常见 stdio/HTTP `mcpServers` JSON 解析和 `enabled`；
- 无效配置不会尝试连接；
- Session 启动不会主动连接外部 Server；
- 并发首次使用只创建一个 Client；
- `list` 分页、稳定排序和多 Server 部分失败；
- `search` 的确定性文本排序和结果上限；
- `execute` 的 `tool_ref` 路由、参数原样传递和 Schema 校验；
- 文本、结构化数据、图片和混合内容转换；
- MCP `isError`、连接异常和参数错误进入现有 Tool 失败链路；
- `tools/list_changed` 只标记失效，下次使用才刷新；
- Catalog 未变化时复用同一 Snapshot；
- Session 停止关闭 Client 和 stdio 子进程；
- Secret 与 Base64 不进入日志、Trace 和 RuntimeUpdate；
- OpenAI 与 Anthropic 的 Tool Result 图片转换保持协议合法；
- 不同 Session 的 Client 和 Catalog 相互隔离。

验证顺序：

1. MCPPlugin 最小测试目录；
2. Agent 工具与多模态相关测试；
3. `make test-agent`；
4. `git diff --check`；
5. 在可用环境中分别用一个 stdio 和一个 Streamable HTTP Server 做小型真实 Smoke Test，
   不记录或输出 Secret。

## 14. 验收结果

第一阶段完成后，用户只需把 MCP Server 提供方的常见配置复制到 `settings.json.mcpServers`，
即可在任何 Session 中让 Agent：

```text
mcp_tool_list / mcp_tool_search
→ 获得目标 Tool 的完整 Schema
→ 按 Schema 构造 arguments
→ mcp_tool_execute
→ Icarus 校验并通过 FastMCP 调用外部 Server
→ 文本、结构化数据或图片结果返回 Agent
```

外部 Server 不在线时不影响 Icarus Session 启动；实际使用时错误进入当前标准工具失败和
RuntimeUpdate 链路。整个闭环不要求新增具体 Browser、Blender 或 Unity 适配代码。

## 15. 当前实现状态

- 已引入 `fastmcp-slim[client]==4.0.3` 与 `jsonschema>=4,<5`；
- 已实现顶层 `mcpServers` 配置、stdio/Streamable HTTP 推断、`enabled` 和环境变量引用；
- 已实现 Session 级按需连接、并发首次连接合并、Catalog Snapshot 和 list-changed 懒失效；
- 已实现 `mcp_tool_list`、`mcp_tool_search`、`mcp_tool_execute` 三个固定 Tool；
- 已实现完整 Schema 返回、确定性文本搜索、执行前 JSON Schema 校验和参数原样转发；
- 已使用禁止外部检索的 JSON Schema Registry，允许文档内 `$ref`，并避免校验时产生网络访问；
- 已实现文本、结构化数据、图片、Embedded Resource 和 Resource Link 转换；
- 已将 MCP 图片接入 Session Asset、Tool Result `ImagePart` 与 OpenAI/Anthropic Provider；
- 已复用现有 Tool Event、TaskErrorEvent、RuntimeUpdate 和 Hook/Trace，并补齐参数与错误脱敏；
- 已通过 FastMCP 4.0.3 的 stdio、Streamable HTTP 以及 `search → execute` 本地真实冒烟；
- 已通过 Agent 全量测试 440 项、Gateway 全量测试 11 项、TUI 全量测试 163 项及 12 个
  视觉快照；编译、依赖一致性和 diff 检查通过。
