# Agent Gateway Development Plan｜Agent Gateway 实施计划

## 目标

基于：

- `apps/gateway/docs/arch/agent-gateway-positioning-design.md`；
- `apps/agent/docs/arch/device-agent-runtime-session-design.md`；
- `apps/agent/docs/plan/device-agent-runtime-session-development-plan.md`；

实现独立 `apps/gateway/` 应用，使 TUI 和 Backend 通过 FastAPI WebSocket 上的 JSON-RPC 2.0 访问
设备级 AgentRuntime，并接收公共 RuntimeUpdate。第一阶段 Gateway 与 AgentRuntime 同进程运行，
默认只监听本机地址。

TUI 从进程内 AgentRuntimeService 到 Gateway Client 的迁移属于 TUI 应用计划：
`apps/tui/docs/plan/tui-gateway-migration-development-plan.md`。旧 Service 和内部 Event 输出链只有在
该计划完成后才最终删除。

## 前置条件

开始 Gateway 实现前，Agent 计划至少完成：

- SessionRuntime；
- AgentRuntime、Session Registry、single-flight resume 和统一 unload；
- Session/Task 状态查询；
- RuntimeUpdatePlugin 和设备级 RuntimeUpdateSubscription；
- submission_id 内存幂等；
- ResourceRef 到 Session Asset 的安全导入。

Gateway 不临时绕过缺失的 AgentRuntime 能力，也不直接访问 SessionRuntime、PluginManager、
EventBus 或具体 Plugin。

## 实施原则

- Gateway 只做协议校验、AgentRuntime 调用、连接订阅和序列化；
- 不引入 tinyrpc、fastapi-ws-rpc 或第二套 RPC 框架；
- Pydantic 模型与 Agent 应用对象分开，Gateway 显式转换；
- JSON-RPC 参数保持扁平，避免通用 request/context 大对象；
- 不暴露 traceback、绝对路径、内部 Event 类名或 source_plugin_id；
- 不在 Gateway 保存 Session、Task、Queue 或历史的第二份事实；
- 一个慢连接不能阻塞其他连接或 AgentRuntime；
- TUI 完成迁移后不保留进程内 Agent fallback。

## 目标文件结构

```text
apps/gateway/
├── src/
│   ├── __init__.py
│   ├── app.py
│   ├── main.py
│   ├── connection.py
│   └── protocol/
│       ├── __init__.py
│       ├── errors.py
│       ├── jsonrpc.py
│       ├── methods.py
│       └── models.py
├── test/
│   ├── test_app.py
│   ├── test_connection.py
│   ├── test_jsonrpc.py
│   └── test_methods.py
└── docs/
    ├── arch/
    └── plan/
```

具体拆分允许按实现体量合并小文件，但不得把 AgentRuntime、连接循环、协议解析和 TUI UI 状态写进
同一个模块。

## 第一阶段 JSON-RPC 方法

WebSocket 承载 JSON-RPC 2.0 Request、Response、Error 和 Notification。第一阶段方法：

| 方法 | AgentRuntime 调用 | 说明 |
|---|---|---|
| `runtime.get_status` | Runtime 只读状态 | Gateway/Runtime 是否可接收调用 |
| `session.create` | `create_session` | 创建并立即加载空 Session |
| `session.list` | `list_session_statuses` | 列出 Workspace 本地与内存 Session |
| `session.get` | `get_session_status` | 获取一个 Session 状态 |
| `session.submit` | `submit` | 自动恢复已存在 Session 并提交 |
| `session.cancel` | `cancel_task` | 取消已加载 Session 中的 Task |
| `session.unload` | `unload_session` | 空闲时释放 SessionRuntime |
| `task.get_status` | `get_task_status` | 查询内存中的当前或近期 Task |
| `session.subscribe` | Gateway 连接状态 | 关注一个 Session 的 Update |
| `session.unsubscribe` | Gateway 连接状态 | 取消关注一个 Session |

Gateway 不提供远程 `runtime.stop`、文件上传、Session 删除、用户权限、Memory 或知识库接口。

公开参数使用 `workspace_path + session_id`；响应只返回 `workspace_key`，不在 RuntimeUpdate 或错误中
回传绝对路径。

## 阶段一：Gateway 应用骨架与依赖

### 新增或更新文件

- `pyproject.toml`
- `apps/gateway/src/__init__.py`
- `apps/gateway/src/main.py`
- `apps/gateway/src/app.py`
- `apps/gateway/test/test_app.py`

### 开发内容

1. 在项目运行依赖增加 `fastapi`、`uvicorn` 和 `websockets`，测试依赖增加 `httpx`；不引入额外
   JSON-RPC 框架。
2. 提供 `create_app(agent_runtime)`，通过 FastAPI lifespan 启停同进程唯一 AgentRuntime。
3. 固定 WebSocket RPC 路径为 `/rpc`，并增加 HTTP `/health`。health 只报告 Gateway 与 AgentRuntime 是否正在接受
   调用，不枚举 Session 或业务数据。
