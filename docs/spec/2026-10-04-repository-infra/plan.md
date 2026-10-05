# Vendored Apps 与仓库基建｜第一批实施计划

> **For agentic workers:** 使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐任务执行。步骤使用 checkbox 跟踪。本文是实施计划，不表示已经执行。

> **执行记录：** [第一批结果、审查修复与环境限制](execution.md)。实际验收状态以此记录为准；镜像/Actionlint/隔离服务检查现已补齐，全量既有红基线仍原样保留。

**Goal:** 在不改变 Mem0/OpenKB 业务协议、持久化路径和现有 UI 的前提下，建立统一的仓库级开发、测试、构建与文档入口，并完成外围裁剪。

**Architecture:** 根目录只负责跨应用控制与验证；应用继续拥有源码、运行镜像、依赖声明、锁文件和私有开发环境。应用裁剪按各自计划执行，根计划负责衔接、公共约定、CI 和最终验收；不建立新的业务 `infra` App 或聚合 Python 包。

**Tech Stack:** Bash、Python 3.12 开发/CI 环境、pytest、uv、Docker Compose、GitHub Actions、EditorConfig、Markdown。

**Spec:** [仓库基建设计](arch.md)；[跨应用约定与索引](../../../spec/2026-10-04-vendored-app-slimming.md)。

## 全局约束

- 第一批保留 Mem0 dashboard、OpenKB frontend、所有服务 API、鉴权、数据库迁移、管理员脚本、内置 deck skill 和两个应用测试套件。
- 冻结的是上游导入来源，不是停止维护 Icarus 补丁或安全修复；不得用 PyPI 上游包替换修改后的本地 SDK。
- 开发 skill 放 `.agents/skills/`；生产基础 skill 源目录为 `skills/`，本批只有约定，不自动安装或注入 Agent。
- 运行环境仍是 Docker；新增 `.venv` 仅服务于测试和开发，不安装到根环境或 Agent/Gateway 环境。
- `make test-mem0`、`make test-openkb` 是必过的离线契约子集；`*-full` 执行完整套件并原样返回退出码，不隐藏红基线。
- 不统一全仓 lint/format，不抽公共 Docker 基础层，不批量重排源码或 renormalize 工作区。
- 保留 Apache-2.0、作者版权、来源 full SHA 与修改声明；不继承上游发布凭据、CLA/accepted-issue/vouch 门禁。
- 不创建分支、提交、推送、删除工作区外资料或重启用户服务，除非另外获得授权。
- 日期按首次进入 Git 的日期确定；本文及两份应用计划尚未提交，首次提交时统一核对，之后名称固定。

## Review Focus

1. **裁剪误伤保留内容**：测试夹具、运维脚本、许可证、SDK JSON 资源不能因目录名字像外围而删除；由两份应用计划的保留集合测试约束。
2. **安装入口造成环境污染**：`install <service> --dev` 不需运行 Secret、不启动服务、不触碰根 `.venv`；Task 3 测试具体命令序列及异常退出。
3. **测试环境串包**：Mem0/OpenKB 分别从应用目录、各自解释器运行，pytest 退出码不被 compile 或日志上传覆盖；Task 2/4 用假解释器与故障注入测试。
4. **CI 漏跑或重复执行**：修改根脚本、Makefile、锁文件、构建输入必须触发相应 job；PR6 合入后只保留一套 Mem0/OpenKB job；Task 4 检查触发路径与 job ownership。
5. **文本及许可损伤**：快照尾空格、二进制、许可证、技能相对链接保持原样；Task 1/5 增加属性检查、内容哈希和链接验收。

---

## 0. 范围、文档落点与依赖

| 部分 | 计划文件 | 所有权 |
| --- | --- | --- |
| 根公共约定、安装调度、测试聚合、CI、最终验收 | 本文 | 跨应用控制面 |
| Mem0 外围裁剪、开发 skill 迁移、私有环境与应用回归 | `apps/mem0/docs/spec/2026-10-04-vendored-app-slimming/plan.md` | Mem0 |
| OpenKB 外围裁剪、导航 skill 迁移、私有环境与应用回归 | `apps/openkb/docs/spec/2026-10-04-vendored-app-slimming/plan.md` | OpenKB |
| 旧 UI 删除 | 第二批另写，不在本文执行 | PR6 合入且真实服务验收之后 |

