# Argus 基础骨架实施计划

> 执行记录（2026-09-29）：四项任务均已完成并经测试验证；下方复选框保留实施前步骤原文，不再表示尚未执行。统一控制命令的端口探测已在代码评审后改为读取 Argus 的 `settings.json`，取代下文第三项里的固定 8766 端口方案。当前架构以代码和测试为准；未提交任何文件。

> **供执行者：**按任务逐项实施；推荐用 `superpowers:executing-plans` 在当前会话原生执行，或经用户选择用 `superpowers:subagent-driven-development`。每项按测试先行推进。

**目标：**为 `apps/argus` 建立可安装、可启动、可健康检查、可停止及可测试的独立后台 Python App，并纳入 Icarus 统一生命周期；不实现 QQ、Jev、mem0/openkb SDK 或任何业务消息。

**架构：**Argus 使用自己的 `.venv` 和仓库根 `PYTHONPATH`；入口读取严格校验的非机密配置，启动一个有界 asyncio 队列、一个 worker 和只绑定本机的 HTTP 健康端点。仓库控制命令将它视为 Gateway 同类的后台 Python 进程，不把它当 Docker Compose 服务。业务层只创建包路径，不预先定义模型、策略或空实现。

**技术栈：**Python ≥3.11，FastAPI、uvicorn、pydantic、python-dotenv、pytest、httpx；标准库 asyncio/logging；根控制命令使用 stdlib。

**设计：**`apps/argus/docs/spec/2026-09-29-argus-scaffold/design.md`。执行前阅读设计和 `AGENTS.md`。

## 全局约束

- 当前分支 `feat/argus`；不新建分支/工作树、不提交或推送，除非用户另行明确要求。保留 `apps/agent/settings.json` 和两个未跟踪备份文件的现状。
- 目录标准拼写 `connector`，不使用 `connecter`；只建 `entity`，不重复建 `type`。
- 只建实需运行代码与包路径：`connector/handler/service/strategy/dao/entity/config/utils`；不要加入虚假的 QQ 连接器、消息、策略、DAO 表或示例数据。
- 本次不修改 `apps/agent`；不把 Windows demo 的源码直接迁移；不实现 SDK、Jev 或 Gateway 会话调用。
- `ICARUS_DATA_DIR` 使用已有 `packages.runtime_environment.get_icarus_data_dir()`，不另设 Argus 数据根目录；默认 HTTP 绑定 `127.0.0.1:8766`，不对公网开放。
- 测试采用 pytest 功能测试与原生 assert；不依赖 Docker、QQ、Gateway 或密钥；不顺带修改已有失败。

## 文件地图

- `apps/argus/{__init__.py,settings.json,requirements.txt,requirements-dev.txt,README.md}`：应用包、可配置的非机密运行参数、依赖和使用说明。
- `apps/argus/scripts/{install.sh,start.sh,test.sh}`：复用 Agent/Gateway 脚本约定，但 `install.sh` 只安装 Argus 依赖。
- `apps/argus/src/{__init__.py,main.py,queue.py,worker.py}`：入口、受控队列、worker 生命周期。
- `apps/argus/src/config/{__init__.py,config_model.py,config_loader.py}`：配置模型与加载，运行时唯一读配置的位置。
- `apps/argus/src/{connector,handler,service,strategy,dao,entity,utils}/__init__.py`：保留稳定的目录职责，当前不填业务实现。
- `apps/argus/test/{__init__.py,test_main.py,test_queue.py,test_worker.py,config/test_config.py}`：镜像本阶段可运行代码；`config/__init__.py`。
- `scripts/icarus/main.py`、`scripts/tests/test_icarus_control.py`：统一控制的后台项目表、安装/启动/状态/停止、安全进程识别与测试。
- `scripts/install.sh`、`scripts/test.sh`、`Makefile`、根 `README.md`：全栈编排、开发入口与准确说明。

## 重点测试的五类失效输入

