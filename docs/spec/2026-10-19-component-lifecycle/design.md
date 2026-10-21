# 统一组件生命周期与运维控制面｜设计

- 日期：2026-10-19
- 状态：注册表、运行时、CLI 与应用连接覆盖已实现；已执行隔离真实验收，最终证据、审查与文档收尾见 [实施记录](execution.md)。当前代码架构见 [arch.md](arch.md)。
- 范围：仓库根控制面 `scripts/icarus/`、根 `icarus.toml`、`infra/` 中间件定义，以及应用侧连接配置的读取方式。
- 关联：[仓库基建设计](../2026-10-04-repository-infra/arch.md)、[Agent relationship 跨应用约定](../../../spec/2026-10-19-agent-relationship-integration.md)、[rela-mem 架构](../../../apps/rela-mem/docs/spec/2026-10-18-rela-mem/arch.md)。

## 背景与问题

设计前的根控制面 `scripts/icarus/main.py` 用一组硬编码常量描述项目：`BACKGROUND_PROJECTS`、`ENDPOINTS`、`PORTS`、`COMPOSE_PROJECTS`、`COMPOSE_FILES`、`EXPECTED_CONTAINERS`。原实现只能管理本机进程和本机 Compose，无法表达：

- 某个应用或中间件已经部署在另一台设备上，只需登记地址并探测；
- 一个应用依赖 PostgreSQL、FalkorDB、Redis、MQ 等中间件，而这些中间件与应用的生命周期不同；
- 同一个地址/端口被控制面和多个消费方分别维护，切换为远程时需要在多个文件里重复修改；
- 依赖不可达时，依赖它的组件是否应该启动。

rela-mem 是第一个暴露这些缺口的组件：它自身是独立应用，但依赖 PostgreSQL 与 FalkorDB；拆分前，三者的地址、端口、密码分散在应用 JSON、`.env` 和三合一 Compose 中。

## 目标

1. 用一份纳入 Git 的 `icarus.toml` 统一管理所有可运行组件的运维拓扑：类型、模式、地址、端口、部署方式、健康探测、依赖关系、凭据变量名。
2. 区分 **应用（app）** 与 **中间件（middleware）**，以及 **本地管理（local）** 与 **远程已有（remote）** 两个维度；同一组件可独立选择模式。
3. 统一生命周期动词：`install`、`start`、`stop`、`status`、`logs`；远程组件只保留探测与状态语义。
4. 依赖关系显式化：`required` 依赖不可达时，依赖它的本地组件启动失败；`optional` 依赖不可达只降级告警。
5. 运维配置与业务配置分离：根注册表只拥有运维事实，应用业务配置仍归应用；连接值通过环境变量注入。
6. 应用保持独立：不 import 根控制面代码，不读取根 `icarus.toml`，脱离仓库根仍可用自己的配置文件运行。

## 非目标

- 不把镜像或进程部署到另一台设备；远程一律视为已经存在的服务。
- 不实现镜像仓库推送、远端 Docker 上下文、SSH 编排。
- 不管理 LLM/Embedding 等第三方 API 端点。
- 不在第一阶段把 mem0 自带的 PostgreSQL 迁移到 `infra/`。
- 不登记 `argus`；它尚未具备运行形态，仍属于验证中的例外。
- 不统一应用业务配置文件的格式，也不让控制面生成应用完整配置。
- 不实现数据库协议的完整校验；根控制面不引入 PostgreSQL/Redis/FalkorDB 客户端。
- 不改变现有本地数据目录和持久化路径。

## 术语与组件模型

