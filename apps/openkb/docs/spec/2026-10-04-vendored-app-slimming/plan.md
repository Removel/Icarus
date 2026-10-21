# OpenKB 外围裁剪与开发入口｜第一批实施计划

> **For agentic workers:** 使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐任务执行。步骤使用 checkbox 跟踪；本文不授权执行删除、服务重启或 Git 提交。

> **执行记录：** [第一批结果与未验证项](execution.md)。下列步骤保留原计划；实际变更、裁决和非绿基线以执行记录为准。

**Goal:** 移除 OpenKB 的上游分发元数据与演示，迁出开发导航 skill，同时保留 CLI/API/编译/Deck/Skill Factory/Workbench、完整测试与锁文件，提供统一的私有开发测试入口。

**Architecture:** `openkb/` 保持现有源码结构及共享 helper，不在清理中拆 CLI 或删后端能力。开发环境使用应用自己的 uv.lock 和 `.venv`；runtime deck 三件套继续留包内并由原 force-include 打包；仓库根仅聚合测试与生命周期控制。

**Tech Stack:** Python 3.12（CI/dev）、uv、pytest、FastAPI、Click、Hatchling、Node 20 Workbench 构建、Docker Compose。

**Spec:** [OpenKB 裁剪设计](arch.md)；[跨应用约定](../../../../../spec/2026-10-04-vendored-app-slimming.md)；[仓库基建计划](../../../../../docs/spec/2026-10-04-repository-infra/plan.md)。

## 全局约束

- 来源冻结在 `VectifyAI/OpenKB@ff54396e575ee6feb0113b631a34caa082b441cc`，保留 Icarus 补丁和维护能力，不回到未修改发行版。
- 第一批不删 `frontend/`、CLI、REST、内部 Agent、Deck/Skill Factory/watch 能力、tests 或数据库/知识库用户数据。
- 根 `.agents/skills/openkb/` 为开发导航 skill；三个 deck skill 仍 `apps/openkb/skills/`，不改 Docker build context。
- `uv.lock` 和 pyproject 精确 pin 保留；`dev` 与 `api` 一起安装，`api` 当前是 `web` 的兼容别名，不是“更轻依赖”。
- Python package 最低 3.10 不变；Icarus 安装器最低 3.11；CI/dev 验证采用 3.12，不承诺未经验证平台全绿。
- 应用 SDK/dev 依赖不进入 Agent、Gateway 或根 `.venv`；Docker 当前 pip 安装不宣称消费 uv.lock。
- 默认离线子集必过，`--full` 完整套件真实返回错误码；不跳过或放宽既有 `test_file_size.py`。
- 不运行真实模型、上游发布、Skill Factory 发布或删除现有知识库；未经额外授权不创建分支/提交/推送。

## Review Focus

1. **混淆仓库分发 manifest 与运行时产物**：只删上游根 `.claude-plugin`，保留临时 KB marketplace 生成/导出及测试，任务一验证。
2. **开发 skill 搬走连带删 runtime deck**：真实三件套、扫描顺序与 wheel 资源必须存在，任务二/四 source 与 wheel 验收。
3. **开发主机没有 CLI 或容器路径不能读取**：导航 skill 说明容器 CLI 执行及路径映射，不把 `/data/kbs` 当宿主路径；任务二引用/命令说明审查。
4. **锁文件与 extras/环境不一致**：`uv sync --locked` 安装 `dev,api` 到 app `.venv`，不是无 extra 重同步后卸掉测试工具；任务三假 uv 参数与 lock 校验。
5. **清理时误修模块行数或弱化断言**：完整套件、失败 nodeid、文件哈希分别比较；任务四确认既有超限未被隐藏，不用全绿掩盖旧失败。

---

## 范围与文件所有权

保留：`openkb/`（含 cli.py、api.py、helpers、agent/deck/skill/prompts/templates）、`skills/{openkb-deck-neon,openkb-deck-editorial,openkb-html-critic}/`、`tests/`、`frontend/`、`pyproject.toml`、`uv.lock`、`Dockerfile.icarus`、`docker-compose.yaml`、`config.yaml.example`、`assets/`、`docs/golden-principles.md`、`docs/spec/`、`AGENTS.md`/`CLAUDE.md`、LICENSE/MODIFICATIONS、四个 Icarus 运行脚本。

