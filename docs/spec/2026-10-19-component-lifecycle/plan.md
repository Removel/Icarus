# 统一组件生命周期与运维控制面实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **仓库规则：** 未获明确授权不得创建分支、提交、amend、rebase 或 push。本计划不包含 `git commit` 步骤；每个任务以“运行验证命令并记录结果”结束。实施前先确认目标 remote 与 `feature` baseline。

**Goal:** 用根 `icarus.toml` 统一管理应用与中间件的生命周期、地址、端口、依赖和配置注入，支持每个组件独立选择 local/remote，并把 rela-mem 及其 PostgreSQL/FalkorDB 接入同一控制面。

**Architecture:** 根控制面保持 stdlib-only，使用 `tomllib` 读取注册表；注册表经依赖图和生命周期层分发给 Compose/进程适配器、健康探测与环境变量注入。应用不读取根配置，只通过环境变量接收连接值；远程组件只探测，不安装、不启停。

**Tech Stack:** Python 3.11+、stdlib `tomllib`/`dataclasses`/`unittest`、Docker Compose、pytest（应用测试）、YAML（仅 Compose 实现文件）。

**Spec:** [design.md](design.md)

## 任务进度摘要（2026-10-19）

当前架构见 [arch.md](arch.md)；执行命令、输出、基线及真实 E2E 的最终记录统一放在
[execution.md](execution.md)，由控制器维护。本页不把早期示例代码块或预期失败当作真实执行证据，
也不批量勾选下方原始 illustrative checklists。以下摘要优先于仍保留的原计划片段。

| 任务 | 当前状态 | 实际落点 / 验收边界 |
| --- | --- | --- |
| 1 注册表 | 已实现并审查 | 严格 schema、overlay、命令/env/owner 元数据；不是旧示例的精确字段清单 |
| 2–4 图、探测、注入 | 已实现并审查 | required DAG、opaque remote owner、有界真实 HTTP/TCP、纯环境映射 |
| 5–6 Compose / 原生进程 | 已实现并审查 | provider 显式 bootstrap、pull/build、Linux `/proc` 身份与安全终止 |
| 7–9 编排、CLI、infra | 已实现并审查通过 | 动态注册、10 组件配置诊断、app-only rela-mem Compose、原数据路径；整体审查修复与验收见 execution |
| 10 应用配置 | 已实现并审查 | Agent 三端点 / rela-mem 八连接字段覆盖，不改业务配置或启用插件 |
| 11 文档、真实验收、最终检查 | 已完成 | 根架构/使用说明/真实 E2E/回归/整体审查/原 Feishu 证据已闭环；TUI 既有失败单列 |

控制器已记录真实 Gateway/WebUI 的 9 次生产 CLI 操作通过；隔离 PG17/FalkorDB 4.22.1/rela-mem
验证健康、认证、人物/档案/关系、墓碑和重启持久化，mixed 原生 app + 同一测试库注册 remote 也通过，
remote stop 留库。没有使用用户数据库/密码，不代表用户指定远程部署验收，不是性能 benchmark。
自建容器/网络/唯一镜像已按明确身份清理，临时数据保留。

最新控制器回归摘要：根 362 passed；Agent 977、Gateway 34（既有 warning）、mem0 86、openkb 335
（2 条既有 warning）、rela-mem 41（29 integration deselected、既有 warning）、WebUI 15 passed。
TUI 最终 253 passed、13 个既有快照失败；基线另有 flaky history-restore 失败，本轮通过且无 TUI 源码修改，
不得按失败数量减少宣称修复。精确命令、时长与失败集合见 execution，本文未重跑完整套件。

## 执行修正（2026-10-19）

开始实施后，以仓库 `AGENTS.md` 和真实行为为准修正原计划中的示例缺陷：

- 根运行时代码仍只使用标准库；新增测试使用现有 Agent 测试环境中的 pytest functional tests/native assert，不为根安装依赖。保留已有 unittest parity 用例。
- embedded/bundled 的 `owner` 是生命周期归属，不是普通 required 边：Agent 的依赖在 Gateway 启动前校验；mem0 自带数据库在 owner 启动后校验，避免循环等待。远程 owner 不递归启动本地内部依赖。
- Compose/进程命令、身份标识和环境绑定通过注册表声明；中间件 bootstrap 显式声明，控制面不反向扫描应用连接配置决定数据库账号。
- Secret 允许继承/显式引用并传给实际服务，但不作为配置派生值、不显示到诊断；警告只列变量名，不打印旧值/新值。HTTP 探测不跟随认证重定向，响应大小有界。
- 增加只读 `icarus config validate`、`icarus config show [component]` 和 `--config PATH`；配合本地覆盖示例，作为“人类友好的配置方式”的验收。
- `--config PATH` 使用旁侧 `<stem>.local.toml`，运维相对路径仍以仓库根为基准；不会隐式叠加另一套机器配置。
- Task 2–4 的小型纯函数模块合并为一个实现/审查单元，仍分别保留 RED/GREEN 记录。
- 原计划中的导入失败、空 `...` 测试与错误 fixture 不作为验收；先写真实行为断言并看到预期失败。
- 真实 E2E 必须运行实际 CLI、子进程与 HTTP/TCP；容器或数据库不可用时如实记录，不把 mock/static config 检查写成端到端通过。

Task 1–10 的实现状态见上方摘要；原计划的逐步示例保留为历史拆解，不代表逐行照抄/逐条执行。

进一步 preflight/实现修正：

- PostgreSQL bootstrap 只读 provider `env` / `secret_env`，不采用 Task 5 旧示例的 consumer 反向扫描。
- `workdir`、`image_action`、`endpoint_args` 已纳入严格 schema；`configuration.py` 负责来源诊断与地址归一，
  `test_lifecycle_cli.py` 通过实际子进程验证 CLI，均是实施时增加的具体落点。
- `install` 不再创建 `.env` 或自动安装全局命令链接；`--dev` 只安装 app-owned 环境，不顺带构建镜像。
- 已确认的 orphan 可按严格身份校验安全收束，external 不误杀；不是“所有 orphan 一律不能停止”。
- **原生 managed process 当前限 Linux `/proc`；旧三合一 rela-mem 必须手动停旧后才能复用原 DB mounts。**
  没有自动 active-mount 冲突保护。remote 角色/库须预建，TCP 可达不代表认证成功。
- 初始 Docker stub 不可用与后续真实 Docker 恢复分开记录；Compose 渲染、真实容器、真实生产 CLI
  是不同证据，不能用其一替代另外两项。mixed remote 使用测试自建 DB，不写用户库。

## Global Constraints

- 根控制面运行时代码只使用 Python 标准库；不创建根 `.venv`，不新增根层依赖。测试使用已有应用 pytest 环境。
- `icarus.toml` 是运维拓扑唯一事实来源；`icarus.local.toml` 是本机覆盖层并被 `.gitignore` 忽略；不保留硬编码回退。
- 凭据只来自 `.env` 或进程环境；被跟踪文件不得出现明文密码。注册表只写 `*_env` 变量名。
- 远程组件（`mode = "remote"`）只做探测；`install`/`start`/`stop` 不触碰本地镜像、容器、进程或数据。
- required 依赖不可达时依赖组件不得启动；optional 依赖不可达只告警，不阻塞。
- 保留 `external`、`orphaned`、PID/进程身份校验、未知端口拒绝等既有语义。
- 保留现有数据路径：`$ICARUS_DATA_DIR/services/rela-mem/postgres`、`$ICARUS_DATA_DIR/services/rela-mem/falkordb`；不自动迁移、不自动清空。
- 不生成或改写应用业务配置；只允许应用配置加载器读取环境变量覆盖连接字段。
- `argus` 不登记；不做远程部署、镜像推送或 SSH 编排。
- 未获明确授权不提交 Git。

## Review Focus