根计划中的具体应用工作不复制第二份实现。推荐执行顺序：Task 1 → 两份应用计划 → Task 3 → Task 4 → Task 5；应用部分可独立验收，但都不修改根控制面。

初始事实：当前 `feature` 为 `b3837bed94e473f86d950079f386fd0b3e96fa84`；PR6 仍 OPEN，已审查的 head 为 `87d12c19bb900e6ce7b21b411ffb5039ecb5018c`。第一批可以基于当前 feature 完成，不 cherry-pick PR6，不提前引用不存在的 WebUI 或报告测试。

- [ ] 执行前记录 `git status --short --branch`、`git rev-parse HEAD`、`gh pr view 6 --repo Removel/Icarus --json state,headRefOid,mergedAt`。
- [ ] 若 PR6 已合入，按实际文件清单登记新增测试和 docs/spec，再执行本文的衔接步骤；不覆盖其 history/source/report 改动。
- [ ] 用同一环境在裁剪前保存两应用默认子集、全量套件和相关 Agent 契约测试的输出、退出码、具体失败 nodeid。缺依赖属于环境未齐备，不称为测试通过。
- [ ] 证据保存到忽略的本地日志目录；供后续审查的摘要包含命令、HEAD、Python/工具版本、退出码和失败集合，不保存 Secret。

## Task 1：文本、SDD 与治理约定

**Files**

- Create: `.editorconfig`、`CONTRIBUTING.md`、`.agents/README.md`、`skills/README.md`。
- Create: `docs/templates/spec/root.md`、`docs/templates/spec/arch.md`、`docs/templates/spec/plan.md`。
- Modify: `.gitattributes`、`.gitignore`、`AGENTS.md`、`README.md`。
- Test: `scripts/tests/test_repository_infra.py`（新增 pytest 函数；不改写既有 unittest 控制面套件）。

**Interfaces**

- Consumes: 根 `AGENTS.md` 的文档路径、层次边界与 Git 授权规则。
- Produces: 一套仓库约定；不新增运行时接口或自动 skill loader。

- [ ] **Step 1：先写会失败的属性与结构测试。**

```python
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def test_text_rules_preserve_snapshot_whitespace():
    result = subprocess.run(
        ["git", "check-attr", "eol", "whitespace", "--",
         "apps/tui/test/__snapshots__/probe.svg"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert "eol: lf" in result.stdout
    assert "whitespace: -trailing-space" in result.stdout
    editor = (ROOT / ".editorconfig").read_text(encoding="utf-8")
    assert "[apps/tui/test/__snapshots__/**]" in editor
    assert "trim_trailing_whitespace = false" in editor


def test_infra_docs_do_not_create_root_python_environment():
    assert not (ROOT / "requirements.txt").exists()
    assert not (ROOT / "pyproject.toml").exists()
    for name in ("root", "arch", "plan"):
        assert (ROOT / f"docs/templates/spec/{name}.md").is_file()
    assert (ROOT / "skills/README.md").is_file()
    assert (ROOT / ".agents/README.md").is_file()
```

Run: `apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q`。
预期：新增文件/属性未存在导致失败，而非 pytest 缺失或导入错误。

- [ ] **Step 2：添加文本配置，保留特殊文件。**

```ini
root = true

[*]
charset = utf-8
end_of_line = lf
insert_final_newline = true
trim_trailing_whitespace = true
indent_style = space

[*.py]
indent_size = 4

[*.{yml,yaml,json}]
indent_size = 2

[*.{js,mjs,ts,tsx,css}]
indent_size = 2

[Makefile]
indent_style = tab

[*.md]
trim_trailing_whitespace = false

[apps/tui/test/__snapshots__/**]
trim_trailing_whitespace = false
```

`.gitattributes` 使用独立 glob，不写 Git attributes 不支持的 brace 扩展：