4. 默认监听 `127.0.0.1:8765`；host、port 由 CLI 参数覆盖。第一阶段不在无认证前提下默认监听
   局域网或公网。
5. Gateway 退出顺序：停止接受请求、关闭连接、停止 Update pump、调用 AgentRuntime.stop、退出。
6. 增加独立 `icarus-gateway` CLI 入口，不改变现有 `icarus` TUI 命令，直到 TUI 迁移阶段。

### 定向测试

- lifespan 只启动/停止一次 AgentRuntime；
- AgentRuntime 启动失败时 health 和 WebSocket 明确失败；
- 关闭顺序不会在连接仍写入时提前销毁 Runtime；
- 默认绑定配置是本机地址。

## 阶段二：JSON-RPC 2.0 最小协议层

### 新增文件

- `apps/gateway/src/protocol/jsonrpc.py`
- `apps/gateway/src/protocol/models.py`
- `apps/gateway/src/protocol/errors.py`
- `apps/gateway/test/test_jsonrpc.py`

### 开发内容

1. 用 Pydantic 定义 JSON-RPC 2.0 Request、Response、Error 和 Notification 信封。
2. 支持字符串或整数 request id；Notification 没有 id 且不返回 Response。
3. 实现实际使用的单请求子集；第一阶段不实现 Batch Request。收到 Batch 时返回明确的
   invalid request。
4. 分开处理 parse error、invalid request、method not found、invalid params 和 internal error。
5. 业务错误使用稳定的 Gateway error code/data 映射；data 只包含安全 code 和必要字段，不带异常
   repr、traceback、配置、凭据或绝对路径。
6. 所有参数和结果模型使用明确 Pydantic 类型；不接受多余字段。
7. RuntimeUpdate 使用 `runtime.update` JSON-RPC Notification，params 是公共 Update 的网络模型。
8. 标准 JSON-RPC 错误使用规范 code：parse error `-32700`、invalid request `-32600`、method not
   found `-32601`、invalid params `-32602`、internal error `-32603`；Agent 业务错误统一使用
   `-32000`，并在 `data.code` 中区分稳定业务原因。

### 定向测试

- 合法 Request/Notification/Response round trip；
- JSON 解析失败、版本错误、缺少 method、未知字段和错误参数；
- Notification 不产生 Response；
- Python 异常不泄漏 traceback 或路径；
- RuntimeUpdate 时间、payload 和可选 task_id 正确序列化。

## 阶段三：AgentRuntime 方法适配

### 新增文件

- `apps/gateway/src/protocol/methods.py`
- `apps/gateway/test/test_methods.py`

### 更新文件

- `apps/gateway/src/app.py`
- `apps/gateway/src/protocol/models.py`

### 开发内容

1. 建立显式 method registry，每个方法只做 Pydantic 输入转换、调用一个 AgentRuntime 操作并转换
   返回值；不使用反射把所有 Runtime 方法自动暴露。
2. session.create 不把已存在 Session 转成 resume；session.submit 不把不存在 Session 转成 create。
3. session.submit 要求 submission_id，并传递 prompt 与 ResourceRef 列表。
4. session.cancel 不恢复已卸载 Session；session.unload 对 busy 返回稳定业务结果，不转成连接错误。
5. session.get/list 和 task.get_status 保持只读，不刷新空闲时间。
6. 将 Agent 应用层稳定错误映射为 JSON-RPC 业务错误，包括 Session 不存在、Session 已存在、提交
   冲突、Session busy、Task 状态不可用、资源无效和 Runtime stopping。
7. 同一连接允许多个请求并发处理；同 Session 修改串行由 AgentRuntime 保证，Gateway 不复制锁。

### 定向测试

- 每个 RPC 方法的成功、参数错误和业务错误映射；
- 相同 submission_id 重试返回相同 task_id，不同内容返回冲突；
- 同 Session 并发 submit 由 Runtime 正确串行接受；
- 不同 Session RPC 可以并行；
- 只读请求不改变 last_activity_at。

## 阶段四：连接订阅与 RuntimeUpdate 分发

### 新增文件

- `apps/gateway/src/connection.py`
- `apps/gateway/test/test_connection.py`

### 更新文件

- `apps/gateway/src/app.py`
- `apps/gateway/src/protocol/models.py`

### 开发内容

1. Gateway 启动时向 AgentRuntime 建立一个设备级 RuntimeUpdateSubscription，并运行一个 Update
   pump；不为每个 WebSocket 或每个 Session 重新订阅 AgentRuntime。
2. 每个 WebSocket Connection 保存自己关注的 `(workspace_key, session_id)` 集合。subscribe 和
   unsubscribe 只修改连接内状态，不进入 AgentRuntime Session Registry。
3. Update pump 按身份筛选后写入每个连接独立的有界发送队列；单连接 Writer 保证 WebSocket 发送
   顺序，RPC Response 和 Runtime Notification 都通过该 Writer 输出，避免并发 send。
