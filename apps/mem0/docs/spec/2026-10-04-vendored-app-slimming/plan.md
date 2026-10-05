# Mem0 外围裁剪与开发入口｜第一批实施计划

> **For agentic workers:** 使用 `superpowers:executing-plans` 或 `superpowers:subagent-driven-development` 逐任务执行。步骤使用 checkbox 跟踪；本文不授权执行删除、服务重启或 Git 提交。

> **执行记录：** [第一批结果与未验证项](execution.md)。下列步骤保留原计划；实际变更、裁决和非绿基线以执行记录为准。

**Goal:** 去除与 Icarus 无关的 Mem0 插件生态、演示与文档站，迁出开发 skill，同时保留完整 Python 核心、服务、运维、UI 和测试，并建立 app-owned 测试入口。

**Architecture:** `mem0/` 与 `server/` 保持现有 SDK/REST 边界，部署仍通过现有 Compose。开发环境独立放 `apps/mem0/.venv`；测试依赖的上游迁移脚本保全到测试夹具而非随外围删除；根控制面接入由根计划负责。

**Tech Stack:** Python 3.12（CI/dev）、本地 mem0ai、pip/Hatchling、pytest/pytest-mock/pytest-asyncio、Bash、FastAPI、Docker Compose。

**Spec:** [Mem0 裁剪设计](arch.md)；[跨应用约定](../../../../../spec/2026-10-04-vendored-app-slimming.md)；[仓库基建计划](../../../../../docs/spec/2026-10-04-repository-infra/plan.md)。

## 全局约束

- 来源冻结在 `mem0ai/mem0@c7ee362aff94a369af70f13f2b4f853f6793ff4c`，继续维护已存在的 Icarus 修改，不从发行版替换本地 SDK。
- 第一批不删 `server/dashboard/`、`server/scripts/`、SDK provider、`mem0/memory/oss_notices_config.json`、数据库迁移、服务路由或测试套件。
- 五个开发 skill 迁到根 `.agents/skills/`；`mem0-cli` 随 standalone CLI 删除；skill 的 LICENSE、references、client、辅助脚本整目录搬迁。
- 本批不把 deck/生产 skill 混入 `.agents`，不自动注册技能，不调用迁移 skill 或托管平台。
- Docker 运行依赖与开发依赖仍由 Mem0 自己声明；不得安装到根或 Agent `.venv`。
- 默认测试门槛为离线 SDK 契约；全量执行完整保留套件，记录缺依赖/既有失败但不吞退出码。
- 新增测试为 pytest 函数和 native assert；不为过测试修改不相关业务逻辑。
- 不生成分支、提交、推送，不删除用户 `.env`/数据库/缓存，执行前查看实际待删文件。

## Review Focus

1. **全量测试读取被删迁移脚本**：`tests/test_oss_to_platform_migrate.py:14` 的 SCRIPT 必须改到保全夹具，任务一验证原测试行为与文件哈希。
2. **迁移 skill 丢辅助文件或坏链接**：文档搜索脚本、所有 LICENSE、跨 skill 相对链接必须可用，任务二逐文件校验。
3. **打包漏资源**：wheel 必须包含 OSS notices JSON 与许可证；Docker 必須从本地 SDK 构建，任务三 wheel/image 验收。
4. **脚本位置/退出码错误**：从仓库外、有空格路径调用；pytest 非零后不继续 compile；任务四故障注入。
5. **可选 server/provider 环境误报全绿**：默认子集不以 importorskip 遮盖必要依赖；全量单独记录 collection 与 server auth fixture 问题，任务四与验收区分结果。

---

## 范围与事实

保留：`mem0/`、`server/`（除独立生产 Dockerfile、旧说明和过时入口）、`pyproject.toml`、`poetry.lock`、`LICENSE`、`MODIFICATIONS.md`、`tests/`、`scripts/icarus-compose.sh`、`scripts/icarus_compose.py`、Icarus `docs/spec/`。

待删目录：`.agents/`、`.claude-plugin/`、`.codex-plugin/`、`.cursor-plugin/`、`.kimi-plugin/`、`.github/`、`integrations/`、`examples/`、`cli/`、`mem0-ts/`。文档站按明确清单删，**不 `rm -rf docs`**。根 `skills/` 先迁五个完整目录，再删剩余 upstream 容器目录。

文件处理：删除 `marketplace.json`、上游治理文件、上游 Makefile/pre-commit 和文档站脚本；`scripts/oss-to-platform-migrate.sh` 不是零引用，迁夹具；`scripts/prepare_local_env.py` 是 Icarus 导入期辅助文件，删除理由是当前无调用者，不称为确认的 upstream 文件。