```gitattributes
* text=auto eol=lf
*.png binary
*.webp binary
*.jpg binary
*.jpeg binary
*.gif binary
*.ico binary
*.pdf binary
*.woff binary
*.woff2 binary
apps/tui/test/__snapshots__/** whitespace=-trailing-space
```

`.gitignore` 补足缓存、生成包与数据；删应用 `.gitignore` 之前逐条核对不可丢失的规则：

```gitignore
*.pyo
*.egg-info/
.pytest_cache/
.ruff_cache/
.mypy_cache/
.coverage
.coverage.*
htmlcov/
node_modules/
apps/mem0/build/
apps/mem0/dist/
apps/mem0/server/history/
apps/mem0/server/.env
apps/openkb/build/
apps/openkb/dist/
apps/openkb/openkb/web/
apps/openkb/raw/
apps/openkb/wiki/
apps/openkb/docs/internal/
```

不把 `skills/`、`docs/spec/`、lock 或 LICENSE 纳入 ignore。不用全仓 `git add --renormalize .`，不主动触碰 `.env`、用户数据、快照或二进制。

- [ ] **Step 3：写治理文档与三种 SDD 模板。**

`CONTRIBUTING.md` 覆盖：feature 集成模型；根控制面/应用拥有依赖；dev 环境和 Docker 的区别；默认契约/全量测试区别；先小后全量再 compile/diff；不修无关失败；来源/许可管理；文档日期与边界。`AGENTS.md` 只加到上述文档的短链接，不复制长流程。

模板保留示意占位是模板本身的用途，但不替功能虚构文档：

- `root.md`：不可拆分的跨应用需求、各 app 职责、公共契约、应用计划链接、验收。
- `arch.md`：当前实现依据、组件及依赖方向、数据流、边界/错误、验证；明确区分未实施目标。
- `plan.md`：目标、设计链接、基线、任务的文件/接口、checkbox red-green 步骤、验收/回滚。
- 文档落点：应用内需求 → `apps/<app>/docs/spec/YYYY-MM-DD-<feature>/{arch.md,plan.md}`；仓库自有基建 → `docs/spec/YYYY-MM-DD-<feature>/{arch.md,plan.md}`；根 `spec/*.md` 只做跨应用约定与索引。

`.agents/README.md` 明确六个技能的来源、许可证、开发期用途；Platform/Vercel/OSS-to-Platform 技能是外部集成参考，不表示这些产品进入 Icarus 运行链，也不得绕过 Icarus SDD/分支授权规则。`skills/README.md` 明确 `<name>/SKILL.md` 约定、当前无生产基础 skill、不自动同步 `$ICARUS_DATA_DIR/skills`、不收 OpenKB 私有 deck 三件套。

- [ ] **Step 4：验证属性、二进制、ignore 与链接。**

```bash
apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q
git check-attr text diff eol -- apps/openkb/assets/openkb-architecture.webp
git check-ignore --no-index apps/mem0/server/history/probe.db apps/openkb/openkb/web/probe.js
bash -n scripts/install.sh scripts/test.sh
git diff --check
```

新增本地链接逐一验证真实目标；不要把拟实施路径当已存在链接。记录既有文件哈希，确认本任务只改约定文档和配置，没有整仓文件换行重写。

- [ ] **Step 5：审查边界。** 此任务不加 root ruff、root requirements、预提交自动修复、部署发布凭据或 CODEOWNERS 的虚构维护人。完成后仅在用户授权下独立提交。

## Task 2：执行两份应用计划，形成统一开发/测试接口

**Files**

- 应用文件与验证见两份 app plan；根控制面暂不改动。

**Interfaces**

- `bash apps/mem0/scripts/install.sh --dev` / `bash apps/openkb/scripts/install.sh --dev`：仅准备各自 `.venv`，不依赖 Docker/Secret，不启动服务。
- `bash apps/<app>/scripts/test.sh [--full]`：默认固定离线子集；`--full` 为完整保留套件；成功 0，真实失败原样非零；未知参数退出 2。
- 两应用支持 Python 3.12 的 CI/dev 环境；不抬高 SDK 自身的 `requires-python`。

