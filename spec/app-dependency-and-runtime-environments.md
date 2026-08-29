# App Dependency and Runtime Environments

## 目标

Icarus 是多语言 Monorepo。每个 App 独立声明外部依赖、使用独立运行环境，并提供属于自己的安装和
启动脚本；仓库根目录只负责编排，不成为所有应用共享的 Python 包或虚拟环境。

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

scripts/                      根目录跨 App 编排
bin/                          可安装到用户 PATH 的命令转发脚本
Makefile                      统一短命令入口
```

未来 Go、Rust、TypeScript 或其他语言 App 使用各自原生依赖和构建文件，只需把对应脚本接入根目录
编排。

## 当前依赖关系

- Agent 声明模型、配置、Plugin Runtime 和持久化所需依赖。
- Gateway 声明 FastAPI、Uvicorn 和 WebSocket 服务依赖；由于当前进程内加载 AgentRuntime，其安装
  脚本同时安装 Agent 的运行依赖，但不复制依赖声明。
- TUI 声明 Textual、Rich、WebSocket Client 和共享协议所需依赖，不安装 Gateway 或模型 SDK。
- TUI 测试直接构造公共 RuntimeUpdate，不依赖 Agent 内部 Event 或 Agent 的模型 SDK。

## 根目录边界

- `make install` 创建并安装三个独立运行环境。
- `make install` 最后将 `bin/icarus` 与 `bin/icarus-gateway` 链接到用户命令目录；默认
  `~/.local/bin`，可由 `ICARUS_BIN_DIR` 覆盖。
- `make install-commands` 只安装命令入口，不修改任何 App 依赖环境。
- `make install-dev` 安装三个 App 各自的测试环境。
- `make start` 启动 Gateway，再以前台方式启动 TUI，并在 TUI 退出时收束它启动的 Gateway。
- `make test` 分别使用各 App 的 `.venv` 运行对应测试。
- 根目录不保存聚合 requirements、Python 虚拟环境或 Python 发布包。
- 根脚本不得改变调用者当前目录的 Workspace 语义。

## 非目标

- 不修改现有 `apps.*` 和 `packages.*` Python import；
- 不引入 uv workspace；
- 不把当前源码拆成多个独立 Python distribution；
- 不要求 Agent 提供独立进程启动脚本。