`server/scripts/{seed.sh,reset_admin_password.py,prune_request_logs.py}` 是初始化/密码恢复/日志清理入口，保留；`server/Makefile` 删除前把这些命令写入 Icarus server README。第一批服务数保持 3。

## Task 1：保留集合与迁移脚本夹具

**Files**

- Create: `apps/mem0/tests/test_icarus_layout.py`。
- Move: `apps/mem0/scripts/oss-to-platform-migrate.sh` → `apps/mem0/tests/fixtures/oss-to-platform-migrate.sh`。
- Modify: `apps/mem0/tests/test_oss_to_platform_migrate.py:14`（只改夹具路径）。

**Interfaces**

- Consumes: 现有迁移脚本与离线 MigrationHTTPServer 测试。
- Produces: 全量套件继续测试同一脚本；不再提供 app 顶层迁移 CLI。

- [ ] **Step 1：保存基线并写布局测试。**

```python
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
ROOT = APP.parents[1]


def test_icarus_runtime_and_test_resources_are_kept():
    for relative in (
        "LICENSE", "pyproject.toml", "poetry.lock",
        "mem0/memory/oss_notices_config.json",
        "server/main.py", "server/alembic.ini", "server/init-db.sh",
        "server/dev.Dockerfile", "server/requirements.txt",
        "server/dashboard/package.json", "server/scripts/seed.sh",
        "server/scripts/reset_admin_password.py",
        "server/scripts/prune_request_logs.py",
        "scripts/icarus-compose.sh", "scripts/icarus_compose.py",
        "tests/fixtures/oss-to-platform-migrate.sh",
    ):
        assert (APP / relative).is_file(), relative
```

先用已有 pytest 环境运行 `python -m pytest apps/mem0/tests/test_icarus_layout.py -q`。fixture 路径缺失应是红的原因；若 SDK pytest config 导致 collection 缺依赖，先按任务四准备 app 环境，不用改断言绕过。

- [ ] **Step 2：迁移夹具，最小修改 SCRIPT。**

```python
SCRIPT = Path(__file__).resolve().parent / "fixtures" / "oss-to-platform-migrate.sh"
```

复制/迁移前后 SHA256 相同，执行权限保留。不要替换成 mock、删测试或删除原脚本行为。脚本仍只在测试的临时 HTTP 服务/目录下执行，不指向真实 Platform API。

- [ ] **Step 3：运行定向回归。**

```bash
cd apps/mem0
.venv/bin/python -m pytest tests/test_icarus_layout.py tests/test_oss_to_platform_migrate.py -q
```

这组需要 Bash、python3、curl；没有工具要报缺失，不称 PASS。默认门槛可以只包含布局测试，迁移脚本原测试归完整套件；不得为移除功能把完整套件悄悄缩小。

- [ ] **Step 4：登记布局与夹具迁移。** 在 `MODIFICATIONS.md` 说明保留测试兼容性但不提供迁移 CLI；fixture 属于上游衍生内容，Apache/版权声明不变。

## Task 2：五个开发 skill 迁移与引用修复

**Files**

- Move: `apps/mem0/skills/{mem0,mem0-integrate,mem0-test-integration,mem0-vercel-ai-sdk,mem0-oss-to-platform}` → `.agents/skills/<same-name>`。
- Delete: `apps/mem0/skills/mem0-cli/`、上游 `skills/{README.md,AGENTS.md,CLAUDE.md}`（先读实际内容，未列出文件不得盲删）。
- Modify: 各迁移 skill 的 README/SKILL/reference 中旧本地路径。
- Test: `apps/mem0/tests/test_icarus_layout.py`。

**Interfaces**

- Consumes: 根 `.agents/README.md` 的开发用途/来源/不自动触发约定。
- Produces: 完整的五个 skill bundle，不自动调用、重写其业务流程或安装 Vercel/Platform 依赖。

- [ ] **Step 1：加迁移布局测试并记录所有源文件 SHA256。**

```python
from hashlib import sha256

SKILLS = (
    "mem0", "mem0-integrate", "mem0-test-integration",
    "mem0-vercel-ai-sdk", "mem0-oss-to-platform",
)
SKILL_LICENSE_SHA256 = "cc3850d680f691b44436f1b0f9f20db0df6f3a1395f4daebeeb4883f0ed4b888"


def test_development_skills_are_complete_at_repository_root():
    for name in SKILLS:
        target = ROOT / ".agents/skills" / name
        assert (target / "SKILL.md").is_file()
        assert sha256((target / "LICENSE").read_bytes()).hexdigest() == SKILL_LICENSE_SHA256
    assert (ROOT / ".agents/skills/mem0/scripts/mem0_doc_search.py").is_file()
    assert not (ROOT / ".agents/skills/mem0-cli").exists()
```