- [ ] **Step 1：执行 Mem0 计划，验证保留 SDK/JSON/server/admin scripts/UI/tests 与五个 skill 迁移。**
- [ ] **Step 2：执行 OpenKB 计划，验证包/CLI/API/UI/lock/config/docs/tests 与三个 deck skill 均保留，只有导航 skill 迁移。**
- [ ] **Step 3：从仓库外目录调用两套 install/test 脚本，路径含空格，并用假解释器产生 pytest 退出 7；确认无误用 CWD、无吞错误。** 应用计划提供具体测试代码。
- [ ] **Step 4：确认不存在新增根 `.venv`；复核 install/test 使用本地修改版 package 而非远端发行版。**

此 Task 完成后才接根聚合入口，避免先让 `make test` 调用不存在的脚本。

## Task 3：安装控制面与 Makefile 聚合

**Files**

- Modify: `scripts/icarus/main.py:111-145`、`Makefile`、`scripts/test.sh`。
- Test: `scripts/tests/test_repository_infra.py`、`scripts/tests/test_icarus_control.py`（更新既有 install-all 预期，保留 unittest 风格）。
- Docs: `spec/2026-08-30-app-dependency-and-runtime-environments.md`、`README.md`、`CONTRIBUTING.md`。

**Interfaces**

- Consumes: Task 2 的两个应用 install/test 脚本。
- Produces: `icarus install <mem0|openkb> --dev`、全仓 `icarus install --dev`、四个测试 Make target。
- 保持无 `--dev` 的服务安装仅构建镜像；全仓 dev 安装仍构建两服务镜像，并额外准备两个私有测试环境。

- [ ] **Step 1：先加 pytest 控制面序列测试。** 测试文件使用与现有套件相同的 `scripts/icarus` 导入路径，不将控制面变为业务共享包。

```python
import sys
import pytest

CONTROL_DIR = ROOT / "scripts/icarus"
if str(CONTROL_DIR) not in sys.path:
    sys.path.insert(0, str(CONTROL_DIR))
from main import IcarusControl


@pytest.mark.parametrize("app", ["mem0", "openkb"])
def test_service_dev_install_is_local_and_docker_free(tmp_path, app, monkeypatch):
    (tmp_path / ".example.env").write_text("ICARUS_DATA_DIR=\n", encoding="utf-8")
    control = IcarusControl(tmp_path, cwd=tmp_path, environ={})
    commands = []
    monkeypatch.setattr(control, "_run_checked", lambda cmd: commands.append(list(cmd)))
    def no_docker():
        raise AssertionError("dev-only install must not require Docker")
    monkeypatch.setattr(control, "_check_docker", no_docker)
    assert control.run(["install", app, "--dev"]) == 0
    assert commands == [
        ["bash", str(tmp_path / f"apps/{app}/scripts/install.sh"), "--dev"],
        [str(tmp_path / "scripts/install-commands.sh")],
    ]
    assert not (tmp_path / ".venv").exists()
```

增加：已有 `.env` 内容原样保留；私有安装失败立即中断、不继续安装命令链接；普通服务安装命令仍为 compose build；全仓 dev 安装包含新环境且每个仅一次。

Run: `apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q`。预期现有 `--dev does not apply` 导致功能测试红。

- [ ] **Step 2：最小修改 `install()`。**

```python
# project is None 分支：现有服务 build 后增加，保留原有安装次序。
if dev:
    for service in COMPOSE_PROJECTS:
        self._run_checked([
            "bash", str(self.repo_root / f"apps/{service}/scripts/install.sh"), "--dev"
        ])

# 单个 compose service 分支：替换当前 --dev 拒绝逻辑。
if dev:
    self._run_checked([
        "bash", str(self.repo_root / f"apps/{project}/scripts/install.sh"), "--dev"
    ])
else:
    self._check_docker()
    self._install_service(project)
```

不改变 `KNOWN_PROJECTS`、启停顺序、Compose health、Secret 验证、Agent in-Gateway 模型；不把 mem0/openkb 纳入 `scripts/install.sh` 的普通 Python App 循环，防止重复安装。

- [ ] **Step 3：加 Makefile 目标和根测试聚合。**