| 概念 | 取值 | 含义 |
| --- | --- | --- |
| `kind` | `app` / `middleware` | 业务应用 / 数据库与消息等基础中间件 |
| `mode` | `local` / `remote` | 由本仓库管理 / 已有外部服务，只登记与探测 |
| `provisioning` | `compose` / `process` / `embedded` / `foreground` / `bundled` / `none` | 本地组件的实际运行形态 |
| `owner` | 组件 id | `bundled` 或 `embedded` 组件的生命周期归属 |
| `engine` | `postgres` / `falkordb` / `redis` / `mq` / ... | 中间件实现类型；未来扩展点 |
| `default_stack` | `true` / `false` | 无参数 `icarus start` 的默认集合 |
| `requires` | 以组件 id 为键的表 | 依赖边；边上有 `required` 和消费方连接配置 |
| `probe` | `http` / `tcp` | 健康探测方式 |
| `env` / `secret_env` | 公开值 / 目标变量到来源变量名 | 显式 bootstrap 与 Secret 引用，不反向扫描消费方 |
| `workdir` | 仓库相对路径 | 后台原生进程 cwd；foreground 保留调用者 cwd |
| `image_action` | `build`（默认）/ `pull` | Compose install 的明确镜像动作 |
| `endpoint_args` | 长 flag 到依赖组件 id | 仅 foreground，可派生 Gateway WebSocket 参数 |

组件按“实例”登记并全局唯一，不按归属应用命名。同一个中间件被多个应用共享时只登记一次；数据库名、schema、用户名、密码变量放在消费方的依赖边上。

## 单一事实来源

除原有生命周期动词外，提供只读 `icarus config validate` 与 `icarus config show [component]`：分别校验有效拓扑、查看合并后的组件与来源文件。`--config PATH` 支持使用另一份 TOML 和其旁侧 `<stem>.local.toml`，运维相对路径仍以仓库根解析；不会自动回写任何配置。配置输出只显示 Secret 变量引用，不解析值。

- `icarus.toml`：纳入 Git，声明全部组件与默认值，不含任何凭据。
- `icarus.local.toml`：加入 `.gitignore`，本机覆盖层，用于把某些组件切到 `remote` 或填写本机网络地址。
- `.env` / 进程环境：Secret、绝对 `ICARUS_DATA_DIR` 及应用需要的环境设置。注册表的 Secret 字段只引用变量名，不写值；地址/端口拓扑以 TOML 为准。
- 优先级：**命令行 > `icarus.local.toml` > `icarus.toml` > 内置默认值**。
- 命令行覆盖只对本次调用生效，不隐式回写任何文件。
- 加载器对未知字段、未知组件引用、缺失必填字段严格报错；`version` 为未来 schema 演进保留。

YAML 被明确放弃：根控制面保持 stdlib-only，YAML 解析需要 PyYAML，而仓库不为非应用层新增依赖。使用 Python 3.11+ 的 `tomllib`。

## 注册表示例

以下仅展示拓扑字段，省略运行命令等配置，不是可单独启动的完整注册表；实际可运行声明以
[根 icarus.toml](../../../icarus.toml) 为准。bootstrap 值属于 provider 的显式 `env`，
不从第一条 consumer 依赖反向推断。