删除：仓库上游 `.claude-plugin/`、嵌套 `.github/`、`examples/`、当前未被调用的 `scripts/prepare_local_env.py`、吸收规则后的 app `.gitignore`。移动：仅 `skills/openkb/` 完整目录。

本计划不把 API 所引用的 CLI helpers 抽新层；移除重构动机不足的 3500 行 cli.py 是另一独立项目。实际 `agent/query.py` 的工具为 `list_skills/read_skill`，不是运行 ShellTool 执行所有命令。

## Task 1：保留集合与外围删除

**Files**

- Create: `apps/openkb/tests/test_icarus_layout.py`。
- Delete: `apps/openkb/.claude-plugin/`、`.github/`、`examples/`、`.gitignore`、`scripts/prepare_local_env.py`。
- Modify: `apps/openkb/README.md`、`MODIFICATIONS.md`、`AGENTS.md`。
- Keep/Test: `tests/test_marketplace.py`、`tests/test_skill_cli.py`、`tests/test_skill_chat_slash.py`、`tests/test_api.py`。

**Interfaces**

- Consumes: 根 `.gitignore` 已承接 web bundle、raw/wiki、缓存等必要规则。
- Produces: 干净的外围结构，但保留 runtime marketplace 导出与 API 创建/查询/维护能力。

- [ ] **Step 1：先写保留与删除断言。**

```python
from pathlib import Path
from hashlib import sha256

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]


def test_icarus_keeps_runtime_build_test_and_maintenance_inputs():
    for relative in (
        "LICENSE", "pyproject.toml", "uv.lock", "Dockerfile.icarus",
        "docker-compose.yaml", "config.yaml.example",
        "openkb/cli.py", "openkb/api.py", "openkb/api_helpers.py",
        "frontend/package.json", "frontend/package-lock.json",
        "docs/golden-principles.md", "assets/openkb-architecture.webp",
        "scripts/icarus-compose.sh", "scripts/icarus_compose.py",
        "scripts/icarus-container-start.sh", "scripts/prepare_managed_kb.py",
        "tests/test_marketplace.py", "tests/test_file_size.py",
    ):
        assert (APP / relative).is_file(), relative
    assert sha256((APP / "LICENSE").read_bytes()).hexdigest() == (
        "27a01683911f50a44061b151de07a590f7e50712ff8a97d1cdff9c395a8db732"
    )


def test_upstream_distribution_and_examples_are_removed():
    for relative in (".claude-plugin", ".github", "examples", "scripts/prepare_local_env.py"):
        assert not (APP / relative).exists(), relative
```

Run: `cd apps/openkb && .venv/bin/python -m pytest tests/test_icarus_layout.py -q`；没有环境则先依 Task 3 安装，不修改生产依赖。预期删除断言 red，其余保留资源仍存在。

- [ ] **Step 2：核对待删输入后删除明确跟踪文件。**

核对 `git ls-files apps/openkb/.claude-plugin apps/openkb/.github apps/openkb/examples`；清理不触及用户 ignored raw/wiki/config。保留测试中的 marketplace.json、README/docs 样本是 tmp_path 的 runtime/fixture，不因为同名匹配删这些断言。

`prepare_local_env.py` 没有当前控制面调用，但来源是导入期 glue，不把无调用者误称为 confirmed upstream 源码。必要 ignore 已进入根 `.gitignore` 才删 app ignore；不要删 `uv.lock` 或 `config.yaml.example`。

- [ ] **Step 3：更新 README、开发地图与声明。**

- README 去掉所有指向已删 examples 的本地链接（commands/skills/slides/REST 示例），保留真实 API `/docs` 与各运行模块说明；架构图保留。
- 不再指引“向上游发布 plugin/skill”作为 Icarus 的默认流程；说明 CLI 在容器内可用，runtime API 才是 Agent/WebUI 的边界。
- `AGENTS.md` 保留 exact pin、locks/mutation、模块地图与本地测试规则；加入新的 app-owned dev/test 命令，并把 SDD 指向 `docs/spec/`。`CLAUDE.md` 继续是 `@AGENTS.md`，不复制另一份。
- MODIFICATIONS 追加冻结、删除目录、已迁 skill、保留功能及检查证据，记录 full upstream SHA，不删除原补丁历史。

- [ ] **Step 4：跑 runtime marketplace 及 REST 定向测试。**