```makefile
test-mem0:
	bash "$(REPO_ROOT)/apps/mem0/scripts/test.sh"

test-openkb:
	bash "$(REPO_ROOT)/apps/openkb/scripts/test.sh"

test-mem0-full:
	bash "$(REPO_ROOT)/apps/mem0/scripts/test.sh" --full

test-openkb-full:
	bash "$(REPO_ROOT)/apps/openkb/scripts/test.sh" --full
```

四目标加入 `.PHONY`；根 `scripts/test.sh` 在最终 diff check 前调用两默认子集，并用 Agent dev 解释器跑新增 pytest infra 测试。保留现有 root 控制面测试执行；不捕获非零后假称绿。

```bash
"$repo_root/apps/agent/.venv/bin/python" -m pytest "$repo_root/scripts/tests/test_repository_infra.py" -q
bash "$repo_root/apps/mem0/scripts/test.sh"
bash "$repo_root/apps/openkb/scripts/test.sh"
```

- [ ] **Step 4：更新安装/依赖 spec。** 明确两个 dev `.venv` 与 Docker 运行镜像并存；根不新增聚合依赖；OpenKB 使用 uv.lock，Mem0 当前 pip 安装不宣称消费 poetry.lock；CI 标准 Python 3.12 不代表 SDK 兼容下限变更。

- [ ] **Step 5：验证。**

```bash
python3 scripts/tests/test_icarus_control.py
apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q
make -n test-mem0 test-openkb test-mem0-full test-openkb-full
make test-mem0
make test-openkb
python3 -m compileall -q scripts/icarus scripts/tests
git diff --check
```

记录命令序列变化，不启动任何服务。本任务实现与测试一起审查，文档单独提交仅在获授权后进行。

## Task 4：根 CI 接入与构建/锁验证

**Files**

- Create/Modify: `.github/workflows/apps.yml`、`.github/workflows/lint.yml`、`.github/workflows/images.yml`。
- PR6 合入后 Modify: `.github/workflows/backend-contracts.yml`；保留 `webui.yml` 的前端检查。
- Test: `scripts/tests/test_repository_infra.py`。

**Interfaces**

- Consumes: 应用 install/test 脚本、Makefile targets、app-owned Dockerfile/context 与 OpenKB uv.lock。
- Produces: 最小权限、无生产 Secret 的离线测试/构建 job；本地和 CI 使用同一默认子集。

- [ ] **Step 1：写 CI 结构测试。** 使用 Agent 测试环境已有 PyYAML；yaml.safe_load 对 `on` 的 YAML 1.1 行为用 `True` 兼容读取，不误判丢触发器。

```python
import yaml


def test_app_workflow_uses_local_entrypoints_and_root_triggers():
    doc = yaml.safe_load((ROOT / ".github/workflows/apps.yml").read_text())
    assert doc["permissions"] == {"contents": "read"}
    triggers = doc.get("on", doc.get(True))
    for event in ("push", "pull_request"):
        paths = triggers[event]["paths"]
        assert "Makefile" in paths
        assert "scripts/**" in paths
        assert "apps/mem0/**" in paths
        assert "apps/openkb/**" in paths
    job = doc["jobs"]["contracts"]
    matrix = job["strategy"]["matrix"]["app"]
    assert matrix == ["mem0", "openkb"]
    runs = "\n".join(step.get("run", "") for step in job["steps"])
    assert "make test-${{ matrix.app }}" in runs
    assert "install.sh --dev" in runs
    assert "continue-on-error" not in str(job)
```

增加 images matrix 的 Dockerfile/context 存在断言，以及所有新增 Actions 不使用 `pull_request_target` 和生产 Secret 的检查。

- [ ] **Step 2：建立 `apps.yml`。**

