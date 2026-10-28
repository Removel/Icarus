# WebUI 控制面生命周期接入

## 目标

让 `apps/webui` 作为原生后台进程接入仓库控制面：`icarus install webui` 完成依赖安装与
前端构建，`icarus start webui` 在仓库数据目录下启动 `node server/index.mjs` 并写入进程
台账，`icarus stop webui` 回收进程，`icarus status` 与其他应用一起展示状态。不使用
Docker，不新增 Compose 文件，不改动 WebUI 业务代码。

## 实现

- 新增 `apps/webui/scripts/install.sh`：Node ≥ 22.12 与 corepack 校验、参数校验
  （空或 `--dev`，其他退出 2），执行 `corepack pnpm install --frozen-lockfile` 与
  `corepack pnpm build`。
- 新增 `apps/webui/scripts/icarus_start.py` 与 `apps/webui/scripts/start.sh`：合并仓库根
  `.env` 的 WebUI 与后端变量、校验非回环 host 凭据与构建产物、`exec` Node 入口。
- 泛化 `scripts/icarus/main.py` 的 Gateway 进程管理为按项目参数化实现，新增 `webui`
  项目：安装分支、启动前置检查、停止顺序、状态机与帮助文本。
- 修正 `_record_matches` 的起始时间比较，容忍 `ps lstart=` 的 ≤5 秒差异。
- `.example.env` 增加 `ICARUS_WEBUI_*` 示例；`Makefile` 增加 `webui-up/down/logs`。
- 新增测试：`scripts/tests/test_icarus_control.py` 覆盖安装委派、启动与台账、start/stop
  顺序、5 秒容差与 webui running 状态；`apps/webui/test/test_lifecycle_scripts.py` 使用
  假 `node`/`corepack` 可执行文件覆盖安装与启动脚本错误路径和正常路径。
- 新增本目录 `arch.md`，并在 `apps/webui/README.md` 增加控制面启动说明。

## 验收

- `python3 scripts/tests/test_icarus_control.py` 全部通过（29 项）。
- `apps/agent/.venv/bin/python -m pytest scripts/tests -q` 全部通过（44 项）。
- `apps/openkb/.venv/bin/python -m pytest apps/webui/test/test_lifecycle_scripts.py -q`
  全部通过（8 项）。
- `bash -n` 通过新增的两个 shell 脚本；`icarus help` 与 `status` 人工冒烟正常。
- Gateway、TUI、Mem0、OpenKB 的既有行为与断言不变。

## 边界

- 不修改 `server/index.mjs`、`Dockerfile`、`package.json` 等业务代码，不新增 Compose。
- `icarus install webui` 只调用应用私有安装脚本，不代管 Node／corepack 安装。
- 端到端 `install`/`start` 需要本机 pnpm 构建与 `apps/shell/dist`，本轮未执行；文档已注明。
- 真实 `.env` 不修改；只更新 `.example.env`。