```toml
version = 1

[defaults]
mode = "local"
default_stack = false

# ---------- 中间件实例 ----------

[components.postgres]
kind = "middleware"
engine = "postgres"
provisioning = "compose"
compose = "infra/postgres/compose.yaml"
image_action = "pull"
shared_network = true
env = { ICARUS_POSTGRES_USER = "rela_mem", ICARUS_POSTGRES_DATABASE = "rela_mem" }
secret_env = { RELA_MEM_PG_PASSWORD = "RELA_MEM_PG_PASSWORD" }
host = "127.0.0.1"          # 宿主机侧探测与宿主机进程消费的地址
port = 15432
network_host = "postgres"   # Compose 网络内消费的地址
network_port = 5432
data_dir = "services/rela-mem/postgres"  # 保留现有数据路径
probe = { type = "tcp" }

[components.falkordb]
kind = "middleware"
engine = "falkordb"
provisioning = "compose"
compose = "infra/falkordb/compose.yaml"
image_action = "pull"
shared_network = true
secret_env = { RELA_MEM_FALKORDB_PASSWORD = "RELA_MEM_FALKORDB_PASSWORD" }
host = "127.0.0.1"
port = 16379
network_host = "falkordb"
network_port = 6379
data_dir = "services/rela-mem/falkordb"
probe = { type = "tcp" }

# ---------- 应用 ----------

[components.gateway]
kind = "app"
provisioning = "process"
default_stack = true
endpoint = "http://127.0.0.1:8765"
workdir = "."
probe = { type = "http", path = "/health", expect_body = { status = "ready" } }
[components.gateway.requires.agent]
required = true

[components.agent]
kind = "app"
provisioning = "embedded"
owner = "gateway"
default_stack = true

[components.tui]
kind = "app"
provisioning = "foreground"
default_stack = true
workdir = "."
endpoint_args = { "--gateway-url" = "gateway" }
[components.tui.requires.gateway]
required = true

[components.webui]
kind = "app"
provisioning = "process"
default_stack = true
endpoint = "http://127.0.0.1:8080"
workdir = "apps/webui"
probe = { type = "http", path = "/health" }

[components.mem0]
kind = "app"
provisioning = "compose"
default_stack = true
endpoint = "http://127.0.0.1:8888"
probe = { type = "http", path = "/docs" }

[components.openkb]
kind = "app"
provisioning = "compose"
default_stack = true
endpoint = "http://127.0.0.1:7566"
probe = { type = "http", path = "/openapi.json", token_env = "ICARUS_OPENKB_API_TOKEN" }

[components.rela-mem]
kind = "app"
provisioning = "compose"
default_stack = true
endpoint = "http://127.0.0.1:8767"
shared_network = true
probe = { type = "http", path = "/healthz" }

# ---------- mem0 自带中间件：第一阶段只登记，不迁移 ----------

[components.mem0-postgres]
kind = "middleware"
engine = "postgres"
provisioning = "bundled"
owner = "mem0"
host = "127.0.0.1"
port = 8432
probe = { type = "tcp" }

# ---------- 依赖边与消费方连接信息 ----------

[components.mem0.requires.mem0-postgres]
required = true

[components.rela-mem.requires.postgres]
required = true

[components.rela-mem.requires.postgres.connection]
host_env = "RELA_MEM_PG_HOST"
port_env = "RELA_MEM_PG_PORT"
database_env = "RELA_MEM_PG_DATABASE"
user_env = "RELA_MEM_PG_USER"
schema_env = "RELA_MEM_PG_SCHEMA"
database = "rela_mem"
user = "rela_mem"
schema = "rela_mem"
password_env = "RELA_MEM_PG_PASSWORD"

[components.rela-mem.requires.falkordb]
required = true

[components.rela-mem.requires.falkordb.connection]
host_env = "RELA_MEM_FALKORDB_HOST"
port_env = "RELA_MEM_FALKORDB_PORT"
graph_env = "RELA_MEM_FALKORDB_GRAPH"
graph = "rela_mem"
password_env = "RELA_MEM_FALKORDB_PASSWORD"

[components.agent.requires.mem0]
required = true
endpoint_env = "ICARUS_MEM0_ENDPOINT"

[components.agent.requires.openkb]
required = true
endpoint_env = "ICARUS_OPENKB_ENDPOINT"

[components.agent.requires.rela-mem]
required = false
endpoint_env = "ICARUS_RELATIONSHIP_ENDPOINT"
```

本机切换到远程中间件时，只新增本地覆盖层：

```toml
# icarus.local.toml（被 .gitignore 忽略）
[components.postgres]
mode = "remote"
host = "<remote-host>"
port = 5432

[components.falkordb]
mode = "remote"
host = "<remote-host>"
port = 6379
```

密码写在 `.env` 的 `RELA_MEM_PG_PASSWORD` / `RELA_MEM_FALKORDB_PASSWORD`，不进入任何被跟踪文件。

实现增加两个可选运维字段，不改变组件语义：`compose_project`（缺省 `icarus-<name>`，显式保留 `mem0-dev`、`openkb-dev` 等既有项目名）和 `expected_containers`（缺省 1）。`gateway` 增加 required 依赖 `agent`，使显式 `icarus start gateway` 时一并满足 embedded Agent 的 required 依赖。

## 生命周期语义

