# Argus 基础骨架设计

> 状态：基础骨架已实现；本文保留实施前设计与范围。当前架构以源代码和测试为准，尚未编写对应 `arch.md`。

## 目标与边界

Argus 是独立于 Agent 的外部信息观测与触发应用。当前先让它作为 Icarus 生态中的一个可安装、可启动、可观测、可停止、可测试的后台进程存在，并留下已商定的职责位置。QQ bot 是后续首个真实验证源，但本阶段不连接 QQ；也不迁移 Windows 上的 demo。

本阶段不实现外部消息、业务对象、持久化表、Jev 判定、策略规则、Icarus session 调用或 mem0/openkb SDK。`apps/agent` 的现有 adapter 与配置不改。保留现有工作区中与本任务无关的改动和备份文件。

## 层次与目录

在 `apps/argus/src/` 建立以下包目录（目录通过 `__init__.py` 进入版本控制，但不放假实现）：

- `connector/`：各外部应用连接及监听；未来按应用分别实现。
- `handler/`：将来源原始数据转为业务对象。
- `entity/`：通用业务类及应用特化类；当前不猜字段，不另建同义的 `type/`。
- `service/`：编排持久化、上下文、策略、判断和 Icarus 操作。
- `strategy/`：独立的复杂业务判断；只表达是否对话及未来的延续/新建意图，不持有具体 session ID。
- `dao/`：持久化访问与增删改查，不包含业务裁决。
- `config/`：非机密配置的加载与校验。
- `utils/`：未来确有复用需求的辅助代码；当前仅留目录。

`src/main.py` 是进程入口；`src/queue.py` 和 `src/worker.py` 分别拥有入站队列和 worker 生命周期。本阶段没有具体消息类型或处理器，worker 只支持启动及正常停机；不消费伪造的业务事件。`apps/argus/test/` 镜像本阶段实际存在的运行代码。

后续完整数据流的约定是 `connector → handler → queue → worker → service → dao/strategy → Icarus`。这个约定不意味着本阶段已实现这条链路。

## 启动、配置与日志

- 遵循现有应用约定：`requirements.txt`、`requirements-dev.txt`（包含运行依赖与 pytest/httpx）、`scripts/install.sh`、`scripts/start.sh`、`scripts/test.sh`；各自使用 `apps/argus/.venv`，`start.sh` 设置仓库根 `PYTHONPATH` 并运行 `python -m apps.argus.src.main`。
- 将无机密默认配置放在 `apps/argus/settings.json`，由 `src/config/` 统一读取并校验：监听地址默认 `127.0.0.1`、端口暂定 `8766`、日志级别、队列容量与 worker 数量（第一阶段固定一个 worker）。不在 JSON 或源码里存 token；目前没有外部连接，无须新增凭据变量。复用 `packages.runtime_environment` 加载根 `.env`，使用已有 `ICARUS_DATA_DIR` 作为 Icarus 生命周期记录和日志的位置，不创建第二套数据根目录。
- `main.py` 装配一个最小的本机 HTTP 服务；启动时初始化有界队列和 worker，停止时取消/等待 worker 并释放资源。仅提供 `GET /health`，在服务已启动且 worker 存活时返回 `{"status": "ready"}`；未就绪或停止时不应宣称 ready。没有消息注入、模拟平台或对外业务接口。默认只绑定 loopback。
- 入口配置 Python `logging`；后台启动时的 stdout/stderr 由统一控制命令写入 `$ICARUS_DATA_DIR/logs/argus.log`。不记录 secret 或原始聊天内容。配置错误导致启动失败并在日志中报告，不静默采用错误配置。

## 纳入统一生命周期

在 `scripts/icarus/main.py` 将 Argus 注册为后台 Python App：`icarus install argus [--dev]`、`icarus start argus`、`icarus status argus`、`icarus stop argus`，并加入无参数的全栈 install/start/stop/status。全栈启动顺序是 `mem0 → openkb → gateway → argus → tui`；停止时先停止 `argus`，再停止 `gateway` 和其依赖服务。

沿用 Gateway 的进程管理模式：通过单独的运行目录记录 PID、进程身份和启动时间；启动前检查端口、启动后轮询 `/health`；停止时只终止确认为本仓库管理的进程组，不按端口或名字广泛杀进程。外部健康服务不由 Icarus 停止。与 Gateway 并列分支处理 Python 后台进程，不把 Argus 错误当作 Docker Compose 服务。将安装和测试脚本 `scripts/install.sh`、`scripts/test.sh`、Makefile 的便捷入口更新为包含 Argus；更新仓库 README 的应用列表与使用说明，不声称 QQ 或自主观测已经上线。

## 验证与非目标

- `apps/argus/test/`：配置校验、健康检查、worker/队列启动及正常停机；测试不访问真实 QQ、Jev 或 Icarus Gateway。
- `scripts/tests/test_icarus_control.py`：全栈启动/停止顺序、Argus 独立安装和生命周期、健康状态、进程记录验证、外部服务不被误停。
- 验证顺序：受影响的最小测试 → `apps/argus/scripts/test.sh` 与控制命令测试 → 受影响的其它应用测试 → `compileall` 和 `git diff --check`。如依赖与配置可用，再单独运行 `icarus start argus` / `status argus` / `stop argus` 的本地冒烟；不会在测试中启动真实外部平台。

本阶段不在 `packages/` 增加 mem0/openkb SDK；该工作单独设计和实施。不规定未来 MQ 的持久性、重试/幂等、业务 entity 字段、QQ 接入协议、Jev 提示词或具体 session 恢复语义。