1. **注册表错误输入：** 缺少 `icarus.toml`、TOML 语法错误、未知字段、未知依赖、缺失 `compose`/`owner` 时必须给出可读错误并停止，不能部分启动。由 Task 1 的 `test_rejects_unknown_field`、`test_rejects_unknown_dependency`、`test_rejects_missing_compose`、`test_wraps_toml_syntax_error` 固定。
2. **覆盖优先级：** `.env`/调用者环境、`icarus.toml`、`icarus.local.toml`、命令行覆盖同时存在时，顺序必须是 CLI > local > base > 默认值，且 CLI 不写回文件。由 Task 1 的 `test_local_override_merges_without_dropping_fields`、`test_cli_override_wins` 和 Task 8 的 `test_cli_override_is_not_persisted` 固定。
3. **依赖失败传播：** required 远程依赖不可达时，依赖它的本地组件必须失败；optional 远程依赖不可达只告警。由 Task 7 的 `test_required_remote_dependency_blocks_dependent`、`test_optional_remote_dependency_warns` 固定。
4. **环境变量冲突：** 注册表派生值必须覆盖调用者同名变量并输出 warning；`password_env` 只声明变量名，Secret 可从既有环境继承或引用，但不得凭空派生或出现在配置输出/警告中。由 Task 4 的环境注入与脱敏测试固定。
5. **本地数据与远程隔离：** 启动本地中间件不能改变现有数据路径；远程模式下不能执行任何本地 Compose/进程操作。由 Task 9 的 `test_infra_compose_files_preserve_data_paths_and_network`、Task 7 的 `test_remote_component_never_calls_local_runtime` 固定。

---

## File Structure

```text
icarus.toml                                     新增  组件注册表
.gitignore                                      修改  忽略 icarus.local.toml
.example.env                                    修改  补 RELA_MEM_* 变量名
infra/README.md                                 新增  中间件约定
infra/postgres/compose.yaml                     新增  共享 PostgreSQL
infra/falkordb/compose.yaml                     新增  FalkorDB
scripts/test.sh                                 修改  纳入新控制面测试
scripts/icarus/main.py                          重构  CLI 与命令分发
scripts/icarus/registry.py                      新增  TOML 加载/合并/校验/模型
scripts/icarus/graph.py                         新增  依赖闭包/拓扑排序
scripts/icarus/probes.py                        新增  HTTP/TCP 探测
scripts/icarus/injection.py                     新增  环境变量映射
scripts/icarus/compose_runtime.py               新增  Compose 适配
scripts/icarus/process_runtime.py               新增  本机进程适配
scripts/tests/test_registry.py                  新增
scripts/tests/test_graph.py                     新增
scripts/tests/test_probes.py                    新增
scripts/tests/test_injection.py                 新增
scripts/tests/test_compose_runtime.py           新增
scripts/tests/test_process_runtime.py           新增
scripts/tests/test_lifecycle.py                 新增
scripts/tests/test_icarus_control.py            修改  行为 parity 与新命令
scripts/tests/test_repository_infra.py          修改  注册表/Compose/Secret 仓库检查
apps/agent/src/agent_app/config/config_loader.py       修改  端点环境覆盖
apps/agent/test/agent_app/config/test_config_loader.py 修改  覆盖测试
apps/rela-mem/src/config.py                            修改  数据库连接环境覆盖
apps/rela-mem/test/test_config.py                      修改  覆盖测试
apps/rela-mem/docker-compose.yaml                      修改  仅应用 + 共享网络
apps/rela-mem/settings.example.json                    修改  独立运行默认值
apps/rela-mem/README.md                                修改  启动说明
```

---

### Task 1: 注册表模型、TOML 加载与校验

**Files:**
- Create: `scripts/icarus/registry.py`
- Test: `scripts/tests/test_registry.py`

**Interfaces:**
- Consumes: stdlib `tomllib`、`dataclasses`、`pathlib`。
- Produces:
  - `class RegistryError(ValueError)`
  - `@dataclass(frozen=True) Probe(type, path="/", token_env=None, header=None, expect_status=None, expect_body=None, host=None, port=None)`
  - `@dataclass(frozen=True) Connection(host_env=None, port_env=None, database_env=None, user_env=None, schema_env=None, graph_env=None, password_env=None, database=None, user=None, schema=None, graph=None)`
  - `@dataclass(frozen=True) Dependency(name, required=True, connection=None, endpoint_env=None)`
  - `@dataclass(frozen=True) Component(name, kind, mode, provisioning, enabled=True, owner=None, engine=None, default_stack=False, endpoint=None, host=None, port=None, network_host=None, network_port=None, compose=None, compose_project=None, expected_containers=1, data_dir=None, probe=None, requires={})`
  - `@dataclass(frozen=True) Registry(version, components)`，方法 `get(name)`, `names()`, `default_stack()`
  - `def load_registry(repo_root: Path, *, overrides: Mapping[str, Mapping[str, object]] | None = None) -> Registry`

**Exact registry keys:**
- Top level: `version`, `defaults`, `components`。
- `defaults`: `mode`, `default_stack`。
- Component: `kind`, `mode`, `provisioning`, `enabled`, `owner`, `engine`, `default_stack`, `endpoint`, `host`, `port`, `network_host`, `network_port`, `compose`, `compose_project`, `expected_containers`, `data_dir`, `probe`, `requires`。
- Dependency: `required`, `connection`, `endpoint_env`。
- Connection: `host_env`, `port_env`, `database_env`, `user_env`, `schema_env`, `graph_env`, `password_env`, `database`, `user`, `schema`, `graph`。
- Probe: `type`, `path`, `token_env`, `header`, `expect_status`, `expect_body`, `host`, `port`。

**Validation rules:**
- `version == 1`；`components` 非空。
- `kind in {"app", "middleware"}`；`mode in {"local", "remote"}`；`provisioning in {"compose", "process", "embedded", "foreground", "bundled", "none"}`。
- `provisioning in {"embedded", "bundled"}` 必须声明存在的 `owner`。
- `provisioning == "compose"` 必须声明相对 `compose` 路径；文件存在性在启动时由 `ComposeRuntime` 检查。
- `kind == "middleware"` 必须声明 `host` 与 `port`；`provisioning == "bundled"` 的中间件可以用 `probe.host/port` 代替。
- `probe.type == "http"` 必须声明 `endpoint`；`probe.type == "tcp"` 使用 `probe.host/port` 或组件 `host/port`。
- `requires` 的键必须是已存在组件；禁止自依赖。
- `data_dir` 必须是相对路径且不含 `..`。
- `compose_project` 缺省为 `icarus-<name>`；`expected_containers` 缺省为 `1`。
- 所有未知键报错；任何 `password` 字面量键报错；`*_env` 值必须匹配 `[A-Za-z_][A-Za-z0-9_]*`。
- 合并顺序：base `icarus.toml` → `icarus.local.toml`（存在时）→ `overrides`；dict 递归合并，标量和列表替换。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_registry.py
import tempfile, textwrap, unittest
from pathlib import Path
import sys

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from registry import RegistryError, load_registry