五份 skill LICENSE 的真实基线 SHA256 相同，但**不同于** app LICENSE（`0bbcbe931c353293a2fafce08326181dfeea0e568c566afd4ce8337a70f5e219`）。保留各自原件，不覆盖许可证来使测试通过。

- [ ] **Step 2：整目录移动，修复明确引用。**

需要检查的确切位置：

- `mem0/README.md`、`mem0/SKILL.md` 中 mem0-cli 链接；
- `mem0-integrate/README.md` 中两处 CLI 链接；
- `mem0-test-integration/README.md`、`mem0-oss-to-platform/README.md` 中 CLI 链接；
- `mem0-vercel-ai-sdk/SKILL.md` 的 CLI 链接；
- `mem0/references/integration-patterns.md` 的 sibling 路径改为 `../../mem0-vercel-ai-sdk/SKILL.md`；
- integrate 的 `SKILL.md` 与 `references/pipeline.md`，五份 README 的安装目录，按 `.agents/skills/` 新位置核对；
- Vercel references 的 `integrations/vercel-ai-sdk` 来源链接改为上游冻结 SHA 的 GitHub URL，而不是留指向已删本地目录的链接。

被删除的 mem0-cli 资料链接改为上游固定 commit 的参考或去掉本地入口，不把它作为仍在本仓库安装的产品。`mem0/scripts/mem0_doc_search.py` 随 bundle 保留；`CLAUDE_SKILL_DIR` 使用目录内部路径的逻辑不改变。

- [ ] **Step 3：验证 skill 完整性与链接。**

```bash
python3 -m compileall -q .agents/skills/mem0/scripts
```

对每个迁移文件核对哈希，仅允许明确列出的文档路径修改；相对 Markdown 文件链接必须存在，HTTP URL 与示意命令不作为本地链接。读取 SKILL.md frontmatter，确保 `name` 和目标目录相同；不用真实模型运行这些集成/迁移流程。

- [ ] **Step 4：更新来源声明。** `THIRD_PARTY_NOTICES.md` 的根 skill 条目最终由根计划收口；Mem0 MODIFICATIONS 记录路径和仅改定位文字。Skill 许可各自跟随；不加 marketplace manifest 自动发布这些 skill。

## Task 3：删除外围与修复维护文档/打包输入

**Files**

- Delete: 前述明确顶层外围目录；`marketplace.json`、`.pre-commit-config.yaml`、`Makefile`、`CONTRIBUTING.md`、`CODE_OF_CONDUCT.md`、`SECURITY.md`、`LLM.md`、根 `AGENTS.md`/`CLAUDE.md`、app `.gitignore`。
- Delete: `scripts/check-llms-txt-coverage.py`、`scripts/llms-txt-ignore.txt`、`scripts/prepare_local_env.py`；`server/Dockerfile`、`server/Makefile`、实际存在时的 `server/.env.example`。
- Modify: `README.md`、`server/README.md`、`MODIFICATIONS.md`、`mem0/AGENTS.md`、`tests/AGENTS.md`；删除失效的 server AGENTS/CLAUDE；保留并更新 SDK/tests 的局部约束。
- Inspect/必要修复: `server/dev.Dockerfile` 的本地 package license 输入。
- Test: `tests/test_icarus_layout.py`、现有 memory/server 测试。

**Interfaces**

- Consumes: 根 `.gitignore` 已吸收缓存/数据规则；任务一夹具/任务二 skill 已就位。
- Produces: 只裁外围的 Mem0 树；Docker context 仍 `apps/mem0`，compose 仍三服务。

- [ ] **Step 1：给删除集合加明确断言并运行 red。**

```python

def test_upstream_distribution_surfaces_are_removed():
    for relative in (
        ".agents", ".claude-plugin", ".codex-plugin", ".cursor-plugin",
        ".kimi-plugin", ".github", "marketplace.json", "integrations",
        "examples", "cli", "mem0-ts", "skills/mem0-cli",
    ):
        assert not (APP / relative).exists(), relative
```

保留集合测试必须同时存在，不用只有 absent 的测试证明删除正确。