```bash
cd apps/openkb
.venv/bin/python -m pytest tests/test_icarus_layout.py tests/test_marketplace.py tests/test_skill_cli.py tests/test_skill_chat_slash.py tests/test_api.py -q
```

预期删除根分发清单不影响 runtime 临时知识库导出。若新失败，定位真实路径读取，先保全资源；不放宽原测试让删除绿。

## Task 2：迁移开发导航 skill，保全 runtime deck 与包资源

**Files**

- Move: `apps/openkb/skills/openkb/` → `.agents/skills/openkb/`（SKILL.md 及 references 两文件）。
- Create: `.agents/skills/openkb/LICENSE`（复制原 `apps/openkb/LICENSE`）；更新根 `.agents/README.md` 的来源说明。
- Modify: 导航 `SKILL.md`/references 中执行环境说明；app README skills 路径。
- Test: `apps/openkb/tests/test_icarus_layout.py`、`test_deck_prompt.py`、`test_deck_neon_prompt.py`、`test_skills.py`、`test_skill_runner.py`。

**Interfaces**

- Consumes: 根 `.agents/skills` 开发约定。
- Produces: 开发 skill 可定位 CLI 与 KB 目录；包内三个 runtime SKILL.md 原内容和 wheel force-include 不变。

- [ ] **Step 1：增加位置与真实 deck 发现测试。**

```python
import openkb.agent.skills as skill_scan

DECK_NAMES = {"openkb-deck-neon", "openkb-deck-editorial", "openkb-html-critic"}


def test_development_navigation_skill_is_separate_from_runtime_decks():
    target = ROOT / ".agents/skills/openkb"
    for relative in ("SKILL.md", "references/commands.md", "references/wiki-schema.md", "LICENSE"):
        assert (target / relative).is_file()
    assert (target / "LICENSE").read_bytes() == (APP / "LICENSE").read_bytes()
    assert not (APP / "skills/openkb").exists()
    for name in DECK_NAMES:
        assert (APP / "skills" / name / "SKILL.md").is_file()


def test_real_bundled_decks_remain_discoverable(tmp_path, monkeypatch):
    # Do not inherit real user skills; unlike test_skills.py, use real bundle roots.
    monkeypatch.setattr(skill_scan, "DEFAULT_SKILL_ROOTS", ())
    entries = skill_scan.scan_local_skills(tmp_path)
    names = {entry["name"] for entry in entries}
    assert DECK_NAMES <= names
    for entry in entries:
        if entry["name"] in DECK_NAMES:
            assert Path(entry["path"]).is_dir()
```

Run 定向 layout 测试，迁移之前新位置缺失应 red。原 `test_skills.py` 的 autouse 屏蔽真实 bundle roots，不能只靠那份套件声称三件套可用。

- [ ] **Step 2：整目录移动并补本地执行环境说明。**

原导航 skill 没有自带 LICENSE；新独立 bundle 复制上游 app LICENSE，root notice 登记来源。前置说明：开发主机只有 skill 不代表 CLI 已装。使用服务容器路径时命令：

```bash
bash apps/openkb/scripts/icarus-compose.sh exec -T openkb openkb status
```

命令从 Icarus 根执行；若 `/data/kbs/...` 是输出的容器路径，后续 `cat/ls/openkb list/query` 也在容器执行，或显式映射至 `$ICARUS_DATA_DIR/services/openkb/kbs`，不能把它交给宿主 Read。开发 `.venv` CLI 也可运行，但必须用明确的 `OPENKB_CONFIG_DIR`/KB 选择，不自行初始化另一个默认知识库。保留相对 references 路径，新增说明不改 KB Schema 或内容。

- [ ] **Step 3：打包配置不动。**

确认原 `pyproject.toml` force-include **已经且仅有**三条 `skills/openkb-deck-*`/`openkb-html-critic`；迁出 navigation 不需删不存在的 entry。`BUNDLED_SKILL_ROOTS` 不追加根 `.agents`，不让开发资料自动成为生产模型 skill。Docker context 仍 `.`，不存在跨根 COPY。

- [ ] **Step 4：运行真实 bundle 定向回归。**

```bash
cd apps/openkb
.venv/bin/python -m pytest tests/test_icarus_layout.py tests/test_deck_prompt.py tests/test_deck_neon_prompt.py tests/test_skills.py tests/test_skill_runner.py -q
```

三份 SKILL.md 比迁移前哈希相同；references 两文件及作者来源完整。wheel 资源验证在 Task 4；不通过真实模型生成 Deck 作为本批默认门槛。