| 动词 | local app | remote app | local middleware | remote middleware |
| --- | --- | --- | --- | --- |
| `install` | 调用应用安装脚本或构建/拉取镜像 | 提示“远程组件无需安装”，成功返回 | 拉取/构建中间件镜像 | 提示并成功返回 |
| `start` | 启动并等待健康 | 只探测，不启动 | 启动容器并等待健康 | 只探测 |
| `stop` | 停止本控制面管理的实例 | 拒绝或明确无操作 | 停止容器，保留数据卷 | 拒绝或明确无操作 |
| `status` | `running` / `starting` / `unhealthy` / `orphaned` / `external` / `stopped` | `reachable` / `unreachable` | `running` / `starting` / `unhealthy` / `stopped` | `reachable` / `unreachable` |
| `logs` | 进程日志或容器日志 | 不提供 | 容器日志 | 不提供 |

- `embedded`（agent）随 owner 启停，状态镜像 owner。
- `foreground`（tui）由 `icarus tui` 或全栈启动在前台运行；`stop tui` 只停止登记实例。
- `bundled` 的启停委托给 owner；owner 为 `remote` 时，bundled 依赖不单独探测，由 owner 的探测覆盖。
- `external` 表示本机地址上已有健康服务但不是当前控制面管理的实例，stop 不向其发信号。
  `orphaned` 仅在 marker、workdir、PID 与 listener 等足以确认当前仓库身份后识别；实现允许显式安全收束并在 start 时清理可确认的孤儿，不能宽泛误杀。身份不确定则保留记录、要求原启动配置。
- 重复 `start` 已就绪的组件返回成功并提示 already running；端口被未知服务占用时失败，不覆盖。

### 地址的两组视图

本地中间件同时存在两个地址视图，注册表必须分别声明：

- `host` / `port`：宿主机侧地址，用于控制面探测，以及 `process`、`foreground`、`embedded` 类型的消费方。
- `network_host` / `network_port`：共享 Compose 网络内的服务别名，用于 `compose` 类型的消费方。

控制面按实际 runtime owner 与网络声明选择地址：仅 local Compose 消费方和 local provider 同时声明
`shared_network = true` 且 `network` 一致时，才使用 provider 的网络视图；其余使用 host/port。
remote provider 始终使用 host/port，不沿用从 base 继承的本地 network_* 别名。

`infra/` 的 Compose 文件与 rela-mem 的 app-only Compose 加入外部网络 `icarus`；不通过 `host.docker.internal` 绕回宿主机。这样 rela-mem 容器内的 `RELA_MEM_PG_HOST` 得到 `postgres`、端口 `5432`，而控制面探测仍使用 `127.0.0.1:15432`。

## 依赖、顺序与失败传播

- 启动顺序按 required 依赖图拓扑排序，foreground 最后；裸 stop 按默认 required 闭包逆序，显式 stop 只归一 named owner。
- 启动某组件前先处理它的依赖：`required` 依赖必须已处于 `running` 或 `reachable`，否则该组件不启动并返回明确错误，错误中带上依赖链。
- `optional` 依赖不可达只记录降级告警，继续启动。
- 一个分支失败不影响无关分支；全栈启动结束后汇总失败并以非零退出码返回。
- 无参数 `icarus start` 的集合是：`default_stack = true` 的组件，加上它们递归的 `required` 依赖。
- 显式后台组件 `icarus start <component>` 同样先满足 required 闭包；`tui` / 显式 `start tui` 只检查目标并打开前台，不隐式启动依赖。

初始 required 设定：

- `mem0` → `mem0-postgres`：required。
- `rela-mem` → `postgres`、`falkordb`：required。
- `agent` → `mem0`、`openkb`：required；`agent` → `rela-mem`：optional（工具型能力，不可用不阻塞 Session）。
- `gateway` → `agent`：required；embedded 组件没有独立进程，但它的 required 依赖必须随 owner 的显式启动一起满足。

`required` 表示“该组件缺少此依赖无法正常工作”，不表示“用户无法打开任何界面”。该区分写入实现和测试。

## 健康探测

- `http`：请求 `endpoint + path`；可配置 `token_env`、`header`、`expect_status`、`expect_body`。响应体只按配置的字段做最小匹配，不解析业务内容。
- `tcp`：只建立 TCP 连接，验证可达性，不验证凭据、数据库名或协议版本。
- 凭据只通过 `token_env` 等变量名引用，注册表不出现 Secret。
- 远程中间件的完整可用性由消费方应用在启动时校验；应用启动失败必须指出是哪个依赖。
- 远程状态使用 `reachable` / `unreachable`，不使用 `healthy`。