```yaml
name: Service application contracts
on:
  workflow_dispatch:
  push:
    paths: ['apps/mem0/**', 'apps/openkb/**', 'scripts/**', 'Makefile', '.github/workflows/apps.yml']
  pull_request:
    paths: ['apps/mem0/**', 'apps/openkb/**', 'scripts/**', 'Makefile', '.github/workflows/apps.yml']
permissions:
  contents: read
concurrency:
  group: service-contracts-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true
jobs:
  contracts:
    runs-on: ubuntu-latest
    timeout-minutes: 20
    strategy:
      fail-fast: false
      matrix:
        app: [mem0, openkb]
    env:
      MEM0_TELEMETRY: 'false'
      PYTHONUTF8: '1'
    steps:
      - uses: actions/checkout@v4
        with:
          persist-credentials: false
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - uses: astral-sh/setup-uv@v6
      - run: bash apps/${{ matrix.app }}/scripts/install.sh --dev
      - run: make test-${{ matrix.app }}
      - name: Verify OpenKB lock without rewriting it
        if: matrix.app == 'openkb'
        run: uv lock --check --project apps/openkb
```

Action 版本与实际合入 PR6 对齐；如改用 full SHA 固定，逐个核对存在的 release commit，不编造 SHA。全量 job 不伪装为必过 job；本批全量验证证据见应用计划。

- [ ] **Step 3：images job。** 基于 PR6 `images.yml` 的矩阵写当前存在的 `mem0`/`openkb` 两项；context 和 Dockerfile 不搬：

```yaml
matrix:
  include:
    - app: mem0
      context: apps/mem0
      dockerfile: apps/mem0/server/dev.Dockerfile
    - app: openkb
      context: apps/openkb
      dockerfile: apps/openkb/Dockerfile.icarus
```

使用原命令 `docker build --pull --no-cache -t icarus-${{ matrix.app }}:check -f ${{ matrix.dockerfile }} ${{ matrix.context }}`；只构建、不发布、不运行生产数据卷。PR6 已合入时保留第三项 WebUI，不用两项模板覆盖三项矩阵。触发路径包含 app 树、该 workflow、根文本/ignore 配置；保留 build 超时和 fail-fast false。

- [ ] **Step 4：lint.yml 的范围限定。**

用 Python 3.12/Go 的显式 setup 后 `go install github.com/rhysd/actionlint/cmd/actionlint@v1.7.7`，运行 `actionlint`；不添加自动修复。`compileall` 覆盖实际 Python 路径 `scripts`、`packages`、`apps/agent/src`、`apps/gateway/src`、`apps/tui/src`、`apps/mem0/mem0`、`apps/mem0/server`、`apps/openkb/openkb`、两服务 `scripts`。

Ruff 只用各 app 已有配置与 dev 依赖版本，不添加 root 统一配置。先对同一基线执行原 app lint：若已有失败，留下明确证据并仅给此变更的 Python 文件启用检查，不为过 CI 全仓修复旧 lint。

现有文档称 PR7 是 CRLF 仓库问题没有代码证据，不用它作变更理由；统一 LF 的理由是跨平台开发约定。

- [ ] **Step 5：衔接 PR6（独立、可等待的集成门槛）。**

PR6 未合入时不引用其不存在测试。合入后：

1. 保留 Agent/Gateway 双平台、WebUI 检查和镜像第三项。
2. 从 `backend-contracts.yml` 移除 Mem0/OpenKB 重复 job，统一由 `apps.yml` 调用默认子集。
3. Mem0 的 history 审计已经位于固定列表的 `tests/memory/test_main.py`、`tests/memory/test_storage.py` 内；OpenKB 固定列表补入 `tests/test_report_ops.py`、`tests/test_managed_kb_template.py`。新增路径只有在对应实现合入后才登记；登记后不存在须明确失败，不用 `if exists` 偷跳契约。
4. 保留 Mem0 hatch `icarus` env 的元数据；本地统一接口仍 `.venv`，不再另跑一套重复 hatch gate。
5. `apps/webui/test/integration` 仍按真实服务 opt-in，不强塞进离线 job。

- [ ] **Step 6：验证 workflow、触发器和退出码。**

```bash
apps/agent/.venv/bin/python -m pytest scripts/tests/test_repository_infra.py -q
actionlint
make test-mem0
make test-openkb
uv lock --check --project apps/openkb
git diff --check
```

检查 workflow 路径过滤不会漏 Makefile/scripts/pyproject/lock；job 上传日志必须 `if: always()`，但不能覆盖前面测试失败。本阶段不宣称目标仓库 Checks 已启用；实际 workflow 权限设置只能由仓库维护人确认。