1. 错误配置：端口越界、队列容量/worker 数非正或非有限，必须在启动前拒绝；任务 1。
2. 运行时 worker 已退出但 HTTP 仍在响应：`/health` 不得返回 ready；任务 2。
3. queue 容量已满：`put_nowait` 明确抛出 `asyncio.QueueFull`，绝不静默丢消息；任务 2。
4. Argus PID 记录指向已结束或其他进程：status 不误报 running、stop 不终止别的进程；任务 3。
5. 默认全栈启停时 Gateway 与 Argus 顺序错误，或外部健康服务被误停；任务 3。

---

### Task 1: Argus 配置与独立安装测试环境

**文件：**创建 `apps/argus/__init__.py`、`settings.json`、`requirements*.txt`、`scripts/{install,start,test}.sh`、`src/__init__.py`、`src/config/{__init__,config_model,config_loader}.py`、`test/{__init__,config/__init__,config/test_config}.py`；创建七个仅含职责 docstring 的业务包 `__init__.py`。`start.sh` 在任务 2 添加的 `main.py` 完成之前尚不能启动。

**接口：**`ArgusConfig(BaseModel)` 提供 `host: str`、`port: int`、`log_level: str`、`queue_capacity: int`、`worker_count: int`；`load_config() -> ArgusConfig` 从 `importlib.resources.files('apps.argus').joinpath('settings.json')` 读取并校验。此阶段配置仅用于本机运行，不包括 Icarus 地址或外部凭据。

- [ ] 测试先行：在 `test/config/test_config.py` 写 `test_default_config`（读取仓库中的配置，断言 host、port、容量、worker_count），以及 `test_rejects_invalid_runtime_values`（参数化 `port=0/65536`、容量 `0`、worker `0/2`、空 host、未知配置键；均应抛 `ValidationError`）。用 `apps/gateway/.venv/bin/python -m pytest apps/argus/test/config -q` 先观察因新包/配置缺失而失败；没有此解释器时先执行后面的安装脚本，再跑同样的红灯测试。
- [ ] 实现：`ArgusConfig` 用 `ConfigDict(extra='forbid')`；`port: int = Field(ge=1, le=65535)`、`queue_capacity: int = Field(gt=0)`、`worker_count: Literal[1] = 1`、`host: Literal['127.0.0.1'] = '127.0.0.1'`；`log_level` 用 Literal 限制为 `DEBUG/INFO/WARNING/ERROR/CRITICAL`。`load_config` 调用 `load_icarus_environment()`，只读 `settings.json`，不把未声明的 env 覆盖写入配置。JSON 初始值 `{"host":"127.0.0.1","port":8766,"log_level":"INFO","queue_capacity":1000,"worker_count":1}`。不要把未确定的业务配置加进 JSON。
- [ ] `requirements.txt` 声明 `fastapi`、`uvicorn`、`pydantic`、`python-dotenv`；`requirements-dev.txt` 以 `-r requirements.txt` 开始，再加入 `pytest`、`httpx`。安装脚本按 `apps/agent/scripts/install.sh` 约定创建私有 venv，支持无参数及 `--dev`，拒绝额外参数。启动脚本按 `apps/gateway/scripts/start.sh` 约定设置 `PYTHONPATH` 和 `python -m apps.argus.src.main "$@"`；测试脚本运行 Argus pytest 与 `compileall -q apps/argus/src apps/argus/test packages`。脚本赋可执行位。
- [ ] 对尚无业务实现的七个包只写 `__init__.py` 的单句模块说明。用 `./apps/argus/scripts/install.sh --dev` 创建环境，再运行 `./apps/argus/scripts/test.sh`；此时仅配置测试，要求通过。安装仅为本地 `.venv`（gitignored），不要启动任何服务。

### Task 2: 启动、队列、worker 与健康检查

**文件：**创建 `apps/argus/src/{queue.py,worker.py,main.py}`，`apps/argus/test/{test_queue.py,test_worker.py,test_main.py}`；从任务 1 导入 `load_config` 和 `ArgusConfig`。