## 配置注入与优先级

控制面在启动本地组件时导出规范化环境变量；**不生成、不改写应用配置文件**。

- 环境源优先级是进程 > 选定 dotenv；注册表派生的连接导出再覆盖两者，应用最后用环境覆盖对应 JSON 字段。
  CLI 拓扑覆盖先进入有效注册表，不表示所有应用业务 CLI 具有统一优先级。
- `ICARUS_ENV_FILE` 为控制面选择替代 dotenv，不能据此假定所有 standalone 应用都改变自己的 dotenv 发现规则。
- 对受控组件，注册表派生值覆盖调用者已有的同名环境变量；发生覆盖时控制面输出明确的 warning，列出变量名，避免静默路由到错误服务。
- 依赖边显式声明字段到环境变量名的映射（`host_env`、`port_env`、`database_env`、`user_env`、`schema_env`、`graph_env`、`password_env`），控制面不做命名推断。
- 静态值（如 `database = "rela_mem"`）由控制面写入对应的 `*_env` 变量；`password_env` 只声明变量名，值由应用从 `.env` 或进程环境读取。
- Agent 配置加载器已支持 `ICARUS_MEM0_ENDPOINT`、`ICARUS_OPENKB_ENDPOINT`、`ICARUS_RELATIONSHIP_ENDPOINT` 覆盖；仅修改 endpoint，不改变插件启用和其他业务字段。
- rela-mem 配置加载器已支持 `RELA_MEM_PG_HOST`、`RELA_MEM_PG_PORT`、`RELA_MEM_PG_DATABASE`、`RELA_MEM_PG_USER`、`RELA_MEM_PG_SCHEMA`、`RELA_MEM_FALKORDB_HOST`、`RELA_MEM_FALKORDB_PORT`、`RELA_MEM_FALKORDB_GRAPH` 覆盖；空值不回退，显式 environ mapping 不合并进程/dotenv。
- WebUI 继续使用现有 `ICARUS_MEM0_ENDPOINT`、`ICARUS_OPENKB_ENDPOINT`、`ICARUS_GATEWAY_ENDPOINT`。
- TUI 的 Gateway 地址由控制面从 `gateway.endpoint` 派生为 `ws://.../rpc`，通过 `--gateway-url` 传入。
- Gateway 的监听地址与端口由控制面通过现有 `--host` / `--port` 参数传入。
- 本地中间件的 Compose 通过环境变量接收端口与数据目录，不复制注册表中的值。

## 本地中间件定义

- 仓库自有定义放在 `infra/<id>/compose.yaml`；YAML 仅作为 Compose 实现细节，注册表是地址与端口的唯一事实来源。选择根 `infra/` 是为了中间件的独立所有权与复用，并不是 Compose 无法选择性操作同文件中的服务。
- `infra/` 的 Compose 与 rela-mem app-only Compose 加入共享外部网络 `icarus`；中间件以服务别名（`postgres`、`falkordb`）对该网络的容器消费方可见。
- 数据目录保持现有路径：`$ICARUS_DATA_DIR/services/rela-mem/postgres`、`$ICARUS_DATA_DIR/services/rela-mem/falkordb`。拆出不迁移数据；`stop` 不删除数据。若确认要从空库开始，必须由用户显式清理，控制面不自动删除。
- 第一阶段 `infra/postgres` 只有一个消费方 rela-mem：provider 自己的 `env` 显式声明
  `ICARUS_POSTGRES_USER` / `ICARUS_POSTGRES_DATABASE`，由 Compose 映射为 `POSTGRES_USER` / `POSTGRES_DB`；
  Secret 通过 `secret_env` 引用。**不反向扫描 consumer 边，不按组件名推断变量名。**
  consumer 的角色/数据库声明是独立连接配置，操作者需保持匹配；这些引导值只作用于空数据目录首次初始化，
  不会迁移已有库、创建新角色或旋转密码。远程角色/库须预先存在，多消费方初始化暂不设计。