## Task 5：声明收口、全仓验收与后续边界

**Files**

- Modify: `THIRD_PARTY_NOTICES.md`、根 `README.md`、设计 spec 的实施状态与计划索引。
- Record: 两 app 的本地验证输出和失败集合，摘要更新到各自计划验收记录。

**Interfaces**

- Consumes: 两份 app plan 的来源、LICENSE 校验、迁移后 skill 与应用测试证据。
- Produces: 可审查的第一批完成证据；不自动进入第二批。

- [ ] **Step 1：声明核对。** mem0 来源 SHA `c7ee362aff94a369af70f13f2b4f853f6793ff4c`，OpenKB 来源 SHA `ff54396e575ee6feb0113b631a34caa082b441cc`；分别登记代码子集和迁到根的 skill，链接保留的 LICENSE 与 MODIFICATIONS。根 LICENSE、历史来源和版权不覆盖为 Icarus 自己的版权。
- [ ] **Step 2：验证所有保留资源。** 比较清理前后 LICENSE/SDK JSON/deck SKILL.md/DB migration/UI/运维脚本内容哈希；审核变更名单没有业务核心或数据库数据被删。
- [ ] **Step 3：运行全仓检查，记录而不掩盖旧失败。**

```bash
make test-mem0
make test-openkb
make test-mem0-full
make test-openkb-full
make test-agent
make test-gateway
make test-tui
make test
python3 -m compileall -q scripts packages apps/agent/src apps/gateway/src apps/tui/src
bash -n apps/mem0/scripts/install.sh apps/mem0/scripts/test.sh apps/openkb/scripts/install.sh apps/openkb/scripts/test.sh
git diff --check
```

每条命令独立记录退出码；根 `make test` 的 fail-fast 不代表后面未跑的 app 成功。全量必须区分业务测试失败、collection 缺依赖、既有门禁失败；不能用 `|| true` 让门禁绿。

- [x] **Step 4：镜像与真实服务验收。** 本次已完成实际源码构建与隔离 offline 真实服务；具体镜像身份、日志和测试边界见 execution.md。

```bash
docker build -t icarus-mem0:slim-check -f apps/mem0/server/dev.Dockerfile apps/mem0
docker build -t icarus-openkb:slim-check -f apps/openkb/Dockerfile.icarus apps/openkb
```

容器 smoke 只使用隔离测试数据和不冲突端口。实际模型 smoke 先取得执行/费用授权，再按 app-owned adapter 验证；不直接 stop 用户当前服务、改 `.env`、升级生产数据库或清空数据。默认部署保持 Mem0 三服务、OpenKB 一服务，health/API 与第一批前一致。

- [x] **Step 5：验收文档与执行 handoff。** 更新了实际结果与裁决；第一批构建/工具/服务验证已完成，保留全量红基线，不执行 Git 提交或第二批。

## 第二批与提交边界

第二批只在 PR6 合入、WebUI 真实服务验收、旧 UI 独有能力及运维入口确认之后写具体 app plan；本计划不删除 dashboard/frontend/admin scripts，不提前改容器数量。

建议逻辑提交单元：根约定 → Mem0 skill/裁剪及测试 → OpenKB skill/裁剪及测试 → 根安装聚合及测试 → CI/声明收口。文档-only 可独立。本文不执行任何 git commit；获授权后每单元运行对应验收再提交，commit 消息按会话要求加 attribution。

## 自查与验收记录

- `SECURITY.md` 本批不新增：尚未确认 Icarus 实际安全受理渠道，不复制上游渠道或虚构邮箱；需要时由维护人确认后单独添加。
- 本文为计划，尚未执行应用安装、删除、模型 smoke 或 CI。
- 应用裁剪细节见两 app 的 `arch.md`/`plan.md`；仓库基建设计的文本/治理/SDD 对应 Task 1，开发接口对应 Task 2/3，CI/构建对应 Task 4，声明与验收对应 Task 5；第二批明确延期。
- 五项 Review Focus 均有对应测试/哈希/触发器或隔离验收，禁止以目录名称或单次 grep 代替依赖证明。
