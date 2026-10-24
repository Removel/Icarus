# Icarus 架构与安全评估报告

- 日期：2026-10-23
- 分支：`feat/webui`
- 范围：统一启动与 `icarus.toml`、多媒体输入链路、安全边界、密钥治理
- 方法：只读代码勘察，以当前源码与测试为准（不采信历史文档表述）
- 结论口径：每条判断附 `file:line` 证据；无法确认的项明确标注"未确认"

---

## 0. 执行摘要

| 维度 | 成熟度 | 一句话结论 |
|---|---|---|
| 统一启动 / 组件拓扑 | 良好 | 声明式拓扑 + 探活 + 进程身份记录，设计扎实；但原生进程管理依赖 Linux `/proc`，Windows 上不可用 |
| 多媒体（图片）输入 | 内核完整，前端空白 | Agent 内核与双 provider 适配器已端到端支持图片；WebUI 完全未接，TUI 仅 macOS |
| 安全边界 | 单一屏障 | 全部防线压在"环回绑定"一个假设上；跨出本机后存在一条**无鉴权 + 无沙箱**的完整攻击链 |
| 密钥治理 | 结构干净，清单有漏 | 单层明文环境变量，无 secret store；`.env` 缺 4 个必填值导致默认栈无法启动 |

### 需要立即处置的 6 项

1. **默认栈起不来**：`.env` 缺 `ICARUS_MEM0_POSTGRES_HOST`、`RELA_MEM_API_TOKEN`、`RELA_MEM_PG_PASSWORD`、`RELA_MEM_FALKORDB_PASSWORD`，均为 compose `${VAR:?}` 硬拦。
2. **WebUI 当前实际无鉴权**：`ICARUS_WEBUI_USER/PASSWORD` 未设置，`authorized()` 恒返回 true。
3. **残留密钥文件**：`apps/agent/.env` 含一个与根 `.env` 不同的 OpenAI key，当前无任何代码读取它。
4. **网关零鉴权且无 Origin 校验**：本机任意进程可驱动 agent 执行任意 shell。
5. **`secret_env` 声明不全**：mem0 的 `ICARUS_MEM0_API_KEY`、`ICARUS_MEM0_LLM_API_KEY` 未声明，缺失时报错发生在 compose 而非控制面。
6. **`ICARUS_JEV_API_KEY` 未文档化但功能已开启**：`settings.json` 中 `jev_enabled: true`，会静默降级为 `NullJudge`。

---

## 1. 统一启动与 `icarus.toml`

### 1.1 调用链

```
make start                    # Makefile:26-27
  └─ bin/icarus                # 需 Python ≥3.11，解析符号链接后定位 repo_root
       └─ python scripts/icarus/main.py
            ├─ configuration.py → registry.py     # 纯配置加载、地址归一化、校验
            └─ lifecycle.py  → graph.py           # 依赖闭包与拓扑序
                             → environment.py     # .env 合并、ICARUS_DATA_DIR 解析
                             → injection.py       # 子进程环境注入
                             → probes.py          # 健康探活
                             → process_runtime.py # 原生进程管理
                             → compose_runtime.py # Docker Compose 管理
```

`scripts/icarus/` 为纯标准库实现（TOML 走 `tomllib`），不依赖应用代码，因此配置校验可以在任何环境执行。

### 1.2 子命令

| 命令 | 行为 | 关键点 |
|---|---|---|
| `start [X]` | 无参启动默认栈的必需闭包；有名则只起该组件 | 前台组件（TUI）永远最后执行 |
| `stop [X]` | 无参按反序停默认闭包；有名只停该组件 | 从不删除数据卷或共享网络 |
| `status [X]` | 无参按注册顺序列出全部组件 | 依赖不可达时显示 `degraded` / `degraded optional` |
| `install [X] [--dev]` | 安装闭包 | `remote` 组件跳过；`--dev` 只装 `kind=app` 的 dev 命令 |
| `logs X [--no-follow]` | 跟随日志 | 前台 / remote 组件拒绝 |
| `tui [OPTS]` | 直接跑第一个 `foreground` 组件 | **不启动依赖**，只探活并要求已就绪 |
| `config validate` / `config show [X]` | 只读校验 / 输出有效注册表 JSON | 不读 `.env`、不碰 Docker、不解析密钥 |

`icarus --session-id ID` 这类以 `-` 开头的首个参数会被回退为 `tui` 子命令（兼容旧调用）。

### 1.3 配置优先级

```
拓扑：CLI 覆盖(--host/--port/--endpoint/--mode) > icarus.local.toml > icarus.toml > 内置默认
环境：进程环境 > .env（ICARUS_ENV_FILE 指定文件会整体替换而非叠加）
```