**接口：**`create_app(config: ArgusConfig | None = None) -> FastAPI` 暴露 GET `/health`；`main(argv: list[str] | None = None) -> None` 启动 uvicorn。`InboundQueue` 封装 `asyncio.Queue`，提供 `put_nowait(item: object) -> None`、`get() -> object`、`task_done() -> None`；`QueueWorker.start() -> None` / `stop() -> None` 管理唯一消费 task；本阶段队列没有生产者，worker 等待消息，不触发任何业务逻辑。明确不把 queue 暴露成接受 HTTP 任意对象的接口。

- [ ] 在 `test_queue.py` 写 `test_full_queue_raises`：容量 1，放入一个 `object()`，第二次 `put_nowait` 抛 `QueueFull`，取出的仍是第一个对象。先运行 `apps/argus/.venv/bin/python -m pytest apps/argus/test/test_queue.py -q`，确认缺少实现而失败。
- [ ] 实现 `InboundQueue(capacity: int)`：基于 `asyncio.Queue(maxsize=capacity)` 的最薄封装；不补推送、去重、持久化或空队列轮询。运行队列测试变绿。
- [ ] 在 `test_worker.py` 写异步测试（可使用 `asyncio.run`，不加 pytest-asyncio）验证 `start()` 后 `is_running` 为 true，`stop()` 后 false；连续两次 `stop()` 不抛异常；对持有的 task 执行 `cancel()` 后 `is_running` 为 false。红灯后实现 `QueueWorker`：内部 task 循环 `await queue.get()` 后 `task_done()`；当前不存在可合法入队的业务事件，不测试伪造 payload 的成功处理。`stop()` cancel task 并 `await asyncio.gather(task, return_exceptions=True)`；不得吞掉外部调用者的取消。实现后运行该文件变绿。
- [ ] 在 `test_main.py` 写 `TestClient` 生命周期测试：lifespan 内 `/health` 返回精确 `{"status":"ready"}`；context 退出后对同一 app 的诊断（可直接调用独立的状态函数，避免尝试请求已关闭的 TestClient）不再是 ready；手动取消 app 的 worker task 后，健康端点返回 `503`，绝不假装 ready。红灯后实现 `create_app`：FastAPI `lifespan` 构造 `InboundQueue`、启动 `QueueWorker`，退出停止；`/health` 检查 worker.is_running，成功精确返回 `{"status":"ready"}`，否则抛 `HTTPException(503)`。如果尚未进入 lifespan，也返回 503。`main` 用 `argparse` 保持无业务参数，加载配置、在入口配置 logging，然后 `uvicorn.run(create_app(config), host=config.host, port=config.port)`。运行 test_main 及全部 Argus 测试变绿。
- [ ] 不启动真实服务做自动测试；运行 `./apps/argus/scripts/test.sh` 与 `git diff --check`。若要本机冒烟，用私有端口且确保结束进程，不读取真实聊天数据。

### Task 3: 纳入 Icarus 进程控制

**文件：**修改 `scripts/icarus/main.py`、`scripts/tests/test_icarus_control.py`、`scripts/install.sh`、`scripts/test.sh`、`Makefile`。复用 `_require_data_dir`、`_write_process_record`、`_record_matches`、`_terminate_record`、`_wait_until_healthy`、`_healthy`，不复制整个 CLI 或改 Compose 实现。

**接口：**`icarus install/start/stop/status argus`；总启动顺序 `mem0,openkb,gateway,argus,tui`；总停止顺序 `tui,argus,gateway,openkb,mem0`；`$ICARUS_DATA_DIR/runtime/argus.json` 与 `$ICARUS_DATA_DIR/logs/argus.log` 只记录由统一控制命令启动的进程。