4. 连接队列溢出时以明确 slow_consumer 原因关闭该连接；不阻塞 Update pump、其他连接或 Runtime。
5. WebSocket 断开只清理连接和订阅集合，不 unload Session、不取消 Task。
6. Runtime 设备订阅 overflow 表示 Gateway 自身已经漏失 Update：关闭全部现有 WebSocket 连接，
   让客户端明确走状态重建，再重建设备订阅并记录错误；第一阶段不声称补发缺失 Update。
7. 第一阶段不提供 sequence、游标和历史补发。重连后客户端重新 subscribe，并调用 session/task
   查询恢复当前状态。
8. AgentRuntime 设备订阅和 Gateway 连接发送队列第一阶段默认容量均为 4096，并允许构造或配置覆盖；
   容量调整不改变溢出语义。

### 定向测试

- 两个连接关注不同 Session 时只收到各自 Update；
- 一个连接关注多个 Session 时保留实际发布顺序；
- Session unload/resume 后无需重新绑定 AgentRuntime 内部对象；
- 慢连接 overflow 不影响快连接；
- 断线不停止 Session 或 Task；
- Response 和 Notification 不发生 WebSocket 并发写错误。

## 阶段五：ResourceRef 协议接入

### 更新文件

- `apps/gateway/src/protocol/models.py`
- `apps/gateway/src/protocol/methods.py`
- `apps/gateway/test/test_methods.py`

### 开发内容

1. session.submit 只接受 ResourceRef 数组，不接受 Base64、绝对路径或 file URI。
2. Gateway 只校验 ResourceRef 的协议形状并转发，不读取文件、检查图片格式或复制 Session Asset。
3. AgentRuntime 在返回 task_id 前完成受控路径解析、文件签名校验和 Session assets 导入。

### 定向测试

- Gateway 不读取图片内容；
- 相同 submission_id 和 ResourceRef 重试返回相同 task_id；
- 路径穿越、绝对路径、缺失资源和伪造格式映射为稳定错误；
- 文本提交不携带 ResourceRef 时不受影响。

## 阶段六：Gateway 收口

### 开发内容

1. 更新 pyproject 运行依赖、Gateway CLI 和 package-data。
2. Gateway 只导入 AgentRuntime 公共类型，不导入 SessionRuntime 或 Plugin 内部类型。
3. 补充结构化启动、连接、RPC、订阅溢出和关闭日志，确保不记录密钥、Prompt、绝对路径或资源
   内容。
4. 完成 Gateway 文档和 TODO；AgentRuntimeService 的最终删除留到 TUI 迁移计划收口阶段。

## 验证顺序

Gateway 每阶段定向测试：

```bash
apps/agent/.venv/bin/python -m pytest apps/gateway/test -q
```

Gateway 与 Agent 联调后：

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m pytest apps/gateway/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test \
  apps/gateway/src apps/gateway/test
git diff --check
```

补充本机真实冒烟：

```text
启动 Gateway
→ 建立测试 WebSocket Client
→ 创建 Session
→ 提交文本和 ResourceRef 图片
→ 观察文本、Tool、Usage 和终态 Update
→ 断开 TUI，确认 Task/Session 不停止
→ 重连并恢复状态
→ 显式 unload，再次 submit 自动 resume
```

真实模型冒烟只在凭据可用时执行，不输出密钥、完整 Prompt 或本地绝对路径。

## 完成标准

- FastAPI WebSocket JSON-RPC 2.0 使用明确 Pydantic 模型并通过协议错误测试；
- Gateway 与 AgentRuntime 同进程，但只有稳定公开接口耦合；
- Session create/list/get/submit/cancel/unload 和 Task 查询完整可用；
- 每个连接只收到已关注 Session 的 RuntimeUpdate；
- 慢连接不会阻塞 AgentRuntime 或其他客户端；
- submission_id 重试不会在同一进程内重复创建 Task；
- 图片不经 JSON-RPC 传二进制或绝对路径，返回 task_id 前已成为 Session Asset；
- Gateway 不依赖内部 Plugin Event 或 SessionRuntime；
- Agent 与 Gateway 全量测试、编译、diff 和本机冒烟通过。

## 实施结果

- 已实现 FastAPI lifespan、`/health`、`/rpc` 和同进程设备级 AgentRuntime；
- 已实现 JSON-RPC 2.0 最小子集、Pydantic 严格校验、稳定业务错误、Session/Task 方法和
  ResourceRef 协议；
- 已实现单设备 Update 订阅、按连接 Session 过滤、独立有界发送队列和慢消费者断开；
- 默认监听 `127.0.0.1:8765`，提供 `icarus-gateway` 命令；
- 真实 Uvicorn `/health`、WebSocket `runtime.get_status`、Notification 无响应和 GatewayClient
  创建/订阅/断开不卸载 Session 冒烟通过。