- `icarus.local.toml` 为被忽略的机器级覆盖文件，按表深合并，标量与数组直接替换。
- `--config PATH` 只加载该文件自己的同级 overlay，**不会**引入 `icarus.local.toml`。
- `ICARUS_DATA_DIR` 只来自合并后的环境，**不存在 `~/.icarus` 默认值**，未设置时受管进程与 compose 数据目录都会失败。

### 1.4 `icarus.toml` 字段语义

顶层：`version = 1`（必须是整数 1）、可选 `[defaults]`（仅允许 `mode`、`default_stack`）、以及若干 `[components.<name>]`。

| 字段 | 取值 | 说明 |
|---|---|---|
| `kind` | `app` / `middleware` | middleware 必须声明 `host`+`port`（除非 bundled 带探活） |
| `mode` | `local` / `remote` | remote 只探活，安装与停止为 no-op |
| `provisioning` | `process` / `foreground` / `compose` / `embedded` / `bundled` / `none` | 见下 |
| `enabled` | bool | false 时不入默认栈，status 显示 disabled |
| `owner` | 组件名 | `embedded`/`bundled` 必填，生命周期归一到 owner |
| `default_stack` | bool | 是否属于裸 `start`/`stop`/`install` |
| `endpoint` | `http/https/ws/wss` 绝对 URL | 禁止内嵌凭据、空白、疑似密钥的 query 键 |
| `host` / `port` / `network_host` / `network_port` | — | 宿主视图与 Docker 共享网络视图 |
| `probe` | `{type, path, token_env, header, expect_status, expect_body, timeout_seconds, max_response_bytes}` | `type` 为 `http` 或 `tcp` |
| `requires.<dep>` | `{required, endpoint_env, connection{...}}` | `required=false` 时降级只告警 |
| `start_command` / `install_command` / `dev_install_command` | argv 数组 | 占位符 `{repo_root}` `{python}` `{data_dir}` `{host}` `{port}` |
| `process_markers` | argv 片段数组 | 与 `/proc/<pid>/cmdline` 匹配，作为进程身份标识 |
| `env` | 目标名 → 字面值 | 公开引导值；**拒绝**疑似凭据的目标名 |
| `secret_env` | 目标名 → 源环境变量名 | 仅做存在性校验，值只从 base env 复制，不从其他导出派生 |
| `host_env` / `port_env` / `endpoint_env` / `bind_host` | 环境变量名 | 自身监听地址对外导出的名字 |
| `compose` / `compose_project` / `compose_command` / `expected_containers` | — | Compose 项目定义 |
| `data_dir` | 相对路径 | 解析在 `$ICARUS_DATA_DIR` 之下，非仓库内 |
| `shared_network` / `network` | bool / 名称 | 共享 Docker 网络，默认网络名 `icarus` |
| `image_action` | `build`（默认）/ `pull` | Compose 安装时的动作 |
| `endpoint_args` | 长选项 → 依赖名 | 仅前台组件，用于把依赖 endpoint 作为 CLI 参数传入 |

`provisioning` 六种语义：

- `process` — 原生后台进程，以 PID/命令行/cwd/监听四元组做身份校验，日志写 `logs/<name>.log`。
- `foreground` — 占用调用者终端（当前仅 `tui`），状态来自 `runtime/<name>/<pid>.json`。
- `compose` — Docker Compose 项目，按 project name + compose 文件标签过滤。
- `embedded` — 运行在 owner 内部，无独立进程（当前 `agent` → `gateway`）。
- `bundled` — 随 owner 的 Compose 项目提供，在 owner 就绪后再探活。当前无使用。
- `none` — 不提供也不启动，只探活，适用于已存在的外部服务。

### 1.5 默认栈与执行顺序

`default_stack = true` 的 7 个：`gateway`、`agent`、`tui`、`webui`、`mem0`、`openkb`、`rela-mem`；`postgres`、`falkordb` 通过 `rela-mem` 的必需依赖进入闭包。

计算出的闭包顺序：

```
(mem0, openkb, agent, gateway, tui, webui, postgres, falkordb, rela-mem)
```

但实际执行序与之不同：`Lifecycle.start` 把所有本地 `foreground` 组件单独收集，**在所有后台组件就绪后才执行**。因此真实顺序为：

```
后台：mem0 → openkb → agent(逻辑就绪) → gateway → webui → postgres → falkordb → rela-mem
前台：tui（最后，占用终端；退出不会停止后台服务）
```

