# App Dependency and Runtime Environments

## 目标

Icarus 是多语言 Monorepo。每个 App 独立声明外部依赖、使用独立运行环境，并提供属于自己的安装和
底层运行脚本；仓库根目录只提供统一控制面，不成为所有应用共享的 Python 包、虚拟环境或业务 App。

## 目录约定

```text
apps/<app>/
├── requirements.txt          Python App 的直接运行依赖
├── requirements-dev.txt      当前 App 的运行与测试依赖
├── .venv/                    当前 App 独立虚拟环境
└── scripts/
    ├── install.sh            创建 .venv 并安装依赖
    ├── start.sh              仅独立进程 App 提供
    └── test.sh               运行当前 App 测试

scripts/icarus/               根目录跨 App 生命周期控制实现
bin/                          可安装到用户 PATH 的命令转发脚本
Makefile                      统一短命令入口
```

未来 Go、Rust、TypeScript 或其他语言 App 使用各自原生依赖和构建文件，只需把对应脚本接入根目录
编排。

`icarus` CLI 是仓库级状态和生命周期管理工具，不属于 `apps/`，也不持有业务能力。`apps/tui` 仍然
是独立的终端客户端；两者不能因为命令名称相同而合并职责。

## 当前依赖关系

- Agent 声明模型、配置、Plugin Runtime 和持久化所需依赖。
- Gateway 声明 FastAPI、Uvicorn 和 WebSocket 服务依赖；由于当前进程内加载 AgentRuntime，其安装
  脚本同时安装 Agent 的运行依赖，但不复制依赖声明。
- TUI 声明 Textual、Rich、WebSocket Client 和共享协议所需依赖，不安装 Gateway 或模型 SDK。
- TUI 测试直接构造公共 RuntimeUpdate，不依赖 Agent 内部 Event 或 Agent 的模型 SDK。
- Mem0 和 OpenKB 使用各自源码目录中的 Dockerfile、Compose 与依赖声明构建镜像，不向 Agent、
  Gateway 或仓库根 Python 环境安装服务端依赖。

## 项目模型

根控制面管理以下项目：

| 项目 | 形态 | 安装边界 | 生命周期 |
| --- | --- | --- | --- |
| `agent` | Gateway 进程内运行库 | `apps/agent/.venv` | 随 Gateway 启停，不提供独立 start/stop |
| `gateway` | 后台进程 | `apps/gateway/.venv`，组合安装 Agent 运行依赖 | 可独立启动、停止和查看状态 |
| `tui` | 前台交互进程 | `apps/tui/.venv` | 通过 `icarus tui` 启动，可查看和停止已登记实例 |
| `mem0` | Docker Compose 服务组 | `apps/mem0` 镜像 | 可独立启动、停止和查看状态 |
| `openkb` | Docker Compose 服务 | `apps/openkb` 镜像 | 可独立启动、停止和查看状态 |

用户若尝试启动或停止 `agent`，CLI 必须说明它随 `gateway` 运行，而不能伪造一个独立 Agent 进程。

## 用户命令面

`bin/icarus` 是唯一推荐的用户入口。首次安装前从仓库执行 `./bin/icarus install`，命令链接安装后
可以在任意 Workspace 使用：

```bash
icarus install
icarus install --dev

icarus start
icarus start mem0
icarus start openkb
icarus start gateway

icarus tui
icarus tui --session-id my-session

icarus stop
icarus stop mem0
icarus stop openkb
icarus stop gateway
icarus stop tui

icarus status
icarus status mem0
```

`icarus start` 按 `mem0 -> openkb -> gateway -> tui` 启动完整能力。前三项在后台运行并逐项等待就绪，
TUI 在调用终端前台运行。TUI 正常退出不自动停止后台项目；用户通过 `icarus stop` 明确关闭全部。

`icarus tui` 只启动 TUI，不隐式启动 Gateway 或外部服务。Gateway 不可用时给出
`icarus start gateway` 或 `icarus start` 的明确提示。`icarus start tui` 可以作为等价兼容入口。