## Task 3：私有锁定环境与测试脚本

**Files**

- Create: `apps/openkb/scripts/install.sh`、`scripts/test.sh`、`tests/test_dev_scripts.py`。
- Keep: `pyproject.toml`、`uv.lock`（本任务不 bump deps）、`tests/conftest.py`。
- Docs: README 的 dev/完整套件说明。

**Interfaces**

- `install.sh --dev`：锁定安装 dev+api 到 app `.venv`，无需 Secret/Docker，不修改 uv.lock。
- `test.sh [--full]`：默认固定离线子集，full 完整 tests；缺环境=1、未知参数=2、pytest 非零直接退出。
- 根 `make test-openkb` 与 CI 都只调用应用脚本；根接口由根计划衔接。

- [ ] **Step 1：写假解释器/uv 的 runner 和安装参数测试。**

```python
import os
import shutil
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def test_runner_keeps_failure_from_private_python(tmp_path):
    app = tmp_path / "repo with spaces/apps/openkb"
    (app / "scripts").mkdir(parents=True)
    (app / ".venv/bin").mkdir(parents=True)
    shutil.copy2(APP / "scripts/test.sh", app / "scripts/test.sh")
    log = tmp_path / "calls.txt"
    fake = app / ".venv/bin/python"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nexit 7\n')
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(app / "scripts/test.sh")], cwd=tmp_path,
        env={**os.environ, "CALL_LOG": str(log)}, capture_output=True, text=True,
    )
    assert result.returncode == 7
    assert "-m pytest" in log.read_text()
    assert "compileall" not in log.read_text()
```

增加 installer 的 fake `uv`/`PYTHON` 捕获：完整参数必须含 `sync --locked --project <app> --extra dev --extra api --python <python>`；传入 `UV_PROJECT_ENVIRONMENT` 为别的目录时脚本必须覆盖为 `<app>/.venv`；确认无 root `.venv`、无 `.env` 读取。默认/全量/未知参数/缺环境测试分别验收。

- [ ] **Step 2：实现 locked installer。**

```bash
#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin=${PYTHON:-python3}
case "${1:-}" in
  --dev) ;;
  *) echo "Usage: $0 --dev" >&2; exit 2 ;;
esac
if [ "$#" -ne 1 ]; then
  echo "Usage: $0 --dev" >&2; exit 2
fi
if ! "$python_bin" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
  echo "Icarus development requires Python 3.11 or newer" >&2; exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "OpenKB development requires uv; install uv before retrying" >&2; exit 1
fi
UV_PROJECT_ENVIRONMENT="$app_dir/.venv" uv sync --locked --project "$app_dir" --extra dev --extra api --python "$python_bin"
```

使用既有 app uv.lock；不自动 `uv lock` 改版本，不 uv workspace，不复制所有 lock 到根。API extra 提供 FastAPI；不要因 transitive 已有 uvicorn 就省略 API extra。

- [ ] **Step 3：实现 runner。**

```bash
#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"
case "${1:-}" in
  "") tests=(tests/test_api.py tests/test_remove.py tests/test_config.py tests/test_marketplace.py tests/test_skills.py tests/test_deck_prompt.py tests/test_deck_neon_prompt.py tests/test_icarus_layout.py tests/test_dev_scripts.py) ;;
  --full) tests=(tests) ;;
  *) echo "Usage: $0 [--full]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--full]" >&2; exit 2
fi
if [ ! -x "$python_bin" ]; then
  echo "OpenKB development environment is missing. Run: icarus install openkb --dev" >&2; exit 1
fi
cd "$app_dir"
"$python_bin" -m pytest "${tests[@]}" -q
"$python_bin" -m compileall -q openkb scripts tests
```

不要用无 extra 的 `uv run` 重新 sync 后卸载 pytest；私有 `.venv/bin/python` 已有 editable 本地包，直接调用。PR6 合入后，在默认列表**显式**加 `tests/test_report_ops.py`、`tests/test_managed_kb_template.py`；其 source/history 修改已有对应现存测试，需保留。未合入前不添加不存在测试，不写 `if exists` 偷跳。

- [ ] **Step 4：验证入口及锁不变。**