注意：代码中没有 "profile" 概念。等价物是 `default_stack` 布尔集合加 `mode = remote` 覆盖。

### 1.6 启动的健壮性设计（值得肯定的部分）

- **探活有硬超时**：整个探测（DNS、连接、重试、header、慢速 body）由子进程加管道统一限时，超时即终止，不跟随重定向，禁用代理，不回显 URL/响应体/异常文本。
- **身份校验严格**：生成前检查端口占用与已有仓库进程；身份记录原子写入，失败回滚；终止前先捕获成员身份再校验，身份不确定时拒绝发信号。
- **被动操作不解密**：`install`/`stop`/`logs` 使用占位值（`not-used-offline`），因此这些操作不要求真实密钥。
- **失败分级**：必需依赖失败 → 阻断分支、退出非零；可选依赖失败 → 仅告警并继续。

### 1.7 缺陷与不一致

| # | 问题 | 影响 | 证据 |
|---|---|---|---|
| 1.1 | 原生进程身份依赖 Linux `/proc` | **Windows/macOS 上 `process` 型组件无法 start/stop**，仅能做配置校验与 remote 探活 | `docs/spec/2026-10-19-component-lifecycle/arch.md:140` |
| 1.2 | 文档称 mem0 为 3 容器含 `mem0-postgres` | 与实现不符：实际 `expected_containers = 1` 且要求**外部** pgvector | `arch.md:59-60`、`README.md:220-221` vs `icarus.toml:73-89` |
| 1.3 | 拓扑序并非"前台最后" | 是 `Lifecycle.start` 的分组实现保证的，不是图排序保证 | `lifecycle.py:326-339` |
| 1.4 | `compose`/`data_dir`/`workdir` 校验为仓库相对，但 `data_dir` 运行时拼在 `$ICARUS_DATA_DIR` 下 | 语义容易误解 | `registry.py:281-283` vs `compose_runtime.py:106-108` |

---

## 2. 多媒体输入链路

> 2026-10-24 实施更新：下列内容保留 2026-10-23 的审查快照。WebUI 图片选择、粘贴、拖入与历史显示，
> 以及 Gateway 的最小上传/读取接口已接入；Agent 源码保持不变。
> 当前实现与验证见 [图片输入计划](spec/2026-10-24-chat-image-input/plan.md)，不据此改写本报告其他审查结论。

### 2.1 分层现状

| 层 | 图片支持 | 说明 |
|---|---|---|
| `apps/agent` | **完整** | `ImagePart` / `ContentPart`、asset 存储、双 provider 映射全部就位 |
| `apps/gateway` | **部分** | 协议接受 `resources` 引用，但无上传传输通道 |
| `apps/tui` | **仅 macOS** | 剪贴板粘贴 → 标记 → ResourceRef |
| `apps/webui` | **无** | 附件按钮禁用，无任何上传/粘贴/拖放实现 |

### 2.2 已具备的能力（agent 内核）

- 消息模型：`Message.content: list[ContentPart]`，`ContentPart = TextPart | ImagePart`；`ImagePart` 支持 `source_type = "url" | "asset"`。
- Asset 存储：单张上限 20 MiB，支持 PNG/JPEG/GIF/WebP，**校验 media_type 与实际字节一致**，按 sha256 去重，落盘 `assets/<sha>.<ext>` 权限 `0600`，读取时防目录逃逸。
- 引用解析：`ResourceRef` 相对 `incoming/` 解析，显式拒绝 `..`、绝对路径与非安全路径段。
- 插件流：`user_input` 插件接受 `input_images`，导入失败时发 `image_import_failed` 任务错误而非静默丢弃；`blackboard` 插件会序列化/反序列化图片部件。
- Provider 映射：
  - OpenAI 协议 → `{"type":"image_url", ...}`，asset 转 data URI；**图片只允许出现在 user 角色**，工具返回的图片会被提升为合成 user 消息。
  - Anthropic 协议 → `{"type":"image","source":{"type":"base64",...}}`；工具结果可含 image block；非 user 角色拒绝图片。

### 2.3 缺口（全部在 WebUI 侧 + 一个网关路由）

| 缺口 | 内容 | 位置 |
|---|---|---|
| A | UI 无 file picker / paste / drag-drop / 缩略图；回形针按钮 `disabled` | `apps/webui/packages/chat-app/src/DswComposer.tsx:131-139` |
| B | 浏览器无法把文件写入 agent 的 `incoming/`；网关只有 `/health` 与 `/rpc` | `apps/gateway/src/app.py:45-60` |
| C | 提交时不带 `resources` 字段 | `apps/webui/packages/chat-app/src/useChatSession.ts:449-457` |
| D | 历史消息丢弃 `payload.resources`；无图片读取端点 | `apps/webui/packages/chat-app/src/updates.ts:54-61` |
| E | TUI 剪贴板仅 darwin 实现，Windows/Linux 返回 `None` | `apps/tui/src/clipboard.py:114-119` |