- [ ] **Step 2：按跟踪清单裁文档站及外围。** 先列 `git ls-files apps/mem0/docs`，保留 `docs/spec/**` 和本计划目录；其余上游 mdx/images/navigation/llms 资料在记录 add.mdx 的 `preserve_input_language` 改动后删除。不对整个 docs 递归删除，不删除 Git 忽略的用户文件。检查 retained tests 与 setup 引用；有新增依赖则先保全夹具再继续。
- [ ] **Step 3：维护文档。**

README 写清：被冻结的 fork 子集、SDK/REST/API auth/数据库边界、统一 `icarus` 生命周期、私有 dev 环境、六类 skill 与根声明链接。server README 保留 seed/reset/prune 的真实命令与要求；不删鉴权、API-key 模块来配合 UI 裁撤。

`mem0/AGENTS.md` 和 `tests/AGENTS.md` 去掉已删 Makefile、pre-commit、CLI/integration/docs 站路径；保持 provider 模式、Pydantic、async/sync、测试结构等本地代码规则。让 CLAUDE symlink 继续指向同一更新后的 AGENTS，不写第二份。

- [ ] **Step 4：wheel/image 检查，不以静态引用推定成功。**

```bash
cd apps/mem0
.venv/bin/python -m pip wheel --no-deps --wheel-dir /tmp/icarus-mem0-wheel-check .
```

检查 wheel zip 中 `mem0/memory/oss_notices_config.json` 和 LICENSE 确实存在。`server/dev.Dockerfile` 当前复制 package 元数据却未显式复制 LICENSE；若构建确认需要，增加 `COPY LICENSE .` 到 `/app/packages` 的安装步骤，写明是打包输入修复而不是换依赖策略。移除独立 `server/Dockerfile` 前确定无 active Compose/CI 依赖。

`poetry.lock` 保留，但 pip 不消费它，不新增假的 lock-consistency gate、不为本批改写整个依赖系统。

- [ ] **Step 5：回归与内容哈希。** 裁剪前后 `mem0/**/*.py`、runtime JSON、alembic、server 路由、运维脚本、dashboard 均无内容变化（除获明确验证的打包输入修复）。执行 memory 子集、全量 baseline 对比、外部 Agent `test_service_config.py`。不要宣称全库缺依赖是既有业务失败或“全绿”。

## Task 4：私有开发环境、默认子集与全量入口

**Files**

- Create: `scripts/install.sh`、`scripts/test.sh`、`requirements-dev.txt`、`tests/test_dev_scripts.py`。
- 保留: `pyproject.toml` 的 test/dev extras、PR6 合入后的 hatch icarus env；`server/requirements.txt`；现有 SDK tests。

**Interfaces**

- `install.sh --dev` 只创建 app `.venv`、安装本地 `[test,dev]`，无需 Secret/Docker；其他参数退出 2。
- `test.sh` 默认固定测试列表；`test.sh --full` 跑整个 `tests/`；未知参数 2，缺环境 1，pytest 错误原样退出。

- [ ] **Step 1：先写假解释器的脚本契约测试。**

```python
import os
import shutil
import subprocess
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def test_runner_preserves_pytest_failure_and_does_not_compile(tmp_path):
    root = tmp_path / "repo with spaces"
    app = root / "apps/mem0"
    (app / "scripts").mkdir(parents=True)
    fake_bin = app / ".venv/bin"
    fake_bin.mkdir(parents=True)
    target = app / "scripts/test.sh"
    shutil.copy2(APP / "scripts/test.sh", target)
    log = tmp_path / "calls.txt"
    fake = fake_bin / "python"
    fake.write_text('#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "$CALL_LOG"\nexit 7\n')
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(target)], cwd=tmp_path,
        env={**os.environ, "CALL_LOG": str(log)}, capture_output=True, text=True,
    )
    assert result.returncode == 7
    assert "-m pytest" in log.read_text()
    assert "compileall" not in log.read_text()
```

同文件增加默认固定列表、`--full` 只有 `tests`、未知 flag=2、缺环境=1 的测试。install 测试用假 `PYTHON` 捕获 `-m venv` 和 pip 参数，断言 `requirements-dev.txt` 引用 app 绝对路径，root `.venv` 不出现，不读取 `.env`。

先从已有 pytest 环境运行，新脚本不存在导致 red；不先写实现再称 TDD。

- [ ] **Step 2：添加 dev requirements 与 installer。**

`requirements-dev.txt`：

```text
-e .[test,dev]
```

`install.sh`：

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
if [ ! -x "$app_dir/.venv/bin/python" ]; then
  "$python_bin" -m venv "$app_dir/.venv"