class RegistryTest(unittest.TestCase):
    def write(self, root: Path, name: str, text: str) -> None:
        (root / name).write_text(textwrap.dedent(text), encoding="utf-8")

    def test_loads_base_registry_with_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", """
                version = 1
                [defaults]
                mode = "local"
                default_stack = false
                [components.gateway]
                kind = "app"
                provisioning = "process"
                endpoint = "http://127.0.0.1:8765"
                probe = { type = "http", path = "/health" }
            """)
            registry = load_registry(root)
            gateway = registry.get("gateway")
            self.assertEqual(gateway.mode, "local")
            self.assertEqual(gateway.probe.path, "/health")
            self.assertEqual(gateway.compose_project, "icarus-gateway")
            self.assertEqual(registry.default_stack(), ())

    def test_local_override_merges_without_dropping_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", """
                version = 1
                [components.postgres]
                kind = "middleware"
                engine = "postgres"
                mode = "local"
                provisioning = "compose"
                compose = "infra/postgres/compose.yaml"
                host = "127.0.0.1"
                port = 15432
                probe = { type = "tcp" }
            """)
            self.write(root, "icarus.local.toml", """
                [components.postgres]
                mode = "remote"
                host = "<redacted-host>"
                port = 5432
            """)
            postgres = load_registry(root).get("postgres")
            self.assertEqual(postgres.mode, "remote")
            self.assertEqual(postgres.host, "<redacted-host>")
            self.assertEqual(postgres.port, 5432)
            self.assertEqual(postgres.engine, "postgres")

    def test_cli_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", """
                version = 1
                [components.gateway]
                kind = "app"
                provisioning = "process"
                endpoint = "http://127.0.0.1:8765"
                probe = { type = "http", path = "/health" }
            """)
            registry = load_registry(root, overrides={"gateway": {"endpoint": "http://remote:8765"}})
            self.assertEqual(registry.get("gateway").endpoint, "http://remote:8765")

    def test_rejects_unknown_field(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", """
                version = 1
                [components.gateway]
                kind = "app"
                provisioning = "process"
                endpoint = "http://127.0.0.1:8765"
                probe = { type = "http", path = "/health" }
                password = "secret"
            """)
            with self.assertRaisesRegex(RegistryError, "unknown"):
                load_registry(root)

    def test_rejects_unknown_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", """
                version = 1
                [components.rela-mem]
                kind = "app"
                provisioning = "compose"
                compose = "apps/rela-mem/docker-compose.yaml"
                endpoint = "http://127.0.0.1:8767"
                probe = { type = "http", path = "/healthz" }
                [components.rela-mem.requires.missing]
                required = true
            """)
            with self.assertRaisesRegex(RegistryError, "missing"):
                load_registry(root)

    def test_rejects_missing_compose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", """
                version = 1
                [components.postgres]
                kind = "middleware"
                engine = "postgres"
                provisioning = "compose"
                host = "127.0.0.1"
                port = 15432
                probe = { type = "tcp" }
            """)
            with self.assertRaisesRegex(RegistryError, "compose"):
                load_registry(root)

    def test_wraps_toml_syntax_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.write(root, "icarus.toml", "version = 1\n[components.gateway\n")
            with self.assertRaisesRegex(RegistryError, "icarus.toml"):
                load_registry(root)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_registry.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'registry'`。

- [ ] **Step 3: 实现 `registry.py`**

按上面的数据模型、键集合、校验规则实现。使用 `tomllib.loads`；捕获 `tomllib.TOMLDecodeError` 并转换为 `RegistryError("failed to parse <path>: ...")`。实现 `_deep_merge(base: dict, override: dict) -> dict`，只对 dict 递归合并；其余值直接替换。`overrides` 的键是组件名，值是字段字典。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_registry.py -v`
Expected: PASS，全部 8 个测试。

---

### Task 2: 依赖图与启动顺序

**Files:**
- Create: `scripts/icarus/graph.py`
- Test: `scripts/tests/test_graph.py`

**Interfaces:**
- Consumes: `Registry`, `Component`, `Dependency` from `registry.py`。
- Produces:
  - `class GraphError(ValueError)`
  - `def required_closure(registry: Registry, names: Sequence[str]) -> tuple[str, ...]`
  - `def topological_order(registry: Registry, names: Sequence[str]) -> tuple[str, ...]`
  - `def reverse_order(order: Sequence[str]) -> tuple[str, ...]`

**Semantics:**
- 只沿 `Dependency.required == True` 的边扩展闭包和排序；optional 边不参与启动顺序。
- 输入名称去重并保持稳定顺序；未知名称报 `GraphError`。
- 循环依赖报 `GraphError`，错误中包含环上的组件名。
- `topological_order` 返回依赖在前、消费者在后；`required_closure` 返回集合的确定性顺序（按输入与发现顺序）。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_graph.py
import unittest
from dataclasses import replace
import sys
from pathlib import Path

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from registry import Component, Dependency, Registry
from graph import GraphError, required_closure, reverse_order, topological_order


def component(name, requires=(), kind="app", mode="local", provisioning="compose"):
    return Component(
        name=name, kind=kind, mode=mode, provisioning=provisioning,
        compose="x.yaml" if provisioning == "compose" else None,
        probe=None, requires={dep: Dependency(dep) for dep in requires},
    )


def registry(**components):
    return Registry(version=1, components=components)


class GraphTest(unittest.TestCase):
    def test_required_closure_follows_required_edges_only(self):
        reg = registry(
            app=component("app", requires=("db", "optional")),
            db=component("db"),
            optional=component("optional"),
        )
        reg.components["app"].requires["optional"] = Dependency("optional", required=False)
        self.assertEqual(set(required_closure(reg, ["app"])), {"app", "db"})

    def test_topological_order_puts_dependencies_first(self):
        reg = registry(
            app=component("app", requires=("db",)),
            db=component("db", requires=("base",)),
            base=component("base"),
        )
        self.assertEqual(topological_order(reg, ["app"]), ("base", "db", "app"))

    def test_reverse_order_reverses_start_order(self):
        self.assertEqual(reverse_order(("base", "db", "app")), ("app", "db", "base"))

    def test_cycle_is_rejected(self):
        reg = registry(
            a=component("a", requires=("b",)),
            b=component("b", requires=("a",)),
        )
        with self.assertRaisesRegex(GraphError, "cycle"):
            topological_order(reg, ["a"])

    def test_unknown_name_is_rejected(self):
        with self.assertRaisesRegex(GraphError, "unknown"):
            required_closure(registry(), ["missing"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_graph.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'graph'`。

- [ ] **Step 3: 实现 `graph.py`**

用带三色标记的 DFS。`required_closure` 先扩展集合，再调用 `topological_order` 对闭包排序；`topological_order` 只遍历 required 边。错误信息包含组件名和依赖链。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_graph.py -v`
Expected: PASS，全部 5 个测试。

---

### Task 3: HTTP/TCP 健康探测

**Files:**
- Create: `scripts/icarus/probes.py`
- Test: `scripts/tests/test_probes.py`

**Interfaces:**
- Consumes: `Component`, `Probe` from `registry.py`。
- Produces:
  - `@dataclass(frozen=True) ProbeResult(ok: bool, state: str, detail: str)`
  - `def probe(component: Component, environ: Mapping[str, str]) -> ProbeResult`

**Semantics:**
- `state` 只能是 `"reachable"` 或 `"unreachable"`；网络错误不抛异常。
- HTTP：URL 为 `component.endpoint.rstrip("/") + probe.path`；超时 2 秒。
- Token：`probe.token_env` 有值时，若 `probe.header` 已设置则发送 `<header>: <token>`，否则发送 `Authorization: Bearer <token>`。
- `expect_status` 设置时要求精确相等；否则接受 `200 <= status < 400`。
- `expect_body` 设置时解析 JSON，并要求每个 key/value 相等。
- TCP：地址使用 `probe.host or component.host`、`probe.port or component.port`，超时 2 秒。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_probes.py
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from probes import probe
from registry import Component, Probe


class Handler(BaseHTTPRequestHandler):
    received = []
    def do_GET(self):
        Handler.received.append({key.lower(): value for key, value in self.headers.items()})
        if self.path == "/ok":
            body = json.dumps({"status": "ready"}).encode()
            self.send_response(200)
        elif self.path == "/bad":
            body = b"no"
            self.send_response(503)
        else:
            body = b"missing"
            self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass


class ProbeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def http_component(self, path, **probe_kwargs):
        return Component(
            name="gateway", kind="app", mode="local", provisioning="process",
            endpoint=f"http://127.0.0.1:{self.port}",
            probe=Probe(type="http", path=path, **probe_kwargs),
        )

    def test_http_status_and_body_expectation(self):
        result = probe(self.http_component("/ok", expect_body={"status": "ready"}), {})
        self.assertTrue(result.ok)
        self.assertEqual(result.state, "reachable")

    def test_http_non_2xx_is_unreachable(self):
        self.assertFalse(probe(self.http_component("/bad"), {}).ok)

    def test_http_token_env_uses_bearer_header(self):
        Handler.received.clear()
        result = probe(self.http_component("/ok", token_env="TOKEN"), {"TOKEN": "abc"})
        self.assertTrue(result.ok)
        self.assertEqual(Handler.received[-1]["authorization"], "Bearer abc")

    def test_http_custom_header_uses_token_without_prefix(self):
        Handler.received.clear()
        probe(self.http_component("/ok", token_env="TOKEN", header="x-api-key"), {"TOKEN": "abc"})
        self.assertEqual(Handler.received[-1]["x-api-key"], "abc")

    def test_tcp_probe(self):
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        component = Component(
            name="postgres", kind="middleware", mode="local", provisioning="compose",
            host="127.0.0.1", port=port, probe=Probe(type="tcp"),
        )
        try:
            self.assertTrue(probe(component, {}).ok)
        finally:
            server.close()

    def test_refused_tcp_is_unreachable(self):
        component = Component(
            name="postgres", kind="middleware", mode="local", provisioning="compose",
            host="127.0.0.1", port=1, probe=Probe(type="tcp"),
        )
        self.assertFalse(probe(component, {}).ok)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_probes.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'probes'`。

- [ ] **Step 3: 实现 `probes.py`**

使用 `urllib.request.Request`/`urlopen` 和 `socket.create_connection`；捕获 `HTTPError`（仍然可以检查状态码）、`URLError`、`TimeoutError`、`OSError`、`ValueError`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_probes.py -v`
Expected: PASS，全部 6 个测试。

---

### Task 4: 环境变量注入

**Files:**
- Create: `scripts/icarus/injection.py`
- Test: `scripts/tests/test_injection.py`

**Interfaces:**
- Consumes: `Registry`, `Component`, `Dependency`, `Connection` from `registry.py`。
- Produces:
  - `@dataclass(frozen=True) InjectionResult(environment: dict[str, str], warnings: tuple[str, ...])`
  - `def build_child_environment(component: Component, registry: Registry, base_environment: Mapping[str, str]) -> InjectionResult`

**Semantics:**
- 复制 `base_environment`。
- 对每个 `component.requires`：
  - `dependency = registry.get(dep.name)`。
  - 若 `dep.endpoint_env` 且 dependency 有 `endpoint`，设置该环境变量。
  - 若 `dep.connection`：
    - 选择地址：`dependency.mode == "remote"` → `host/port`；否则消费方 `provisioning == "compose"` 且依赖有 `network_host` → `network_host/network_port`；其余 → `host/port`。
    - 写入 `host_env`/`port_env`（端口转字符串）。
    - 写入 `database_env`/`user_env`/`schema_env`/`graph_env` 的静态值。
    - **不读取、不写入** `password_env` 的值。
- 当目标键已在 base 中存在且值不同，追加 warning：`"<component>: overriding <ENV> for <dependency> (<old> -> <new>)"`。
- 返回的 `environment` 是完整子进程环境；`warnings` 顺序与依赖声明顺序一致。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_injection.py
import unittest
from pathlib import Path
import sys

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from injection import build_child_environment
from registry import Component, Connection, Dependency, Registry


def comp(name, **kwargs):
    defaults = dict(kind="app", mode="local", provisioning="process")
    defaults.update(kwargs)
    return Component(name=name, **defaults)


class InjectionTest(unittest.TestCase):
    def test_compose_consumer_gets_network_address(self):
        postgres = comp("postgres", kind="middleware", provisioning="compose",
                        host="127.0.0.1", port=15432,
                        network_host="postgres", network_port=5432)
        rela = comp("rela-mem", provisioning="compose", requires={
            "postgres": Dependency("postgres", connection=Connection(
                host_env="RELA_MEM_PG_HOST", port_env="RELA_MEM_PG_PORT",
                database_env="RELA_MEM_PG_DATABASE", database="rela_mem",
                user_env="RELA_MEM_PG_USER", user="rela_mem",
                password_env="RELA_MEM_PG_PASSWORD",
            ))
        })
        result = build_child_environment(rela, Registry(1, {"postgres": postgres, "rela-mem": rela}), {})
        self.assertEqual(result.environment["RELA_MEM_PG_HOST"], "postgres")
        self.assertEqual(result.environment["RELA_MEM_PG_PORT"], "5432")
        self.assertEqual(result.environment["RELA_MEM_PG_DATABASE"], "rela_mem")
        self.assertNotIn("RELA_MEM_PG_PASSWORD", result.environment)

    def test_process_consumer_gets_host_address(self):
        mem0 = comp("mem0", endpoint="http://127.0.0.1:8888")
        gateway = comp("gateway", requires={
            "mem0": Dependency("mem0", endpoint_env="ICARUS_MEM0_ENDPOINT")
        })
        result = build_child_environment(gateway, Registry(1, {"mem0": mem0, "gateway": gateway}), {})
        self.assertEqual(result.environment["ICARUS_MEM0_ENDPOINT"], "http://127.0.0.1:8888")

    def test_remote_dependency_gets_remote_address(self):
        postgres = comp("postgres", kind="middleware", mode="remote",
                        provisioning="compose", host="postgres.example.invalid", port=5432)
        rela = comp("rela-mem", provisioning="compose", requires={
            "postgres": Dependency("postgres", connection=Connection(
                host_env="RELA_MEM_PG_HOST", port_env="RELA_MEM_PG_PORT"))
        })
        result = build_child_environment(rela, Registry(1, {"postgres": postgres, "rela-mem": rela}), {})
        self.assertEqual(result.environment["RELA_MEM_PG_HOST"], "postgres.example.invalid")
        self.assertEqual(result.environment["RELA_MEM_PG_PORT"], "5432")

    def test_registry_value_overrides_caller_environment(self):
        mem0 = comp("mem0", endpoint="http://127.0.0.1:8888")
        gateway = comp("gateway", requires={
            "mem0": Dependency("mem0", endpoint_env="ICARUS_MEM0_ENDPOINT")
        })
        result = build_child_environment(
            gateway, Registry(1, {"mem0": mem0, "gateway": gateway}),
            {"ICARUS_MEM0_ENDPOINT": "http://stale:8888"},
        )
        self.assertEqual(result.environment["ICARUS_MEM0_ENDPOINT"], "http://127.0.0.1:8888")
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("ICARUS_MEM0_ENDPOINT", result.warnings[0])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_injection.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'injection'`。

- [ ] **Step 3: 实现 `injection.py`**

按语义实现；使用 `str(port)`；静态值只在声明了对应 `*_env` 时写入；忽略 `password_env` 的值。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_injection.py -v`
Expected: PASS，全部 4 个测试。

---

### Task 5: Compose 运行时适配

**Files:**
- Create: `scripts/icarus/compose_runtime.py`
- Test: `scripts/tests/test_compose_runtime.py`

**Interfaces:**
- Consumes: `Component` from `registry.py`；`ICARUS_DATA_DIR`/`.env`。
- Produces:
  - `class ComposeError(RuntimeError)`
  - `class ComposeRuntime:`
    - `__init__(repo_root: Path, data_dir: Path, environ: Mapping[str, str], registry: Registry, runner: Callable[..., subprocess.CompletedProcess] | None = None)`
    - `ensure_network(self, name: str = "icarus") -> None`
    - `command(self, component: Component, *args: str) -> list[str]`
    - `build(self, component: Component) -> None`
    - `up(self, component: Component, environment: Mapping[str, str] | None = None) -> None`
    - `down(self, component: Component) -> None`
    - `logs(self, component: Component) -> int`
    - `containers(self, component: Component) -> list[dict[str, object]]`

**Semantics:**
- 命令前缀：优先 `docker compose`，否则 `docker-compose`；与现有 `_check_docker` 行为一致。
- 参数：`--env-file <repo>/.env`（不存在时用 `os.devnull`）、`-p <compose_project>`、`-f <repo>/<compose>`，后接动作参数。
- 环境：`{**environ, "ICARUS_DATA_DIR": str(data_dir), **injected_environment}`。
- `ensure_network`：`docker network inspect icarus` 失败时执行 `docker network create icarus`；已存在视为成功。
- `containers`：使用 `docker ps -a --filter label=com.docker.compose.project=<project> --filter label=com.docker.compose.project.config_files=<abs compose> --format {{json .}}`，逐行解析 JSON。
- `up` 前检查 `repo_root / component.compose` 存在；缺失时报 `ComposeError`。若声明了 `data_dir`，创建 `data_dir / component.data_dir`。
- 对中间件 bootstrap，使用 provider 自己的 `env` / `secret_env` 显式声明（已实施修正）；不扫描 consumer，不推断 `ICARUS_<NAME>_*`。早期测试示例中的反向扫描未采用。
- `expected_containers` 由上层状态判断使用，不在本类中校验。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_compose_runtime.py
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from compose_runtime import ComposeError, ComposeRuntime
from registry import Component, Connection, Dependency, Registry


def component(name="postgres", **kwargs):
    defaults = dict(kind="middleware", mode="local", provisioning="compose",
                    compose="infra/postgres/compose.yaml", compose_project="icarus-postgres",
                    host="127.0.0.1", port=15432, data_dir="services/rela-mem/postgres")
    defaults.update(kwargs)
    return Component(name=name, **defaults)


class ComposeRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "infra/postgres").mkdir(parents=True)
        (self.root / "infra/postgres/compose.yaml").write_text("services: {}\n", encoding="utf-8")
        self.commands = []
        self.registry = Registry(1, {"postgres": component()})
        self.runtime = ComposeRuntime(
            self.root, self.root / "data", {"PATH": "/bin"}, self.registry)
        self.runtime.runner = self.fake_run

    def tearDown(self):
        self.temp.cleanup()

    def fake_run(self, command, **kwargs):
        self.commands.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    def test_up_builds_compose_command_with_project_and_file(self):
        self.runtime.up(component())
        command = self.commands[-1][0]
        self.assertIn("-p", command)
        self.assertIn("icarus-postgres", command)
        self.assertIn("-f", command)
        self.assertIn(str(self.root / "infra/postgres/compose.yaml"), command)

    def test_containers_parses_json_lines(self):
        self.runtime.runner = lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, '{"State":"running","HealthStatus":"healthy"}\n', "")
        rows = self.runtime.containers(component())
        self.assertEqual(rows[0]["HealthStatus"], "healthy")

    def test_missing_compose_file_raises(self):
        missing = component(compose="infra/missing/compose.yaml")
        with self.assertRaisesRegex(ComposeError, "compose"):
            self.runtime.up(missing)

    def test_consumer_connection_provides_postgres_bootstrap(self):
        rela = Component(
            "rela-mem", "app", "local", "compose", compose="x.yaml",
            requires={"postgres": Dependency("postgres", connection=Connection(
                user_env="RELA_MEM_PG_USER", database_env="RELA_MEM_PG_DATABASE",
                user="rela_mem", database="rela_mem"))},
        )
        self.runtime.registry = Registry(1, {"postgres": component(), "rela-mem": rela})
        self.runtime.up(component())
        environment = self.commands[-1][1]["env"]
        self.assertEqual(environment["ICARUS_POSTGRES_USER"], "rela_mem")
        self.assertEqual(environment["ICARUS_POSTGRES_DATABASE"], "rela_mem")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_compose_runtime.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'compose_runtime'`。

- [ ] **Step 3: 实现 `compose_runtime.py`**

按修正后的语义实现；`runner` 默认 `subprocess.run`，在测试中可替换。`ensure_network` 仅在 `up` 或显式调用时执行。
不实现旧 `_bootstrap_environment` 反向扫描；统一注入模块接收 provider 声明的 bootstrap 环境。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_compose_runtime.py -v`
Expected: PASS。

---

### Task 6: 本机进程运行时提取

**Files:**
- Create: `scripts/icarus/process_runtime.py`
- Test: `scripts/tests/test_process_runtime.py`
- Modify: `scripts/icarus/main.py`（后续 Task 8 完成调用切换；本任务只提取代码）

**Interfaces:**
- Consumes: 现有 `main.py` 中的进程/PID/孤儿逻辑。
- Produces:
  - `@dataclass(frozen=True) ProcessState(state: str, detail: str)`
  - `class ProcessRuntime:`
    - `__init__(repo_root: Path, data_dir: Path | None, environ: Mapping[str, str], markers: Mapping[str, tuple[str, ...]])`
    - `spawn(component: Component, args: Sequence[str], log_path: Path) -> int`
    - `status(component: Component) -> ProcessState`
    - `stop(component: Component) -> None`
    - `logs(component: Component, follow: bool = True) -> int`
    - `active_tui_records(self) -> list[tuple[Path, dict[str, object]]]`
    - 测试可见辅助方法：`write_record(path, pid, project, *, marker)`, `read_record(path)`, `record_matches(record, project)`, 可注入的 `healthy(project)`。

**Semantics:**
- 迁移现有 `_spawn_process`、`_process_project_status`、`_stop_process`、`_orphaned_process_records`、`_terminate_record`、`_write_process_record`、`_read_record`、`_record_matches`、`_process_command`、`_process_cwd`、`_process_listens_on_port` 等逻辑，行为保持不变。
- `markers` 从注册表/组件派生；网关与 WebUI/TUI 的 marker 由调用方传入。
- 进程记录路径保持 `$ICARUS_DATA_DIR/runtime/<component>.json`；日志保持 `$ICARUS_DATA_DIR/logs/<component>.log`。
- `external` 与 `orphaned` 判定不变。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_process_runtime.py
import json
import tempfile
import unittest
from pathlib import Path
import sys

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from process_runtime import ProcessRuntime
from registry import Component


class ProcessRuntimeTest(unittest.TestCase):
    def test_record_round_trip_and_identity_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ProcessRuntime(root, root / "data", {}, {"gateway": ("apps.gateway.src.main",)})
            path = root / "gateway.json"
            runtime.write_record(path, 123, "gateway", marker="apps.gateway.src.main")
            self.assertEqual(runtime.read_record(path)["pid"], 123)
            self.assertFalse(runtime.record_matches({"pid": 123, "marker": "other"}, "gateway"))

    def test_external_state_when_healthy_without_record(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = ProcessRuntime(root, root / "data", {}, {"gateway": ("apps.gateway.src.main",)})
            runtime.healthy = lambda name: True
            self.assertEqual(runtime.status(Component("gateway", "app", "local", "process")).state, "external")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_process_runtime.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'process_runtime'`。

- [ ] **Step 3: 实现 `process_runtime.py`**

把 `main.py` 中的相关方法原样迁移，改为实例方法；保留所有校验（命令、CWD、启动时间容差、监听端口）。测试可访问的辅助方法命名为 `write_record`、`read_record`、`record_matches`。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_process_runtime.py -v`
Expected: PASS。

---

### Task 7: 生命周期编排

**Files:**
- Create: `scripts/icarus/lifecycle.py`
- Test: `scripts/tests/test_lifecycle.py`

**Interfaces:**
- Consumes: `Registry`, `Component`, `graph.required_closure`, `graph.topological_order`, `graph.reverse_order`, `probe`, `build_child_environment`, `ComposeRuntime`, `ProcessRuntime`。
- Produces:
  - `@dataclass(frozen=True) StatusRow(component: str, mode: str, state: str, detail: str)`
  - `class Lifecycle:`
    - `__init__(self, registry: Registry, *, compose: ComposeRuntime, process: ProcessRuntime, probe_func=probe, inject_func=build_child_environment, installer: Callable[[Component], int] | None = None, process_args: Callable[[Component], Sequence[str]] = lambda component: (), output=print)`
    - `install(self, names: Sequence[str] | None) -> int`
    - `start(self, names: Sequence[str] | None) -> int`
    - `stop(self, names: Sequence[str] | None) -> int`
    - `status(self, names: Sequence[str] | None) -> tuple[StatusRow, ...]`
    - `logs(self, name: str) -> int`

**Semantics:**
- `names is None` → `registry.default_stack()`；`names` 显式时，先取 `required_closure`。
- `start` 按 `topological_order`；远程组件只 `probe_func`；本地组件按 `provisioning`：
  - `compose` → `compose.up(component, inject_func(...).environment)`。
  - `process` → `process.spawn(component, args, log_path)`；args 由 `main.py` 传入（见 Task 8）。
  - `embedded` → 不单独启动，委托 `owner`；若 owner 未启动则报错。
  - `foreground` → 由 `main.py` 在最后调用 TUI；`start tui` 只在前台运行。
  - `bundled` → 不启动，委托 `owner`。
- required 依赖不可用 → 当前组件失败，输出依赖链，继续无关分支；最终返回非零。
- optional 依赖不可用 → 输出 warning，继续。
- `stop`：显式 `stop <component>` 只停止该组件，不停止其依赖（避免误停共享中间件）；无参数 `stop` 按 `registry.default_stack()` 的 `reverse_order` 执行。远程组件输出“远程组件不受管理”并跳过；`bundled`/`embedded` 委托 owner。
- `status` 输出每行：组件、mode、state、detail；远程不可达时 detail 包含地址/错误。
  - `process`/`foreground` 使用 `ProcessRuntime` 状态（含 `external`、`orphaned`）。
  - `compose` 使用容器状态与 `expected_containers` 判断 running/starting/unhealthy。
  - `embedded`/`bundled` 镜像 owner 的状态；owner 为远程时显示远程探测结果。
  - `tui` 使用前台进程记录；无记录时为 stopped。
- `logs`：local compose 调用 `compose.logs`；local process 读取 `$ICARUS_DATA_DIR/logs/<component>.log`；remote、embedded、bundled 输出明确提示或委托 owner；foreground 不提供独立日志。
- `install`：远程 no-op；本地按 provisioning 调用 `compose.build` 或 `installer(component)` 回调（由 Task 8 注入）。

- [ ] **Step 1: 写失败测试**

```python
# scripts/tests/test_lifecycle.py
import unittest
from pathlib import Path
import sys

CONTROL_DIR = Path(__file__).resolve().parents[1] / "icarus"
sys.path.insert(0, str(CONTROL_DIR))
from lifecycle import Lifecycle
from registry import Component, Dependency, Probe, Registry


class FakeCompose:
    def __init__(self): self.calls = []
    def up(self, component, environment=None): self.calls.append(("up", component.name))
    def down(self, component): self.calls.append(("down", component.name))
    def build(self, component): self.calls.append(("build", component.name))
    def logs(self, component): return 0


class FakeProcess:
    def __init__(self): self.calls = []
    def spawn(self, component, args, log_path): self.calls.append(("spawn", component.name)); return 1
    def status(self, component): return type("S", (), {"state": "stopped", "detail": "-"})()
    def stop(self, component): self.calls.append(("stop", component.name))


def component(name, **kwargs):
    defaults = dict(kind="app", mode="local", provisioning="process", probe=Probe("http", "/health"))
    defaults.update(kwargs)
    return Component(name=name, **defaults)


class LifecycleTest(unittest.TestCase):
    def make(self, components, probe_func=None):
        registry = Registry(1, components)
        compose, process = FakeCompose(), FakeProcess()
        lifecycle = Lifecycle(registry, compose=compose, process=process,
                              probe_func=probe_func or (lambda c, e: type("P", (), {"ok": True, "state": "reachable", "detail": ""})()),
                              output=lambda *a: None)
        return lifecycle, compose, process

    def test_required_remote_dependency_blocks_dependent(self):
        postgres = Component("postgres", "middleware", "remote", "compose", host="postgres.example.invalid", port=5432, probe=Probe("tcp"))
        rela = component("rela-mem", provisioning="compose", compose="x.yaml",
                         requires={"postgres": Dependency("postgres", required=True)})
        lifecycle, compose, _ = self.make(
            {"postgres": postgres, "rela-mem": rela},
            probe_func=lambda c, e: type("P", (), {"ok": False, "state": "unreachable", "detail": "refused"})(),
        )
        self.assertNotEqual(lifecycle.start(["rela-mem"]), 0)
        self.assertNotIn(("up", "rela-mem"), compose.calls)

    def test_optional_remote_dependency_warns_but_starts(self):
        rela = component("rela-mem", mode="remote", endpoint="http://relationship.example.invalid:8767",
                         probe=Probe("http", "/healthz"))
        consumer = component("consumer", requires={
            "rela-mem": Dependency("rela-mem", required=False)})
        lifecycle, _, process = self.make(
            {"rela-mem": rela, "consumer": consumer},
            probe_func=lambda c, e: type("P", (), {"ok": False, "state": "unreachable", "detail": ""})(),
        )
        self.assertEqual(lifecycle.start(["consumer"]), 0)
        self.assertIn(("spawn", "consumer"), process.calls)

    def test_remote_component_never_calls_local_runtime(self):
        postgres = Component("postgres", "middleware", "remote", "compose", host="postgres.example.invalid", port=5432, probe=Probe("tcp"))
        lifecycle, compose, process = self.make({"postgres": postgres})
        self.assertEqual(lifecycle.start(["postgres"]), 0)
        self.assertEqual(compose.calls, [])
        self.assertEqual(process.calls, [])

    def test_explicit_stop_does_not_stop_shared_dependency(self):
        app = component("app", default_stack=True, requires={"db": Dependency("db")})
        db = component("db", provisioning="compose", compose="x.yaml", default_stack=False)
        lifecycle, compose, _ = self.make({"app": app, "db": db})
        lifecycle.stop(["app"])
        self.assertNotIn(("down", "db"), compose.calls)

    def test_no_arg_stop_uses_reverse_default_stack_order(self):
        first = component("first", provisioning="compose", compose="x.yaml", default_stack=True)
        second = component("second", provisioning="compose", compose="x.yaml", default_stack=True)
        lifecycle, compose, _ = self.make({"first": first, "second": second})
        lifecycle.stop(None)
        self.assertEqual(compose.calls, [("down", "second"), ("down", "first")])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_lifecycle.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'lifecycle'`。

- [ ] **Step 3: 实现 `lifecycle.py`**

按语义实现；依赖顺序来自 `graph`；用一个内部方法 `_dependency_ready(component)` 探测 required 依赖。错误信息包含组件链。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 scripts/tests/test_lifecycle.py -v`
Expected: PASS。

---

### Task 8: CLI 接线、`icarus.toml` 与现有组件 parity

**Files:**
- Create: `icarus.toml`
- Modify: `scripts/icarus/main.py`
- Modify: `scripts/test.sh`
- Modify: `scripts/tests/test_icarus_control.py`

**Interfaces:**
- Consumes: `load_registry`, `graph`, `Lifecycle`, `ComposeRuntime`, `ProcessRuntime`，以及现有应用安装/启动脚本。
- Produces: 命令面保持 `icarus install|start|stop|status|logs|tui`；新增 `--mode/--host/--port/--endpoint`（必须与显式组件一起使用）。

**`icarus.toml` 必须登记：**

| 组件 | kind | provisioning | default_stack | endpoint / host:port | compose | compose_project | expected |
|---|---|---|---|---|---|---|---|
| gateway | app | process | true | `http://127.0.0.1:8765` | - | - | - |
| agent | app | embedded | true | - | - | - | - |
| tui | app | foreground | true | - | - | - | - |
| webui | app | process | true | `http://127.0.0.1:8080` | - | - | - |
| mem0 | app | compose | true | `http://127.0.0.1:8888` | `apps/mem0/server/docker-compose.yaml` | `mem0-dev` | 3 |
| mem0-postgres | middleware | bundled | false | `127.0.0.1:8432` | - | - | - |
| openkb | app | compose | true | `http://127.0.0.1:7566` | `apps/openkb/docker-compose.yaml` | `openkb-dev` | 1 |
| rela-mem | app | compose | true | `http://127.0.0.1:8767` | `apps/rela-mem/docker-compose.yaml` | `icarus-rela-mem` | 1 |
| postgres | middleware | compose | false | `127.0.0.1:15432` / 网络 `postgres:5432` | `infra/postgres/compose.yaml` | `icarus-postgres` | 1 |
| falkordb | middleware | compose | false | `127.0.0.1:16379` / 网络 `falkordb:6379` | `infra/falkordb/compose.yaml` | `icarus-falkordb` | 1 |

依赖边按 design.md §注册表示例；`agent` 依赖 `mem0`、`openkb`（required）与 `rela-mem`（optional）；`mem0` 依赖 `mem0-postgres`；`rela-mem` 依赖 `postgres`、`falkordb`。额外增加 `[components.gateway.requires.agent] required = true`，使显式 `icarus start gateway` 也满足 embedded Agent 的 required 依赖。`probe` 使用 design.md 中的路径与期望；`openkb` 的 `token_env = "ICARUS_OPENKB_API_TOKEN"`。

**CLI 规则：**
- `icarus start` 无组件：`default_stack` + required 闭包，最后前台启动 TUI；只接受 TUI 参数。
- `icarus start <component> --mode/--host/--port/--endpoint`：覆盖只对本次调用生效。
- `icarus start agent`：保持“agent runs inside gateway”错误。
- `icarus tui`：保持 Gateway 就绪检查；显式 `--gateway-url` 时不检查默认本地 Gateway。
- `icarus status`：输出全部组件，包含 mode、地址、状态与降级信息。
- `icarus install`：远程 no-op；本地调用对应安装/构建。
- `stop`：逆序；远程输出“不受本控制面管理”。
- `main.py` 向 `Lifecycle` 注入 `installer`（本地 app 调用 `apps/<component>/scripts/install.sh`；compose 组件调用 `compose.build`）和 `process_args`（gateway 传 `--host/--port`；webui 无参数）。

- [ ] **Step 1: 写/改失败测试**

在 `scripts/tests/test_icarus_control.py` 增加/修改：

```python
def test_no_arg_start_includes_rela_mem_and_required_middleware(self):
    # 用真实 icarus.toml 加载后，断言 default_stack 闭包含 rela-mem、postgres、falkordb
    ...

def test_explicit_start_includes_required_dependencies(self):
    # start rela-mem 时先启动 postgres/falkordb（fake runtime 记录顺序）
    ...

def test_cli_override_is_not_persisted(self):
    # start postgres --mode remote --host postgres.example.invalid 后，文件内容不变
    ...

def test_no_component_start_rejects_mode_override(self):
    # start --mode remote 报错
    ...
```

将旧的精确序列断言更新为“包含 rela-mem 与 required 中间件”；保留 `external`、`orphaned`、PID 校验、未知端口拒绝测试。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 scripts/tests/test_icarus_control.py -v`
Expected: 新测试失败；旧测试中依赖旧常量表的断言失败。

- [ ] **Step 3: 实现 CLI 接线与 `icarus.toml`**

创建 `icarus.toml`（按上表 + design.md 的依赖边）；`main.py` 改为加载注册表并调用 `Lifecycle`；保留 `bin/icarus` 入口和帮助文本。`scripts/test.sh` 在系统 `python3` 下依次运行新 unittest 模块：

```bash
for test in test_registry test_graph test_probes test_injection \
            test_compose_runtime test_process_runtime test_lifecycle \
            test_icarus_control; do
  python3 "$repo_root/scripts/tests/$test.py"
done
```

- [ ] **Step 4: 运行测试确认通过**

Run:
```bash
python3 scripts/tests/test_registry.py
python3 scripts/tests/test_graph.py
python3 scripts/tests/test_probes.py
python3 scripts/tests/test_injection.py
python3 scripts/tests/test_compose_runtime.py
python3 scripts/tests/test_process_runtime.py
python3 scripts/tests/test_lifecycle.py
python3 scripts/tests/test_icarus_control.py
```
Expected: 全部 PASS；旧的单组件行为测试保留。

---

### Task 9: infra 中间件定义与 rela-mem Compose 拆分

**Files:**
- Create: `infra/postgres/compose.yaml`
- Create: `infra/falkordb/compose.yaml`
- Create: `infra/README.md`
- Modify: `apps/rela-mem/docker-compose.yaml`
- Modify: `scripts/tests/test_repository_infra.py`

**Interfaces:**
- Consumes: `icarus.toml` 中的 `postgres`、`falkordb`、`rela-mem` 条目；Task 10 的应用环境覆盖。
- Produces: 可被 `ComposeRuntime` 独立启动的 Compose 项目；共享外部网络 `icarus`。

**Compose 契约：**
- `infra/postgres/compose.yaml`：服务 `postgres`；镜像 `pgvector/pgvector:pg17`；`POSTGRES_USER=${ICARUS_POSTGRES_USER:?}`、`POSTGRES_DB=${ICARUS_POSTGRES_DATABASE:?}`、`POSTGRES_PASSWORD=${RELA_MEM_PG_PASSWORD:?}`；端口 `127.0.0.1:${ICARUS_POSTGRES_PORT:?}:5432`；数据卷 `${ICARUS_DATA_DIR:?}/services/rela-mem/postgres:/var/lib/postgresql/data`；`pg_isready` 健康检查；网络 `icarus`。
- `infra/falkordb/compose.yaml`：服务 `falkordb`；镜像 `falkordb/falkordb:v4.22.1`；密码 `${RELA_MEM_FALKORDB_PASSWORD:?}`；端口 `127.0.0.1:${ICARUS_FALKORDB_PORT:?}:6379`；数据卷 `${ICARUS_DATA_DIR:?}/services/rela-mem/falkordb:/var/lib/falkordb/data`；Redis 健康检查；网络 `icarus`。
- 两个文件都声明：

```yaml
networks:
  icarus:
    external: true
```

- `apps/rela-mem/docker-compose.yaml`：只保留 `rela-mem` 服务；删除 `postgres`、`falkordb`；加入外部网络 `icarus`；环境变量补充 `RELA_MEM_PG_HOST/PORT/DATABASE/USER/SCHEMA`、`RELA_MEM_FALKORDB_HOST/PORT/GRAPH`；保留 API Token、两个数据库密码、配置只读挂载与 `/healthz` 健康检查。

- [ ] **Step 1: 写失败测试**

在 `scripts/tests/test_repository_infra.py` 增加：

```python
def test_infra_compose_files_preserve_data_paths_and_network():
    import yaml
    postgres = yaml.safe_load((ROOT / "infra/postgres/compose.yaml").read_text())
    assert set(postgres["services"]) == {"postgres"}
    assert postgres["networks"]["icarus"]["external"] is True
    assert "services/rela-mem/postgres" in str(postgres["services"]["postgres"]["volumes"])
    falkordb = yaml.safe_load((ROOT / "infra/falkordb/compose.yaml").read_text())
    assert set(falkordb["services"]) == {"falkordb"}
    assert "services/rela-mem/falkordb" in str(falkordb["services"]["falkordb"]["volumes"])

def test_rela_mem_compose_is_app_only():
    import yaml
    compose = yaml.safe_load((ROOT / "apps/rela-mem/docker-compose.yaml").read_text())
    assert set(compose["services"]) == {"rela-mem"}
    assert compose["networks"]["icarus"]["external"] is True
```

- [ ] **Step 2: 运行测试确认失败**

Run: `apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q`
Expected: FAIL，`FileNotFoundError: infra/postgres/compose.yaml`。

- [ ] **Step 3: 实现 Compose 文件与 `infra/README.md`**

按契约创建；`infra/README.md` 说明共享网络、数据路径、local/remote 切换、`icarus.local.toml` 与 `.env` 的职责。

- [ ] **Step 4: 运行测试确认通过**

Run: `apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q`
Expected: PASS。

- [ ] **Step 5: 本地真实 smoke（需要 Docker）**

Run:
```bash
ICARUS_DATA_DIR=<absolute-data-dir> RELA_MEM_PG_PASSWORD=<secret> \
RELA_MEM_FALKORDB_PASSWORD=<secret> \
./bin/icarus start postgres
./bin/icarus start falkordb
./bin/icarus start rela-mem
curl -fsS http://127.0.0.1:8767/healthz
```
Expected: 三个组件均 running/reachable；`healthz` 返回 200。记录实际结果；失败时保留原始错误，不用 mock 结果替代。

---

### Task 10: 应用配置的环境变量覆盖

**Files:**
- Modify: `apps/agent/src/agent_app/config/config_loader.py`
- Modify: `apps/agent/test/agent_app/config/test_config_loader.py`
- Modify: `apps/rela-mem/src/config.py`
- Modify: `apps/rela-mem/test/test_config.py`
- Modify: `.example.env`
- Modify: `.gitignore`
- Modify: `apps/rela-mem/settings.example.json`
- Modify: `apps/rela-mem/README.md`

**Interfaces:**
- Agent：`ICARUS_MEM0_ENDPOINT` → `runtime.plugin_config.memory.endpoint`；`ICARUS_OPENKB_ENDPOINT` → `runtime.plugin_config.knowledge.endpoint`；`ICARUS_RELATIONSHIP_ENDPOINT` → `runtime.plugin_config.relationship.endpoint`。缺失插件配置节时创建。
- rela-mem：`RELA_MEM_PG_HOST/PORT/DATABASE/USER/SCHEMA` 覆盖 `postgres`；`RELA_MEM_FALKORDB_HOST/PORT/GRAPH` 覆盖 `falkordb`；端口转 int。
- 环境变量优先于文件值；未设置时保持文件值。

- [ ] **Step 1: 写失败测试**

```python
# apps/agent/test/agent_app/config/test_config_loader.py（追加）
def test_endpoint_environment_overrides_settings(tmp_path, monkeypatch):
    from agent_app.config.config_loader import get_config
    import json
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({
        "openai_base_url": "https://example.invalid",
        "anthropic_base_url": "https://example.invalid",
        "model_settings": [{"name": "m", "model_name": "m", "context_window": 1,
                            "max_tokens": 1, "temperature": 0, "default_think_level": "low"}],
        "model_roles": {"agent": "m", "compaction": "m",
                        "skill_generation": "m", "title": "m"},
        "runtime": {"plugin_config": {"memory": {"user_id": "u", "agent_id": "a"}}},
    }), encoding="utf-8")
    monkeypatch.setenv("ICARUS_MEM0_ENDPOINT", "http://mem0:8888")
    monkeypatch.setenv("ICARUS_RELATIONSHIP_ENDPOINT", "http://rela:8767")
    config = get_config(path)
    assert config.runtime.plugin_config["memory"]["endpoint"] == "http://mem0:8888"
    assert config.runtime.plugin_config["relationship"]["endpoint"] == "http://rela:8767"


# apps/rela-mem/test/test_config.py（追加）
def test_connection_environment_overrides_settings(tmp_path, monkeypatch):
    from config import load_settings
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(configuration()), encoding="utf-8")
    monkeypatch.setenv("RELA_MEM_PG_HOST", "postgres.example.invalid")
    monkeypatch.setenv("RELA_MEM_PG_PORT", "5432")
    monkeypatch.setenv("RELA_MEM_PG_DATABASE", "rela_mem_remote")
    monkeypatch.setenv("RELA_MEM_FALKORDB_HOST", "falkordb.example.invalid")
    monkeypatch.setenv("RELA_MEM_FALKORDB_PORT", "6379")
    settings = load_settings(path, {
        "RELA_MEM_API_TOKEN": "token",
        "RELA_MEM_FALKORDB_PASSWORD": "graph-password",
        "RELA_MEM_PG_PASSWORD": "pg-password",
    })
    assert settings.postgres.host == "postgres.example.invalid"
    assert settings.postgres.port == 5432
    assert settings.postgres.database == "rela_mem_remote"
    assert settings.falkordb.host == "falkordb.example.invalid"
    assert settings.falkordb.port == 6379
```

- [ ] **Step 2: 运行测试确认失败**

Run:
```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/config/test_config_loader.py -q
apps/rela-mem/.venv/bin/python -m pytest apps/rela-mem/test/test_config.py -q
```
Expected: 新测试失败（端点/连接值仍是文件默认值）。

- [ ] **Step 3: 实现两个配置加载器的环境覆盖**

Agent：在 `get_config` 读取 JSON 后、构造 `ConfigModel` 前调用 `_apply_endpoint_overrides(data, os.environ)`。
rela-mem：在 `load_settings` 合并 Secret 后、`Settings.model_validate` 前调用 `_apply_connection_overrides(data, environ)`；端口用 `int()`，非法值抛出可读错误。

- [ ] **Step 4: 更新环境模板与文档**

`.example.env` 增加空值：

```dotenv
# rela-mem service and its middleware
RELA_MEM_API_TOKEN=
RELA_MEM_PG_PASSWORD=
RELA_MEM_FALKORDB_PASSWORD=
RELA_MEM_SETTINGS_FILE=
```

`.gitignore` 增加 `icarus.local.toml`。`apps/rela-mem/settings.example.json` 的 `falkordb.host`/`postgres.host` 改为 `127.0.0.1`，端口改为宿主机端口 `16379`/`15432`，并在 README 说明 Compose 内由环境变量覆盖。README 更新启动顺序（先 `icarus start postgres falkordb` 或配置远程地址，再 `icarus start rela-mem`）。

- [ ] **Step 5: 运行测试确认通过**

Run:
```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/config/test_config_loader.py -q
apps/rela-mem/.venv/bin/python -m pytest apps/rela-mem/test/test_config.py -q
```
Expected: PASS。

---

### Task 11: 远程模式验收、文档与最终检查

> 实际验收使用自建隔离数据库完成本地容器和 native+remote mixed 两种组合，不是下文原始
> “用户授权远端库”操作。下方命令与未勾选框保留为原计划，不声称每条都执行；真实日志及收尾归 execution。
> 本次文档更新不修改 execution 或 Feishu；控制器已另行更新原 Feishu，最终收尾仍由其负责。

**Files:**
- Modify: `scripts/tests/test_lifecycle.py`（远程组合用例）
- Modify: `spec/2026-10-19-agent-relationship-integration.md`
- Create: `docs/spec/2026-10-19-component-lifecycle/arch.md`（实施完成后）
- Modify: `docs/spec/2026-10-19-component-lifecycle/design.md`（如有实施偏差）

**Interfaces:**
- Consumes: Task 1–10 的全部产物。
- Produces: 远程 PostgreSQL/FalkorDB 的真实 smoke 记录；最终架构文档；跨应用边界更新。

- [ ] **Step 1: 增加远程组合测试**

```python
def test_remote_middleware_does_not_require_local_compose(self):
    postgres = Component("postgres", "middleware", "remote", "compose",
                         host="postgres.example.invalid", port=5432, probe=Probe("tcp"))
    falkordb = Component("falkordb", "middleware", "remote", "compose",
                         host="falkordb.example.invalid", port=6379, probe=Probe("tcp"))
    rela = component("rela-mem", provisioning="compose", compose="x.yaml", requires={
        "postgres": Dependency("postgres"),
        "falkordb": Dependency("falkordb"),
    })
    lifecycle, compose, _ = self.make(
        {"postgres": postgres, "falkordb": falkordb, "rela-mem": rela},
        probe_func=lambda c, e: type("P", (), {"ok": True, "state": "reachable", "detail": ""})(),
    )
    self.assertEqual(lifecycle.start(["rela-mem"]), 0)
    self.assertEqual(compose.calls, [("up", "rela-mem")])
```

- [ ] **Step 2: 运行测试**

Run: `python3 scripts/tests/test_lifecycle.py -v`
Expected: PASS。

- [ ] **Step 3: 真实远程 smoke（需用户授权的地址与凭据）**

准备（不提交）：

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

`.env` 填写 `RELA_MEM_PG_PASSWORD`、`RELA_MEM_FALKORDB_PASSWORD`、`RELA_MEM_API_TOKEN`。运行：

```bash
./bin/icarus status postgres
./bin/icarus status falkordb
./bin/icarus start rela-mem
curl -fsS http://127.0.0.1:8767/healthz
```

Expected: `status` 显示 remote + reachable；`rela-mem` 启动成功；未创建本地数据库容器。记录实际输出；凭据错误时如实报告并指向具体依赖。

- [ ] **Step 4: 更新文档**

- 在 `spec/2026-10-19-agent-relationship-integration.md` 的边界处注明：rela-mem 已由仓库基建统一生命周期管理，原“不扩展根全栈启动列表”仅描述接入当时的范围。
- 实施完成后创建 `arch.md`，按实际源码与测试描述最终架构；若实现与 `design.md` 有偏差，先更新设计文档并说明原因。

- [ ] **Step 5: 最终验证**

Run:
```bash
python3 scripts/tests/test_registry.py
python3 scripts/tests/test_graph.py
python3 scripts/tests/test_probes.py
python3 scripts/tests/test_injection.py
python3 scripts/tests/test_compose_runtime.py
python3 scripts/tests/test_process_runtime.py
python3 scripts/tests/test_lifecycle.py
python3 scripts/tests/test_icarus_control.py
apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_app/config/test_config_loader.py -q
apps/rela-mem/.venv/bin/python -m pytest apps/rela-mem/test/test_config.py -q
make test-agent
make test-rela-mem
git diff --check
```
Expected: 新测试与受影响套件通过；既有环境缺失或已知红基线按仓库规则单独报告，不用 skip/continue-on-error 掩盖。另检查：

```bash
git check-ignore -v icarus.local.toml
git grep -nE 'RELA_MEM_(PG|FALKORDB)_PASSWORD=[^ ]+' || true
```
Expected: `icarus.local.toml` 被忽略；被跟踪文件中没有明文密码匹配。

## Acceptance and Rollback

- **Acceptance:** 以 execution 中真实命令与结果验收，不要求照抄旧 illustrative 测试；本地 rela-mem + 本地两个中间件与 native rela-mem + 隔离测试库登记 remote 均已有真实通过证据。用户特定远程部署未作认证/写入验收，不补写为通过；最终审查/文档收尾单列。`external`/`orphaned`/PID 校验等行为由实际测试固定。
- **Rollback:** 回退控制面与应用配置读取代码即可；远程服务不受影响；本地数据卷与 `$ICARUS_DATA_DIR/services/rela-mem/...` 不删除。回滚不得隐式迁移、重绑或删除业务数据。
- **Data safety:** 不自动清空数据库；若用户要求从空库开始，必须由用户显式清理，并在执行前确认目标与备份。