补充：
- 网关模型为 `extra="forbid"`，浏览器若发送任何非约定形状的附件字段会直接被拒。
- 协议层图片是**按引用**传递（`resource_id`），不是内联 base64，因此"上传"这一步必须由一个 HTTP 路由完成。
- 服务端标题摘要已能处理纯图片消息（返回 `"[Image]"`），不需要额外改动。

### 2.4 建议实施顺序

设计已存在，可直接施工，无需重新设计：`apps/webui/docs/spec/2026-10-23-dswui-chat-integration/plan-refinements.md:37-47` 逐条列出了 B → A/C → D 的顺序（该文档还标注 `progress.md:45` 为"仍未实现"）。

1. 网关新增 `POST /resources`（multipart），写入 runtime 的 `incoming` 目录，返回 `ResourceRefModel`。
2. WebUI 生产 server 与 Vite dev proxy 增加该路径转发。
3. Composer 接入文件选择、粘贴、拖放与缩略图；队列项承载 refs。
4. 提交序列化补 `resources`；历史渲染补 `user.message.resources` 与图片读取端点。

---

## 3. 安全边界

### 3.1 网络监听面

| 组件 | 默认绑定 | 代码级守卫 |
|---|---|---|
| gateway | `127.0.0.1:8765` | **无**，`--host` 可直接改 `0.0.0.0` |
| webui | `127.0.0.1:8080` | **有**（两处，见下） |
| mem0 | `127.0.0.1:8888` | 无，仅靠 compose 默认 |
| openkb | `127.0.0.1:7566` | 无阻断，仅告警 |
| rela-mem | 宿主 `127.0.0.1:8767` | 无代码守卫；应用默认反而是 `0.0.0.0`，靠 compose 映射收敛 |
| postgres | `127.0.0.1:15432` | compose 默认 |
| falkordb | `127.0.0.1:16379` | compose 默认 |

所有 compose 端口映射统一使用 `${VAR:-127.0.0.1}`，容器无法在未显式设置变量时意外发布到所有网卡。容器内部一律 `0.0.0.0`（mem0、openkb、rela-mem、webui 镜像），但仅在 Docker 网络内可达。

**WebUI 的两道守卫**：
1. Node 层：非 loopback 且无密码 → 抛错拒绝启动。
2. 启动器层：非 loopback 要求 user 与 password **同时**提供，否则 `SystemExit`。

注意两者不完全等价：Node 层只校验 password，因此"只设密码不设用户名"能通过 Node 层，只剩启动器一道拦截。

### 3.2 鉴权现状

| 表面 | 是否受保护 |
|---|---|
| gateway `/rpc`、`/health`、`/docs` | **完全无鉴权** |
| WebUI（未设密码时） | **无鉴权**（本机当前即此状态） |
| WebUI `/health` | 永远免认证 |
| mem0 `/docs`、`/`、`/auth/setup-status`、`/auth/register|login|refresh` | 免认证 |
| mem0 数据端点 | JWT / API key / admin key，除非 `AUTH_DISABLED` |
| openkb `/docs`、`/openapi.json`、SPA `/` | 免认证 |
| openkb `/api/v1/*` | 配了 token 才要求 Bearer，否则全开 |
| rela-mem | 全路由强制 Bearer，docs 全关，仅 `/healthz` 开放 |