fi
cd "$app_dir"
"$app_dir/.venv/bin/python" -m pip install -r "$app_dir/requirements-dev.txt"
```

Python 3.12 是实际 CI 基线；不改 SDK `>=3.10,<4.0`，只遵循 Icarus 安装器最低 3.11。本方案 A 是维护 Icarus 私有 dev 环境，替换失效的上游 hatch-only 开发说明；PR6 hatch 元数据仍保留兼容，不重复建立第二个 gate。

- [ ] **Step 3：添加 runner，子集清单唯一写在应用脚本。**

```bash
#!/usr/bin/env bash
set -euo pipefail
app_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
python_bin="$app_dir/.venv/bin/python"
case "${1:-}" in
  "") tests=(tests/memory/test_main.py tests/memory/test_storage.py tests/test_icarus_layout.py tests/test_dev_scripts.py) ;;
  --full) tests=(tests) ;;
  *) echo "Usage: $0 [--full]" >&2; exit 2 ;;
esac
if [ "$#" -gt 1 ]; then
  echo "Usage: $0 [--full]" >&2; exit 2
fi
if [ ! -x "$python_bin" ]; then
  echo "Mem0 development environment is missing. Run: icarus install mem0 --dev" >&2; exit 1
fi
cd "$app_dir"
export MEM0_TELEMETRY=false
# Mirror the server's bare imports for server tests in the full suite.
export PYTHONPATH="$app_dir/server${PYTHONPATH:+:$PYTHONPATH}"
"$python_bin" -m pytest "${tests[@]}" -q
"$python_bin" -m compileall -q mem0 scripts tests
```

PR6 的新 history 审计测试在 `memory/test_main.py`/`test_storage.py` 内，合入后不丢；`server` 测试不是当前默认门槛，不能称 SDK 子集覆盖了完整鉴权路由。

- [ ] **Step 4：full 环境与非零结果诚实处理。**

SDK 可选供应商/graph/provider 和 server 测试会需要额外依赖。按保留 pyproject optional groups 与实际 import 安装到**同一 Mem0 dev venv**；server 的 FastAPI/认证/数据库依赖仍引用 `server/requirements.txt`，不复制一份 pin：

```bash
cd apps/mem0
.venv/bin/python -m pip install -e '.[test,dev,vector-stores,llms,extras,nlp]' -r server/requirements.txt
PYTHONPATH="$PWD/server${PYTHONPATH:+:$PYTHONPATH}" .venv/bin/python -m pytest tests -q
```

该命令是全量环境准备/基线验证，不保证每个 provider extras 都可在每个平台安装。缺其他可选 SDK 时列出 import 与对应模块，不为本批强加无关供应商到核心依赖。

`test_server_params.py` 的 fixture auth 环境、bare imports 必须与基线对比；若存在原有失败，不改服务鉴权来使旧测试通过。不全局 export `AUTH_DISABLED=true` 让 auth 测试失真。全量环境升级可能影响默认子集，升级后再跑默认 gate。

- [ ] **Step 5：验证脚本与 app。**

```bash
bash -n apps/mem0/scripts/install.sh apps/mem0/scripts/test.sh
bash apps/mem0/scripts/install.sh --dev
bash apps/mem0/scripts/test.sh
bash apps/mem0/scripts/test.sh --full
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/memory/test_service_config.py -q
git diff --check
```

每条记录真实 exit/collection/skips/failures；全量失败照常非零，不给 runner `|| true`。根 Makefile/控制面由根计划 Task 3 衔接，本任务不重复改一遍。

## 验收、后续与回滚

- 业务源码和资源哈希与第一批前一致；迁移脚本测试与 skill 链接继续有效；wheel 包含 JSON/LICENSE。
- 默认契约子集无新增失败；完整 retained suite 与同环境基线逐 nodeid 对比。若缺依赖/环境未完成，明确记录未验收，不能据此删测试。
- 镜像构建与 Compose 三服务保留；运维脚本入口文档已转移；根声明列完整来源和迁移目录。
- PR6 合入后保留 `docs/spec/2026-10-01-memory-history-attributes/`、history/db schema 补丁和对应测试，按根计划只收敛重复 CI job。
- UI 删除及生产数据迁移不在本计划；回滚只回滚对应源码变更，不能删或覆盖 `$ICARUS_DATA_DIR/services/mem0`。
- 提交拆分建议：夹具保全/技能迁移/外围裁剪/开发脚本及测试，均在获 Git 授权后进行；文档-only 尽量独立。

## 计划自查记录

本计划按读取的代码与测试补正了“上游脚本无引用”的旧判断；尚未执行删除、安装、wheel 或服务验收。当前应用存量指引、实际依赖、测试输入均优先于仅按文件夹名称作出的判断。