无子命令的旧形式 `icarus --session-id ...` 暂时继续转发给 TUI，文档统一推荐
`icarus tui --session-id ...`。`icarus-gateway` 暂时保留为可传监听参数的前台调试入口；常规后台
运行统一使用 `icarus start gateway`。

## 安装语义

`icarus install` 依次调用各 App 自己的安装脚本：

```text
apps/agent/scripts/install.sh
apps/gateway/scripts/install.sh
apps/tui/scripts/install.sh
```

每个 Python App 继续把依赖安装到自己的 `.venv`。根目录不创建 `.venv`，也不聚合 requirements。
`--dev` 同样透传到每个 App。安装流程还会安装命令链接、检查 Docker 与 Docker Compose 是否可用，
但不替用户安装系统软件、不启动服务，也不要求安装阶段已经填写所有运行 Secret。Mem0 和 OpenKB
的依赖由各自 Compose 在安装阶段构建到独立镜像中。

根 `.env` 缺失时，安装流程可以从 `.example.env` 创建空模板，但不得覆盖已有 `.env`。服务启动时
继续由各服务适配器校验实际所需变量并给出见名知意的错误。

## 生命周期与状态

- Mem0 与 OpenKB 继续通过各自的 `icarus-compose` 适配器调用 Docker Compose。`stop` 只停止并移除
  容器，不删除 `$ICARUS_DATA_DIR/services` 中的数据。
- Gateway 由根控制面后台启动。PID 与日志存放在 `$ICARUS_DATA_DIR/runtime` 和
  `$ICARUS_DATA_DIR/logs`；停止前必须校验 PID 对应 Icarus Gateway，不能使用宽泛 `pkill`。
- 每个由 `icarus tui` 启动的 TUI 实例登记独立 PID。`icarus stop tui` 只停止仍可校验为 Icarus TUI
  的登记实例，允许多个 Workspace/Session 同时运行。
- `icarus stop` 按 `tui -> gateway -> openkb -> mem0` 逆序收束。项目未运行不视为错误，也不删除
  Session、Memory 或 Knowledge 数据。
- `icarus status [project]` 同时使用受控 PID、Compose 状态和健康端点判断状态。端口可访问但不是由
  当前 Icarus 控制面启动时，应显示为 `external`，不能误报为本地受管进程，也不能在 stop 时杀掉。
- 状态至少区分 `running`、`starting`、`unhealthy`、`external` 和 `stopped`，并展示可用端点或日志路径。
- 重复 start 已健康的项目返回成功并显示 `already running`；已占用端口但健康检查不匹配时应失败，
  不能覆盖或停止未知进程。

健康端点为：

| 项目 | 就绪检查 |
| --- | --- |
| Mem0 | `http://127.0.0.1:8888/docs`，并结合 Compose 服务状态 |
| OpenKB | `http://127.0.0.1:7566/openapi.json` |
| Gateway | `http://127.0.0.1:8765/health` |
| TUI | PID 与进程身份，不使用网络健康检查 |

## 根目录边界

- `icarus install` 创建并安装三个独立运行环境。
- `icarus install` 最后将 `bin/icarus` 与 `bin/icarus-gateway` 链接到用户命令目录；默认
  `~/.local/bin`，可由 `ICARUS_BIN_DIR` 覆盖。
- `make install-commands` 只安装命令入口，不修改任何 App 依赖环境。
- `make install-dev` 安装三个 App 各自的测试环境。
- Makefile 是开发与 CI 的兼容快捷入口，安装和生命周期命令转发给同一套根控制面，不能复制一份
  启停逻辑。
- `make test` 分别使用各 App 的 `.venv` 运行对应测试。
- 根目录不保存聚合 requirements、Python 虚拟环境或 Python 发布包。
- 根脚本不得改变调用者当前目录的 Workspace 语义。

## 非目标

- 不修改现有 `apps.*` 和 `packages.*` Python import；
- 不新增 `apps/cli`；
- 不引入 uv workspace；
- 不把当前源码拆成多个独立 Python distribution；
- 不要求 Agent 提供独立进程启动脚本。