WebUI 的防护细节（做得较好的部分）：
- CSRF：要求 `Host` 与配置 origin 一致；拒绝 `sec-fetch-site: cross-site`；非 GET/HEAD 要求 `Origin` 精确匹配。
- 凭据隔离：删除客户端传入的 `authorization`、`cookie`、`x-api-key`、`proxy-authorization`、`forwarded`、`origin` 与所有 `x-forwarded-*`，改由服务端按路由注入（mem0 注入 `X-API-Key`，openkb 注入 `Bearer`，**gateway 不注入任何凭据**）。
- 静态文件：拒绝 `\` 与 NUL，`realpath` 后要求 `relative(root, file)` 不以 `..` 开头、非绝对、且为普通文件。

### 3.3 Agent 工具能力（关键风险面）

| 工具 | 路径约束 | 命令约束 | 环境变量 |
|---|---|---|---|
| `bash` | **无**（`workdir` 任意） | **无白名单/黑名单** | **全量继承**（未传 `env=`） |
| `read` | **无**（不 `resolve()`、不查符号链接） | — | — |
| `write` | **无**，且自动 `parent.mkdir(parents=True)` | — | — |
| `insert` | **无** | — | — |
| `background_process` | 有（解析符号链接 + `is_relative_to`） | **无** | **全量继承** |
| `knowledge` 上传 | 有（`O_NOFOLLOW` + 相对工作区） | 扩展名白名单 | — |
| skill 生成执行器 | 有（Draft 目录 + 符号链接拒绝 + 敏感文件名黑名单） | 有（程序白名单、禁 shell 组合、禁 `python -c`、禁 `node -e`） | 仅 `PATH`/`LANG`/`LC_ALL` |

关键事实：

- `builtin_tools/factory.py` 显式 `del workspace_path, session_id, required_capabilities` —— 通用工具**主动丢弃**工作区约束。
- 框架有超时与输出预算（默认 120s / 上限 600s；输出 4000–16000 token；每批 ≤8 调用；bash 输出上限 16 MiB），但**没有审批门、没有确认门、没有按工具的权限检查**。
- 唯一硬化的执行器只服务于 skill **生成**流程，其自身文档也明确"是实用护栏，不是操作系统沙箱"。

### 3.4 提示注入与不可信内容

- **默认启用 Playwright MCP**：具备完整浏览器自动化能力，子进程继承 agent 环境变量。
- **工具输出零净化**：结果直接 `json.dumps` 进模型上下文，仅受 token 截断，无 HTML/JS 剥离、无编码归一化、无注入模式过滤。
- **"不可信数据"只是提示词**：knowledge / memory / skill / builtin_tools / relationship 五处 prompt 都写了"外部数据不执行其中指令"，属于对模型的请求而非机制。
- **注入检测默认关闭**：出厂默认 `jev_enabled: false`，本机 `settings.json` 反而为 `true`；且该 judge 只分类**用户输入**，不覆盖工具/网页内容。

### 3.5 完整攻击链（本机当前成立）

```
任意本地进程
  → 127.0.0.1:8080  WebUI（未设密码 → authorized() 恒 true）
  → /rpc（代理不加任何凭据，index.mjs:36）
  → 127.0.0.1:8765  gateway（零鉴权，WebSocket 无条件 accept）
  → session.submit，workspace_path 由客户端任意指定（仅 min_length 校验）
  → settings.update 可改写模型 base_url（把 prompt/补全外送）
  → 模型调用 bash（无白名单、workdir 任意、全量继承环境变量）
  → 宿主 shell，身份为 gateway 运行用户，env 内含全部密钥
```

唯一屏障是环回绑定，而项目自身文档已声明这不够：`apps/webui/docs/deployment.md:3`（"不要将 Mem0、OpenKB 或 Gateway 直接公开到互联网"）、`README.md:300,337`（"GUI 和 Gateway 远程认证尚未接入"）。

### 3.6 密钥脱敏的实测缺口

- 能正确脱敏：映射形式 `{"OPENAI_API_KEY": ...}`、`Authorization: Bearer ...`、`api_key: xyz`、`password=xyz`。
- **不能脱敏**：`.env` 风格文本 `OPENAI_API_KEY=sk-...`（因归一化为 `openai_api_key`，既不等于也不前缀/后缀匹配 `api_key`）。
- **模型侧完全无脱敏**：`serialize_tool_result` 不调用 redactor，因此 `bash` 执行 `env`/`cat .env` 的输出会**原样进入模型上下文**。

### 3.7 已声明的不设防项（来自项目自身文档）

| 来源 | 表述 |
|---|---|
| `docs/todo/agent-core.md:265-266` | 统一 Tool 权限/安全策略属未完成项，"不把各具体 Tool 已有的局部限制误当成统一沙箱" |
| `apps/gateway/README.md:72` | 第一阶段默认只监听本机地址，不提供远程认证 |
| `README.md:300,337` | WebUI 单用户，多租户后端与远程认证尚未接入 |
| `apps/webui/README.md:73` | 不支持 iframe 嵌入或多租户权限隔离 |
| `infra/README.md:49-51` | 镜像按版本固定而非 digest；TCP `reachable` 不代表通过鉴权 |
| `docs/spec/2026-10-04-repository-infra/plan.md:511` | **无 `SECURITY.md`**，漏洞上报渠道未定义 |

---

## 4. 密钥清单

### 4.1 传输模型

**没有任何 Docker secret、没有 `*_FILE`、没有密钥存储服务。** 全部凭据以明文环境变量传递：

```
.env / 进程环境
  → scripts/icarus/environment.py 合并（进程环境优先）
  → injection.py 按 secret_env 复制同名值（仅存在性校验）
  → 原生进程 env / Compose --env-file + 完整 env 字典
  → 容器内重命名为组件自己的变量（如 OPENAI_API_KEY、ADMIN_API_KEY、LLM_API_KEY）