- [ ] 在控制测试的临时仓库 fixture 增加 `apps/argus/scripts`；修改 `test_start_all_orders_dependencies_then_opens_tui` 预期，新增停止顺序测试（替换 `stop_project` 捕获名称），新增单独 `install argus --dev` 测试（应调用 Argus 私有 install.sh，无 Docker），新增 `start argus` 与 `stop argus` 测试（mock `Popen`/进程记录，验证 cwd、log、start_new_session、日志目录/路径）；先运行 `python3 scripts/tests/test_icarus_control.py` 看红灯。
- [ ] 增加安全边界测试：`_record_matches` 遇到不符 marker 或启动时间应为 false；Argus record 丢失/错配时，`stop argus` 不对任意 PID 发信号；健康但无 Argus record 的服务报 `external`，stop 保留它。端口已有其他进程但健康探测失败时拒绝启动。用 stub `_healthy`、`_port_open`、`_read_record`、`_terminate_record`；不实际杀进程。红灯后再实现。
- [ ] 将 `argus` 加到 `BACKGROUND_PROJECTS`、`KNOWN_PROJECTS`、`STATUS_ORDER`、安装 Python 项目分支、`ENDPOINTS` (`http://127.0.0.1:8766/health`)、`PORTS` (`8766`)、`PROCESS_MARKERS` (`apps.argus.src.main` 与脚本) 及 help。`start_background` 对 `argus` 调用新的 `_start_argus`：检验私有 venv、`ICARUS_DATA_DIR`，`Popen` 脚本并记录 PID/进程组，写 log；复用 gateway 同型启动逻辑时仅提取两个应用的**真实公共部分**，不要修改 gateway 孤儿进程语义。`project_status('argus')` 用现有 record + 健康规则，记录失效而健康时为 `external`；`_stop_argus` 只终止匹配的托管 record、清理自己的 record，外部进程不动。`_wait_until_healthy` 在 Argus 进程先退出时及时失败（不能干等 180 秒）。`_healthy` 对 gateway 与 argus 均要求精确 JSON `{"status":"ready"}`，不能只看 200。
- [ ] 修改 `scripts/install.sh` 与 `scripts/test.sh` 加入 Argus，Makefile 加 `install-argus`、`test-argus` 等最薄便捷入口，保留原命令行为。运行控制测试、`apps/argus/scripts/test.sh`；最后执行 `make test-gateway` 检查同为后台项目的 Gateway 未回归。别运行 `icarus start` 全栈（会拉 Docker、TUI）。

### Task 4: 文档、运行验证与收尾

**文件：**创建 `apps/argus/README.md`；修改根 `README.md` 的结构、安装、启动与当前边界；更新本计划勾选状态（仅在实际执行各步后）；不把设计稿误写成当前架构。

- [ ] Argus README 明确当前只有本机健康端点、空队列/worker 和统一控制接口；说明 `./bin/icarus install argus --dev`、`start/status/stop argus`；说明 QQ、Jev、DAO/策略、mem0/openkb SDK 尚未实现。根 README 同步上述实际能力，不改产品方向。
- [ ] 全量检查：运行 `./apps/argus/scripts/test.sh`、`python3 scripts/tests/test_icarus_control.py`、`make test-agent`、`make test-gateway`、`make test-tui`、`python3 -m compileall -q apps/argus/src apps/argus/test scripts/icarus`、`git diff --check`。报告每个结果；对于仓库原有 TUI 失败，对比具体失败集合，勿修无关失败。
- [ ] 可用时做控制命令本机冒烟：先检查 `icarus status argus` 与 `127.0.0.1:8766` 未被占用，再 `./bin/icarus start argus` → `./bin/icarus status argus` → `./bin/icarus stop argus`；用 `curl -f http://127.0.0.1:8766/health` 验证精确 JSON。若端口已有服务，不覆盖/停止它，而报告跳过原因。确认 Argus 日志与 PID 记录位于指定 `ICARUS_DATA_DIR`；不展示 .env 里的值。
- [ ] 最终 `git status --short`，区分本任务文件和预存的三个未改动项；不 git add/commit/push。

## 执行提示

任务 1、2、3 共享接口，推荐顺序执行。评审焦点：健康检查必须反映 worker 生命周期而非仅 HTTP 存活；Argus 的后台记录不能误停止其它进程；没有来源消息时 worker 不应写入任何业务状态。