- FalkorDB 的密码是实例级配置，第一阶段一个实例对应一个密码；不支持每个消费方使用不同密码。共享实例需要多密码时另行设计。
- 依赖边中的 `database` / `user` / `schema` / `graph` 通过对应的 `*_env` 变量进入应用；应用配置加载器必须优先读取这些变量。
- rela-mem 现有的三合一 Compose 在实现阶段拆为应用部分 + `infra/postgres`、`infra/falkordb`；拆分后仍为本地模式，且不改变已有数据目录的语义。
- mem0 的 PostgreSQL 在本阶段保持 `bundled`，不迁移。

## 命令面

保留现有动词优先形式：

```bash
icarus install [component] [--dev]
icarus start [component] [--mode local|remote] [--host HOST] [--port PORT] [--endpoint URL]
icarus stop [component]
icarus status [component]
icarus logs [component]
icarus tui [...]
```

- 组件名来自注册表，未知组件明确报错。
- 覆盖参数只影响本次调用，且必须与显式组件一起使用；无组件的 `icarus start` 只接受转发给 TUI 的参数（保留 `--session-id`、`--gateway-url` 等现有行为）。
- `icarus start` 无组件时按 `default_stack` 与 required 依赖闭包执行，最后前台启动 TUI。
- 显式后台组件 `icarus start <component>` 先启动 required 本地依赖（递归），required 远程依赖不可达时失败。
  `icarus start agent` 报“agent runs inside gateway”；显式 `start tui` 与 `tui` 只检查目标、不启动依赖。
- `icarus stop <component>` 只停止该组件，不停止其依赖，避免误停共享中间件；无参数 `icarus stop` 按 default_stack 与 required 闭包逆序停止。
- `icarus tui` 保持现有 Gateway 就绪检查，不自动启动 Gateway；显式给出 `--gateway-url` 时不检查默认本地 Gateway。
- `icarus status` 无组件时按注册表顺序展示全部组件，包含 mode、地址、状态与降级信息。

## 兼容与迁移

- 第一阶段把 `agent`、`gateway`、`tui`、`webui`、`mem0`、`openkb` 全部登记进 `icarus.toml`，并删除 `BACKGROUND_PROJECTS`、`ENDPOINTS`、`PORTS`、`COMPOSE_PROJECTS`、`COMPOSE_FILES`、`EXPECTED_CONTAINERS` 等硬编码常量。
- 不保留“注册表缺失时回退硬编码”的双轨逻辑。
- **Parity 指单组件行为一致，不是旧测试的精确序列不变。** 现有测试会因 `rela-mem` 进入 `default_stack`、状态词表增加远程状态而更新；更新必须逐条说明是有意的行为变化，不能为了让测试变绿而放宽断言。`external`、`orphaned`、PID 校验、未知端口拒绝等既有语义保留。
- 现有 `install` 不再创建根 `.env` 或自动链接全局命令；用户显式复制模板、按需运行
  `scripts/install-commands.sh`。`install --dev` 是 app-owned 测试环境安装，不隐式构建 Compose 镜像。
- 无参数 `install/start/stop` 的集合由 `default_stack` 与 required 依赖闭包决定；加入 rela-mem 及其本地中间件是有意变更。
- 现有 `.env` 变量名保持兼容；`.example.env` 补充 `RELA_MEM_*` 变量名。
- `argus` 不登记。
- rela-mem 接入根生命周期后，`spec/2026-10-19-agent-relationship-integration.md` 中“不扩展根全栈启动列表”的边界由本设计替代；应用侧的 HTTP 契约不变。
- 当前实现与限制已补充到 [arch.md](arch.md)，以实际代码与测试为准。

## 设计边界与实现决定

下列为原设计边界及实施/审查中形成的实现决定，不表示每项细节都由用户逐条裁决。
涉及实际行为以代码、测试及 execution 记录为准，不能把旧示例当作已执行证据。

### 保留的设计边界