```

### 4.2 完整清单（16 项）

| # | 变量 | 归属 | 用途 | 必填 | 回落 |
|---|---|---|---|---|---|
| 1 | `OPENAI_API_KEY` | agent | 主模型（OpenAI 协议，实际接 DeepSeek） | ✅ | — |
| 2 | `ANTHROPIC_API_KEY` | agent | Anthropic 协议 | 仅切换协议时 | — |
| 3 | `ICARUS_MEM0_LLM_API_KEY` | mem0 | 容器内注入为 `OPENAI_API_KEY` | ✅ | → #1 |
| 4 | `ICARUS_OPENKB_LLM_API_KEY` | openkb | 容器内注入为 `LLM_API_KEY` | ✅ | → #1 |
| 5 | `ICARUS_MEM0_API_KEY` | mem0 ↔ agent/webui | `X-API-Key`，≥16 字符 | ✅ | 仅 `AUTH_DISABLED=true` 旁路 |
| 6 | `ICARUS_OPENKB_API_TOKEN` | openkb ↔ agent/webui/探活 | Bearer，≥16 字符 | ✅ | 无（服务端未设即全开） |
| 7 | `ICARUS_OPENKB_PAGEINDEX_API_KEY` | openkb | PageIndex Cloud | ❌ | 空 |
| 8 | `RELA_MEM_API_TOKEN` | rela-mem ↔ agent/webui | Bearer | ✅ | 无 |
| 9 | `RELA_MEM_PG_PASSWORD` | rela-mem + postgres | PostgreSQL 密码 | ✅ | 无 |
| 10 | `RELA_MEM_FALKORDB_PASSWORD` | rela-mem + falkordb | Redis `requirepass` | ✅ | 无 |
| 11 | `ICARUS_MEM0_POSTGRES_PASSWORD` | mem0 | **外部** pgvector 密码 | ✅ | 无 |
| 12 | `ICARUS_MEM0_JWT_SECRET` | mem0 | 会话 JWT 签名 | ✅ | 无 |
| 13 | `ICARUS_WEBUI_USER` | webui | Basic 用户名 | 仅非环回 | 与 #14 成对 |
| 14 | `ICARUS_WEBUI_PASSWORD` | webui | Basic 密码，兼鉴权总开关 | 仅非环回 | 与 #13 成对 |
| 15 | `ICARUS_JEV_API_KEY` | agent | persona judge（OpenRouter） | 可选 | → #16，否则 `NullJudge` |
| 16 | `ARGUS_JEV_API_KEY` | agent | #15 的旧名 | 可选 | — |

非密钥但需同步修改的伴随项：`ICARUS_MEM0_OPENAI_BASE_URL`、`ICARUS_MEM0_LLM_MODEL`、`ICARUS_OPENKB_OPENAI_BASE_URL`、`ICARUS_OPENKB_LLM_MODEL`、以及 `apps/agent/settings.json` 中的 `openai_base_url` / `anthropic_base_url`。

### 4.3 分组

- **模型 provider**：#1 #2 #3 #4
- **服务间 token**：#5 #6 #8（注意：gateway ↔ TUI/WebUI 这一跳**完全没有 token**）
- **数据库/图库密码**：#9 #10 #11 #12
- **UI 凭据**：#13 #14
- **第三方**：#7 #15 #16

### 4.4 治理缺口

| # | 问题 | 后果 |
|---|---|---|
| 4.4.1 | `.env` 缺 `ICARUS_MEM0_POSTGRES_HOST` | mem0 启动即失败（`${VAR:?}`） |
| 4.4.2 | `.env` 缺 `RELA_MEM_API_TOKEN` / `RELA_MEM_PG_PASSWORD` / `RELA_MEM_FALKORDB_PASSWORD` | rela-mem 与 postgres/falkordb 启动即失败；因 rela-mem 属默认栈且 required，**`icarus start` 整体无法成功** |
| 4.4.3 | 三个 key 值完全相同（#1 = #3 = #4） | 轮换必须三处同改，否则后两者静默保留旧凭据 |
| 4.4.4 | `icarus.toml` 的 `secret_env` 声明不全（mem0 漏 #3 #5；openkb 漏 #4 #7） | 缺失时不报清晰的控制面错误，而在 compose 抛原始 `${VAR:?}` |
| 4.4.5 | 残留 `apps/agent/.env`（含与根 `.env` 不同的 OpenAI key） | 当前无代码读取；属应轮换后删除的死重。旧 README 快照 `PKG-INFO:122` 仍错误宣称"Gateway 和 TUI 都读该文件" |
| 4.4.6 | `ICARUS_JEV_API_KEY` 不在 `.example.env`，但 `settings.json` 已开 `jev_enabled: true` | 开了功能却无文档化凭据，静默降级为 `NullJudge` |
| 4.4.7 | `ICARUS_WEBUI_ORIGIN`（非环回必填）、`ICARUS_OPENKB_KNOWLEDGE_BASE` 未出现在根 `.example.env` | 文档漂移 |
| 4.4.8 | `ICARUS_MEM0_API_KEY` 存在"必需"与"可空"两种表述 | `.example.env` 说 `AUTH_DISABLED=false` 即必需；compose 默认却是空；启动器才是真实执行点 |
| 4.4.9 | `ICARUS_OPENKB_API_TOKEN` 同上：启动器要求 ≥16 字符，服务端未设则全开 | 唯一真实强制在控制面 |
| 4.4.10 | OpenKB 可通过配置轮换接口把 `LLM_API_KEY` **写入磁盘** `<kb>/.env` 或 `~/.config/openkb/.env` | 环境变量来源的密钥泄漏到 `ICARUS_DATA_DIR` 之外的普通文件 |
| 4.4.11 | 存在 `apps/rela-mem/.env.example` 中的三个遗留变量（`RELA_MEM_DB_BIND_ADDRESS` 等）已无任何代码引用 | 误导配置 |

### 4.5 运行默认栈的最小 `.env`

```
OPENAI_API_KEY                    # 模型调用
ICARUS_DATA_DIR                   # 绝对路径
ICARUS_MEM0_API_KEY               # ≥16 字符
ICARUS_MEM0_POSTGRES_PASSWORD
ICARUS_MEM0_POSTGRES_HOST         # 当前缺失
ICARUS_MEM0_JWT_SECRET
ICARUS_OPENKB_API_TOKEN           # ≥16 字符
RELA_MEM_API_TOKEN                # 当前缺失
RELA_MEM_PG_PASSWORD              # 当前缺失
RELA_MEM_FALKORDB_PASSWORD        # 当前缺失
# 可选
ANTHROPIC_API_KEY                 # 仅 use_protocol = anthropic
ICARUS_MEM0_LLM_API_KEY           # 缺省回落 OPENAI_API_KEY
ICARUS_OPENKB_LLM_API_KEY         # 缺省回落 OPENAI_API_KEY
ICARUS_OPENKB_PAGEINDEX_API_KEY
ICARUS_JEV_API_KEY                # settings.json 中 jev_enabled = true
ICARUS_WEBUI_USER / _PASSWORD     # 仅非环回部署
```

---

## 5. 优先级建议

### P0 — 阻断性或高危，建议本周内处理

1. **补齐 `.env` 四个缺失值**，恢复默认栈可启动。
2. **轮换并删除 `apps/agent/.env`**（该 key 与根 `.env` 不同，属于未纳管凭据）。
3. **给 gateway 加鉴权**，或至少在非环回启动时强制 token；同时补 WebSocket 升级的 Origin 校验。
4. **明确 `workspace_path` 约束**：即使保持当前开放模型，也应记录/校验其必须落在允许的根目录内。
5. **给 `bash`/`read`/`write`/`insert` 加最小护栏**：环境变量白名单（至少剥离 `*_KEY`/`*_TOKEN`/`*_PASSWORD`/`*_SECRET`）、路径 `resolve()` + `is_relative_to`、可选命令审批。

### P1 — 结构性改进

6. **WebUI 图片上传**：按 `plan-refinements.md` 实现 `POST /resources` → 代理 → composer → 历史渲染。
7. **`secret_env` 声明补全**，让缺失在控制面清晰报错。
8. **修复 redactor 对 `NAME=value` 的漏判**；决定模型侧是否也做脱敏。
9. **统一文档与实现**：修正 mem0 容器数、`apps/agent/.env` 读取说明、`secret_env` 清单。
10. **`ICARUS_JEV_API_KEY` 纳入 `.example.env`**，或把 `jev_enabled` 调回 false。

### P2 — 长期治理

11. 引入 `SECURITY.md` 与漏洞上报渠道。
12. 镜像改为 digest 固定。
13. 评估 Secret store 或 `*_FILE` 支持，替代全明文 env。
14. 统一 Tool 权限策略（对应 `docs/todo/agent-core.md:265-266`）。
15. 解决 Windows 下原生进程管理的跨平台问题（当前 `make start` 在 Windows 无法管理 gateway/webui）。

---

## 6. 证据索引

### 统一启动
`bin/icarus:1-26`、`Makefile:26-39`、`scripts/icarus/main.py:52-87,103-206`、`registry.py:18-110,213-407`、`configuration.py:15-62`、`graph.py:13-79`、`environment.py:11-61`、`injection.py:43-143`、`probes.py:25-146`、`process_runtime.py:55-122,263-355,357-415,445-494`、`compose_runtime.py:37-145`、`lifecycle.py:32-49,72-131,133-256,268-396`

### 图片链路
`apps/agent/src/icarus/core/models/messages.py:18-61`、`icarus/runtime/assets.py:20-27,150-209`、`icarus/core/resource_ref.py:21-50`、`agent_app/plugins/user_input/plugin.py:102-296`、`icarus/core/agents/react_agent.py:446-459`、`agent_app/model_provider/impl/openai_llm.py:239-323`、`agent_app/model_provider/impl/anthropic_llm.py:264-349`、`apps/gateway/src/protocol/methods.py:76-90,276-332`、`packages/gateway_protocol/models.py:22-24`、`apps/tui/src/clipboard.py:114-174`、`apps/tui/src/submission.py:8-55`、`apps/webui/packages/chat-app/src/DswComposer.tsx:131-139`、`useChatSession.ts:13-20,449-457`、`updates.ts:3-13,54-61`、`apps/webui/server/index.mjs:22-37`

### 安全
`apps/webui/server/index.mjs:10-17,38-46,58-99,121-166,188-194,227`、`apps/webui/scripts/icarus_start.py:20,54-63,84`、`apps/gateway/src/app.py:42-60`、`apps/gateway/src/main.py:12-15`、`apps/gateway/src/connection.py:50`、`apps/gateway/src/protocol/methods.py:47-48`、`apps/mem0/server/auth.py:15-20,144-220`、`apps/mem0/server/main.py:91-105,170-177,336-582`、`apps/mem0/server/routers/auth.py:88-144`、`apps/openkb/openkb/api_helpers.py:64-125`、`apps/openkb/openkb/api.py:158-174,186-791`、`apps/rela-mem/src/api/dependencies.py:9-11`、`apps/rela-mem/src/main.py:61-97`、`apps/agent/src/agent_app/plugins/builtin_tools/bash_tool.py:43,101-110,263-282,285-336,420-426,466-476`、`read_tool.py:62,74`、`write_tool.py:39-43`、`insert_tool.py:44-59`、`factory.py:9-12`、`plugins/process/manager.py:110-119,652-683`、`plugins/skill/generation_tools.py:35-52,250,460-530`、`plugins/knowledge/plugin.py:27-32,133-219`、`icarus/core/tools/settings.py:12-18`、`icarus/core/tools/result_budget.py:44-55`、`agent_app/utils/redactor.py:11-36,108-120`、`agent_app/config/environment.py:15-26`、`agent_app/config/config_loader.py:33-39`、`apps/agent/settings.json:22,48-60`、`agent_app/resources/default_settings.json:24,55-60`、`infra/postgres/compose.yaml:9`、`infra/falkordb/compose.yaml:6-15`

### 密钥
`.example.env:1-57`、`icarus.toml:73-174`、`apps/mem0/server/docker-compose.yaml:7,16-31`、`apps/openkb/docker-compose.yaml:9-18`、`apps/rela-mem/docker-compose.yaml:7-12`、`apps/mem0/scripts/icarus_compose.py:25-78`、`apps/openkb/scripts/icarus_compose.py:29-73`、`apps/webui/scripts/icarus_start.py:21-51`、`apps/webui/server/index.mjs:12-37`、`apps/agent/src/agent_app/plugins/persona/judge.py:101-146`、`apps/openkb/openkb/api_config.py:74-80,158,235-240,400-404`

### 文档与规范
`README.md:126-142,181-246,284-341`、`CONTRIBUTING.md:12-18`、`infra/README.md:8-104`、`docs/spec/2026-10-19-component-lifecycle/arch.md:16-144`、`design.md:41-354`、`apps/webui/docs/spec/2026-10-23-dswui-chat-integration/plan-refinements.md:37-47`、`docs/todo/agent-core.md:265-266`、`spec/2026-08-30-app-dependency-and-runtime-environments.md:58-121`
