# WebUI 生命周期接入架构

2026-10-04，依据当前 `apps/webui` 脚本、`scripts/icarus/main.py` 与对应测试整理。

## 当前实现

WebUI 生产入口是 `node server/index.mjs`。进程从环境读取 `ICARUS_WEBUI_HOST`（默认
`127.0.0.1`）、`ICARUS_WEBUI_PORT`（默认 `8080`）、`ICARUS_WEBUI_ORIGIN`、
`ICARUS_WEBUI_USER`、`ICARUS_WEBUI_PASSWORD`，以及三个后端地址
`ICARUS_MEM0_ENDPOINT`、`ICARUS_OPENKB_ENDPOINT`、`ICARUS_GATEWAY_ENDPOINT` 和
`ICARUS_MEM0_API_KEY`、`ICARUS_OPENKB_API_TOKEN`。非回环 host 未同时提供用户名与密码时，
入口进程直接拒绝启动。`GET /health` 返回 200 文本 `ok`，不校验认证。

构建依赖 pnpm：`apps/webui/package.json` 通过 `packageManager` 锁定 `pnpm@10.32.1`，
`corepack pnpm` 在该应用目录内解析到 10.32.1。构建输出为 `apps/shell/dist/`，
`server/index.mjs` 会在启动时 `realpath` 该目录，缺失则进程失败。

仓库控制面 `scripts/icarus/main.py` 此前只有 Gateway 以原生后台进程方式运行：`mem0`、
`openkb` 走 Docker Compose，`gateway` 通过 `apps/gateway/scripts/start.sh` 启动并写入
`${ICARUS_DATA_DIR}/runtime/gateway.json` 进程记录，`tui` 作为前台进程管理。进程身份由
记录中的 PID、命令行 marker 与 `ps lstart` 起始时间共同校验，状态机区分
running / starting / unhealthy / orphaned / external / stopped。

## 本次变化

WebUI 作为第四个后台项目接入控制面，不使用 Docker、不写 Compose。

- `apps/webui/scripts/install.sh`：校验 Node ≥ 22.12（`node -e` 判断主次版本）与
  `corepack`，缺失时退出 1；只接受空参数或 `--dev`（二者行为相同），其他参数退出 2；
  随后在应用目录执行 `corepack pnpm install --frozen-lockfile` 与 `corepack pnpm build`。
- `apps/webui/scripts/start.sh` + `apps/webui/scripts/icarus_start.py`：`start.sh` 只做
  Python 启动器包装；`icarus_start.py` 仅用标准库，读取仓库根 `.env`（复用
  `scripts/icarus/environment.read_env_file`），把 `ICARUS_WEBUI_*` 与三个后端
  endpoint／key 合并进进程环境（进程环境优先于 `.env`），校验非回环 host 必须同时提供
  用户名与密码、构建产物存在，最后在 `apps/webui` 目录 `os.execvpe("node", ["node",
  "server/index.mjs"])`。
- `scripts/icarus/main.py`：把 Gateway 专用的进程管理泛化为按项目参数化的实现
  （`_spawn_process` / `_stop_process` / `_orphaned_process_records` /
  `_replace_orphaned_process` / `_project_log_detail` / `_process_record_path`），
  Gateway 相关方法保留原有名称与行为。新增 `webui`：
  `BACKGROUND_PROJECTS=("mem0","openkb","gateway","webui")`，
  `STATUS_ORDER=("mem0","openkb","gateway","agent","webui","tui")`，
  `ENDPOINTS["webui"]="http://127.0.0.1:8080/health"`、`PORTS["webui"]=8080`、
  `PROCESS_MARKERS["webui"]=("apps/webui/server/index.mjs","apps/webui/scripts/start.sh")`。
  `install` 让 webui 走本地应用安装分支（支持 `--dev` 透传）；`start` 前置检查 `node`
  可用与 `apps/webui/apps/shell/dist/index.html` 存在；`stop` 整体顺序改为
  `tui → webui → gateway → openkb → mem0`；`status` 复用进程项目状态机。
- `.example.env` 增加 `ICARUS_WEBUI_HOST/PORT/USER/PASSWORD` 与三个 endpoint 的可选覆盖
  注释；`Makefile` 增加 `webui-up` / `webui-down` / `webui-logs`。

## 进程身份与边界

WebUI 的 `start.sh` 不 `exec` Python，而是运行启动器子进程，使受管进程的命令行保持为
`bash apps/webui/scripts/start.sh`，其 marker 全程稳定。启动器再把 Python 替换为 Node，
因此实际监听 8080 的是 Node 子进程。台账记录的是包装进程：`_record_matches` 命中
`start.sh` marker，`_terminate_record` 以进程组方式回收 Node。进程记录写入
`${ICARUS_DATA_DIR}/runtime/webui.json`，日志写入 `${ICARUS_DATA_DIR}/logs/webui.log`。

`_record_matches` 新增两处泛化：命令行 token 按进程工作目录解析后可与 marker 相对路径
比对（Node 以 `node server/index.mjs` 启动时仍能识别）；`ps lstart=` 的起始时间允许
≤5 秒差异，避免同一 PID 因秒级截断被误判为 orphaned。PID 合法性、marker 匹配与 5 秒容差
之外的行为保持不变。

错误与边界：`node` 缺失或构建产物缺失时 `icarus start webui` 抛 `ControlError` 并提示
`icarus install webui`；非回环 host 缺账号时启动器以退出码 1 报错；端口 8080 被非本仓库
进程占用时拒绝启动；外部已有健康服务保持不动并报告 external。WebUI 仍要求 Node 与
`corepack` 可用，`icarus install webui` 不代管 Node 安装。

## 验证

本轮实际运行：

- `python3 scripts/tests/test_icarus_control.py`：29 项通过。
- `apps/agent/.venv/bin/python -m pytest scripts/tests -q`：44 项通过。
- `apps/openkb/.venv/bin/python -m pytest apps/webui/test/test_lifecycle_scripts.py -q`：
  8 项通过。
- `bash -n apps/webui/scripts/install.sh`、`bash -n apps/webui/scripts/start.sh` 通过。
- `./bin/icarus help` 与 `./bin/icarus status webui` 人工冒烟通过（后者在未启动时报告
  stopped）。

未验证：真实的 `icarus install webui` 构建与 `icarus start webui` 端到端启动，需要在本机
执行 pnpm 安装与构建、并准备 `apps/shell/dist` 后才能进行。