- 根注册表使用 TOML 与 stdlib `tomllib`，不为根层新增依赖。
- 本地中间件使用共享外部 Docker 网络 `icarus`，注册表分别声明宿主机地址与网络地址。
- rela-mem 拆出的 PostgreSQL/FalkorDB 保留现有 `$ICARUS_DATA_DIR/services/rela-mem/...` 数据路径，不自动迁移、不自动清空。
- 第一阶段 `infra/postgres` 只服务 rela-mem 一个消费方；多消费方角色初始化推迟。
- `external`、`orphaned` 与 PID/容器身份校验保留；远程状态使用 `reachable`/`unreachable`。
- 显式后台组件 `icarus start <component>` 自动启动 required 本地依赖；required 远程依赖不可达则失败，前台入口按下述例外处理。
- `icarus tui` 不自动启动 Gateway，保持现有就绪检查。
- 注册表派生环境变量覆盖调用者同名变量，并输出 warning；命令行覆盖优先级最高。
- 无组件的 `icarus start` 只把参数转发给 TUI；`--mode/--host/--port/--endpoint` 必须与显式组件一起使用。
- 旧控制面测试按“单组件行为一致”更新，不保留硬编码回退。

### 实施中落定的配置细节

- bootstrap 使用 provider 显式 `env` / `secret_env`；替代早期“扫描第一条 consumer connection”的示例。
- `workdir` 为原生后台进程声明仓库相对 cwd，marker 路径相对此 cwd；WebUI 使用 `apps/webui`，
  marker 为 `server/index.mjs` / `scripts/start.sh`。foreground 仍保留用户 Workspace。
- `image_action` 明确 pull/build：独立数据库 pull，应用镜像默认 build，不靠 kind 或解析 YAML 猜测。
- `endpoint_args` 显式把前台参数绑定到 declared dependency，不把 TUI/Gateway 名称硬编码在 runtime；
  自动 HTTP(S)→WS(S)、补 `/rpc`，显式 argv 优先。
- `compose_project`、`expected_containers`、argv 命令、`process_markers`、监听 env、共享网络元数据均入注册表。
- 全栈 foreground 最后；显式 `start tui` 与 `tui` 都不自动启动依赖。remote owner 使内部图不透明。
- install 的模板/命令链接和 `--dev` 行为按上文实际实现说明，不宣称旧精确调用序列 parity。

## 验收

以下是验收维度，不是本页自身的通过证明；隔离实际结果见 [execution.md](execution.md)。

- 注册表加载：优先级、深度覆盖、未知字段/未知引用、缺失必填、`version` 校验。
- 行为回归：保留单组件身份/就绪/安全停止语义；默认集合、安装副作用与 `--dev` 的有意变化按上文记录，不要求旧硬编码调用序列不变。
- 依赖传播：required 依赖不可达时依赖组件不启动且错误可读；optional 依赖只告警；无关分支继续。
- 远程模式：纯 TCP 探测、HTTP 探测、带 Token 探测；远程 `install/start/stop` 不触碰本地资源。
- 配置注入：Agent 端点、WebUI 端点、TUI Gateway URL、rela-mem 数据库 host/port 均来自注册表。
- 真实 smoke：本地 rela-mem + 本地两个中间件；本地原生 rela-mem + 同一组隔离数据库登记为 remote。
  已完成的 mixed 测试不等于用户指定远程设备部署验收，不使用用户数据库或用户提供的凭据，不做 benchmark。
- 仓库检查：`git diff --check`；被跟踪文件中不存在明文密码；`icarus.local.toml` 被忽略。

## 风险与边界

- **旧三合一 rela-mem 项目必须由用户手动先停；新旧项目不得同时挂载相同数据库目录。** 无自动 active-mount 碰撞保护，不宣称自动迁移或接管。
- 原生 managed process 的身份/监听与终止实现当前依赖 Linux `/proc`，不声明 macOS/Windows 原生进程管理兼容。
- `required` 过严会让单个远程依赖故障阻断上层应用；初始设定按“组件能否正常工作”标注，后续按实际故障体验调整。
- 环境变量映射依赖应用侧配合；显式映射与契约测试用于防止名称漂移。
- `bundled` 中间件只保证依赖图完整，不提供独立启停；数据迁移仍是后续独立任务。
- 远程中间件只做可达性探测，错误凭据要到应用启动时才暴露；错误信息必须指向具体依赖。
- TOML 严格校验会拒绝未知字段；schema 演进必须提升 `version` 并保持旧版本可读或明确拒绝。