```bash
bash -n apps/openkb/scripts/install.sh apps/openkb/scripts/test.sh
bash apps/openkb/scripts/install.sh --dev
bash apps/openkb/scripts/test.sh
bash apps/openkb/scripts/test.sh --full
uv lock --check --project apps/openkb
git diff -- apps/openkb/pyproject.toml apps/openkb/uv.lock
git diff --check
```

完整套件保留失败；静态检查的当前超限 api.py/api_helpers.py 仅是审查提示，必须实跑 `test_file_size.py` 并按 nodeid 对比裁剪前后，不修改 _GRANDFATHERED 或加豁免过门禁。

## Task 4：源码、wheel、镜像与原始声明验收

**Files**

- Record: app 验证记录、MODIFICATIONS；不改 API/CLI 行为。
- Keep: Dockerfile.icarus 的 Node stage、`openkb/web` artifacts、`.[web]` 安装和 Compose 服务。

**Interfaces**

- Consumes: 三个前任务形成的锁定 dev 环境、保留包资源与开发 skill。
- Produces: 第一批构建/回归证据，非第二批 UI 删除许可。

- [ ] **Step 1：确认 API helpers 仍可导入、应用仍可创建。**

```bash
app_dir="$PWD/apps/openkb"
test_home=$(mktemp -d)
HOME="$test_home" OPENKB_CONFIG_DIR="$test_home/config" OPENKB_KB_ROOT="$test_home/kbs" \
  "$app_dir/.venv/bin/python" -c 'import openkb.cli, openkb.api_helpers, openkb.documents; from openkb.api import create_app; app = create_app(); assert app is not None'
"$app_dir/.venv/bin/python" -m compileall -q "$app_dir/openkb" "$app_dir/scripts" "$app_dir/tests"
```

命令从仓库根执行；create_app 当前确切签名为 `create_app() -> FastAPI`。使用临时 config/HOME/KB，不指向真实用户知识库；本次不修改业务代码来迎合验收。

- [ ] **Step 2：wheel 资源检查，脱离 source checkout 验证。**

```bash
uv build --project apps/openkb --wheel --out-dir /tmp/icarus-openkb-wheel-check
```

在 zip 中逐项检查 `openkb/_skills/openkb-deck-neon/SKILL.md`、`openkb/_skills/openkb-deck-editorial/SKILL.md`、`openkb/_skills/openkb-html-critic/SKILL.md`。导航 skill 不再出现在 app source bundle roots（原来不在 wheel 内，不宣称本批减少 wheel 内该条目）。LICENSE 和声明需在输出中可追溯。

- [ ] **Step 3：镜像完整构建，不用旧业务镜像代替源码。**

```bash
docker build -t icarus-openkb:slim-check -f apps/openkb/Dockerfile.icarus apps/openkb
```

无挂载地在容器 `/tmp` 工作目录检查已安装 package `_skills` 三资源和 `/app/openkb/web/index.html`，防止源码相对目录掩盖 wheel 缺文件。离线 API smoke 用临时配置/数据和不冲突端口，不接现有用户 KB；验证 `/openapi.json`、鉴权和 Source/Page 服务基础端点。

- [ ] **Step 4：失败集合与链接/声明收口。**

核对前后整个保留 `openkb/`、三个 deck SKILL.md、tests 既有文件内容哈希；只允许布局/path 测试新增和维护说明变化。重新跑 default/full，逐项记录。root notice 链接 app 以及根导航 skill，源 SHA 与 OpenKB LICENSE 校验一致。

## PR6、第二批与回滚

PR6 合入前，保留当前模型/API 状态；合入后保留 report/source ID/managed-KB template 改动及 `docs/spec/2026-10-01-quality-report-management/`，更新默认固定子集并收敛根重复 job。

第二批 UI 删除另写 plan，包含 Docker Node stage、静态挂载、依赖 extra、WebUI 能力差异与验收。不因为业务 backend 仍有 chat/deck/skill/watch 就认定新 WebUI 已支持全部界面。

回滚只回滚源码/配置；不删除/降级 `$ICARUS_DATA_DIR/services/openkb/{config,kbs,backups}`。建议外围裁剪、导航 skill 迁移、dev 脚本三逻辑单元独立审查，只有获授权才提交。

## 计划自查记录

只读调查确认 runtime marketplace 是临时 KB 产物、三条 force-include 本来就存在、query 使用函数工具；这些结论已写入计划，尚未执行安装、删除或镜像构建。基线不能依据静态行数或代理报告替代真实命令输出。
