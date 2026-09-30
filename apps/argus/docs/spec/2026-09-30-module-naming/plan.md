# Argus 模块命名统一实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans` 在当前会话逐项执行；若用户另选子代理执行，再使用 `superpowers:subagent-driven-development`。步骤使用复选框记录进度。当前仅编写计划，未授权本轮实施或提交。

**Goal:** 统一 Argus 的 Python 文件为 snake_case，保留 PascalCase 类名和“平台/软件名 + 职责”的语义，更新导入、测试与文档，不改变业务行为。

**Architecture:** 此次是机械命名整理，不调整层次或新增框架。`QQService`、`QQConnector`、`JevClient` 等类名、方法签名不变；模块路径改为 `qq_service`、`qq_connector`、`jev_client`。后续决策图开发沿用此规范，但不包含在本计划内。

**Tech Stack:** 现有 Python ≥3.11、pytest、应用私有 `.venv`；不新增依赖或格式化工具。

**Spec:** 同目录 [design.md](design.md) 记录已确认的命名规则。功能背景参见 `../2026-09-29-qq-observation-flow/arch.md`；后续功能见[决策图设计](../2026-09-30-judgement-graph/design.md)与[决策图实施计划](../2026-09-30-judgement-graph/plan.md)。本命名计划是图实施计划的前置，两者一并评审、分开实施和验证，不把图功能混入改名。

## Global Constraints

- Python 文件、包目录、函数和变量采用小写 snake_case；类采用 PascalCase；常量采用 UPPER_SNAKE_CASE。
- 平台/软件特定模块保留名称及职责，例如 `qq_connector.py` / `QQConnector`、`jev_client.py` / `JevClient`。
- 类名中的 QQ 缩写保持大写；`BasePolicy` 的未来模块为 `base_policy.py`，本轮不创建该基类。
- 目录 `connector/handler/service/strategy/dao/entity/clients/utils` 保留；不为单复数统一而移动目录，不强制一个类一个文件。
- 只处理 `apps/argus` 的模块、测试与相关文档引用。决策图、批次、模型判断行为、可靠性修复均不得混入。
- 保留未提交的 `apps/agent/settings.json`、`apps/argus/settings.json`、判断图设计稿修订和两个本地备份文件；不改 `.env`，不读取密钥。
- 类名、数据库表/字段、JSON subtype/version、配置键、策略版本、HTTP/RPC 接口保持不变；不做数据库迁移。
- 不启动或重启 Argus、SnowLuma、Gateway，不产生实际模型调用/QQ 消息。部署时由用户安排重启。
- 不创建分支、worktree，不 commit/amend/push，除非用户另外要求。重命名用 `mv`，不借 `git mv` 顺带暂存其他内容。
- 源码内部全部更新到新路径，不保留两套大小写模块或兼容转发文件；仓库外手写脚本如导入旧路径，需要调用方同步修改。

## Review Focus

1. **旧导入遗漏**：main、DAO 的相对导入、测试中的局部导入全部应解析到新路径（任务 1 的包导入检查与全套测试）。
2. **重命名后测试不再收集**：五个改名测试仍符合 `test_*.py`，收集节点数及功能测试对应不应减少（任务 1 的 collect-only 前后对照）。
3. **真实运行配置触发外部副作用**：测试不得因为本机 QQ enabled=true 而连接 Gateway/模型或读取真实数据库（任务 1 的临时配置测试启动器）。
4. **Windows/Linux 大小写差异**：确认旧文件真实移除，新文件名精确 snake_case，在当前 Linux 环境运行导入和功能测试（任务 1）。
5. **持久化兼容性被误改**：除导入路径和测试文件名外，生产函数、类定义、常量及映射应不变；现有 DAO round-trip/去重测试仍通过（任务 1）。文档中的类名不要误替换成模块名（任务 2）。

---

## 文件映射

以下路径均相对 `apps/argus/`。

| 原文件 | 新文件 | 类/公共符号保持 |
|---|---|---|
| `src/connector/QQConnector.py` | `src/connector/qq_connector.py` | `QQConnector` |
| `src/handler/QQHandler.py` | `src/handler/qq_handler.py` | `QQHandler` |
| `src/service/QQService.py` | `src/service/qq_service.py` | `QQService`、`name_mentioned` |
| `src/entity/QQObservation.py` | `src/entity/qq_observation.py` | `QQObservation` |
| `src/dao/QQObservationCodec.py` | `src/dao/qq_observation_codec.py` | `QQObservationCodec` |
| `src/clients/IcarusClient.py` | `src/clients/icarus_client.py` | `IcarusClient`、现有异常类 |
| `src/clients/JevClient.py` | `src/clients/jev_client.py` | `JevClient`、`Judgement` |
| `src/strategy/QQStrategy.py` | `src/strategy/qq_strategy.py` | `QQStrategy` |
| `test/connector/test_QQConnector.py` | `test/connector/test_qq_connector.py` | 所有测试函数 |
| `test/handler/test_QQHandler.py` | `test/handler/test_qq_handler.py` | 所有测试函数 |
| `test/entity/test_QQObservation.py` | `test/entity/test_qq_observation.py` | 所有测试函数 |
| `test/dao/test_QQObservationCodec.py` | `test/dao/test_qq_observation_codec.py` | 所有测试函数 |
| `test/clients/test_JevClient.py` | `test/clients/test_jev_client.py` | 所有测试函数 |

`test_dispatch.py`、`test_observation.py` 等现有功能测试文件可以保持不变；它们围绕行为分组，不强制机械合并成 `test_qq_service.py`。`test_gateway.py` 暂保持所在位置，不把测试重新归层混进这次模块改名。

### Task 1: 一次性更新模块、引用和测试

**Files:**
- Rename: 上表 8 个源码模块与 5 个测试文件。
- Modify: `src/main.py`、`src/dao/observation_store.py` 及 `src/`、`test/` 中实际引用旧模块路径的文件。
- Test: 现有完整 `apps/argus/test/`，尤其 handler、DAO、client、service 和 application 测试；不为类名变化添加无行为意义的测试。

**Interfaces:**
- Consumes: 现有构造参数、方法、响应与持久化契约。
- Produces: `from apps.argus.src.connector.qq_connector import QQConnector` 等新模块路径；`python -m apps.argus.src.main` 启动入口不变。

- [ ] **Step 1 — 基线和测试隔离。**查看 `git status --short`、新路径是否存在（存在则先检查，不能覆盖），检查 `test_main.py` 和配置测试是否会加载本机启用 QQ 的设置。将已有安全默认值注入本次 pytest 进程，不修改用户的 settings.json。以下启动器保存在本计划专属 gitignored scratch 目录 `run_tests.py`，用于基线、最终测试和 collect-only：

```python
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))

with TemporaryDirectory(prefix="argus-naming-tests-") as directory:
    data_dir = Path(directory)
    settings = data_dir / "settings.json"
    settings.write_text(json.dumps({
        "host": "127.0.0.1", "port": 8766, "log_level": "INFO",
        "queue_capacity": 1000, "worker_count": 1,
        "qq": {"enabled": False}
    }), encoding="utf-8")
    from apps.argus.src.config import config_loader
    # files('apps.argus') 在当前 loader 中仅用于定位 settings.json。
    with patch.dict(os.environ, {"ICARUS_DATA_DIR": str(data_dir)}), \
         patch.object(config_loader, "files", return_value=data_dir), \
         patch.object(config_loader, "load_icarus_environment"):
        result = pytest.main(["apps/argus/test", "-q", *sys.argv[1:]])
    raise SystemExit(result)
```

运行 `apps/argus/.venv/bin/python <scratch>/run_tests.py` 和同命令加 `--collect-only`，保存日志。预期测试通过并得到当前节点清单；若基线失败，记录具体失败，不在本次改名中修业务缺陷。测试隔离只作用于当前进程。

- [ ] **Step 2 — 验证新路径尚不可用。**在改名前运行下面的公开导入冒烟，预期 `ModuleNotFoundError` 指向尚不存在的 snake_case 模块（不是依赖缺失）：

```bash
apps/argus/.venv/bin/python - <<'PY'
from apps.argus.src.connector.qq_connector import QQConnector
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.service.qq_service import QQService
from apps.argus.src.entity.qq_observation import QQObservation
from apps.argus.src.dao.qq_observation_codec import QQObservationCodec
from apps.argus.src.clients.icarus_client import IcarusClient
from apps.argus.src.clients.jev_client import JevClient, Judgement
from apps.argus.src.strategy.qq_strategy import QQStrategy
from apps.argus.src.main import create_app
print("All renamed modules import successfully")
PY
```

- [ ] **Step 3 — 机械重命名及引用更新。**按映射表逐个使用 `mv`；所有相互依赖模块在同一任务内一起改名，不留下半套路径。编辑每一个导入的模块部分，不修改导入符号。例如：

```python
# 原来
from apps.argus.src.entity.QQObservation import QQObservation
from .QQObservationCodec import QQObservationCodec
# 改为
from apps.argus.src.entity.qq_observation import QQObservation
from .qq_observation_codec import QQObservationCodec
```

检查绝对、相对和函数内局部导入，以及以字符串保存的动态导入/patch 目标。不要把 `QQObservation(...)`、`QQStrategy.version`、JSON 类型标签 `qq_message` 或数据库列改名。全部替换限定到 Python 模块路径，不对类名做全局替换。

- [ ] **Step 4 — 验证新路径与测试行为。**重新运行 Step 2 导入冒烟，预期成功。运行隔离测试启动器 `--collect-only` 并与基线比较：用映射表归一化前后文件名，测试函数集合应相同。随后先跑对应最小测试（例如 `apps/argus/.venv/bin/python -m pytest apps/argus/test/handler/test_qq_handler.py apps/argus/test/dao -q`，这些测试使用临时 DB），再运行隔离启动器完整测试，预期行为与基线相同。

- [ ] **Step 5 — 导入残留与内容检查。**执行：

```bash
rg -n '(^|[.])(QQConnector|QQHandler|QQService|QQObservationCodec|QQObservation|QQStrategy|IcarusClient|JevClient)([.]| import)' \
  apps/argus/src apps/argus/test --glob '*.py'
python3 -m compileall -q apps/argus/src apps/argus/test
python3 scripts/tests/test_icarus_control.py
git diff --check
```

搜索命中需区分合法的类属性访问与非法旧模块路径；不能以搜索类名为零作为标准。检查 `git diff` 与新旧文件比较，生产代码差异仅限 import。配置文件工作区 diff 必须与基线一致，未启动任何服务。

### Task 2: 同步文档中的规范与路径

**Files:**
- Modify: `apps/argus/docs/spec/2026-09-29-qq-observation-flow/{arch,design,plan}.md` 的实际文件路径引用和命名约定。
- Modify if referenced: `apps/argus/README.md`。
- Modify: `apps/argus/docs/spec/2026-09-30-judgement-graph/design.md` 增加同一命名约定，不改变已讨论的图语义。

**Interfaces:**
- Consumes: Task 1 的新模块路径。
- Produces: 文档与源码一致的 snake_case 文件引用；类名保持 PascalCase。

- [ ] **Step 1 — 更新现状文档。**逐项将 `connector/QQConnector.py` 改为 `connector/qq_connector.py` 等。代码示例中的 import 同步修改，类名和实际功能描述保持原样。README 没有旧路径则不改，不为显得完整而新增段落。

- [ ] **Step 2 — 记录规则取代。**QQ 设计/计划中原“文件名与类名大小写一致”的规则已被用户本轮选择取代，改为：

```text
Python 模块采用 snake_case，类采用 PascalCase；平台/软件前缀及职责保留。
例如 connector/qq_connector.py 定义 QQConnector；clients/jev_client.py 定义 JevClient。
测试模块采用 test_ + 源模块名。
```

历史计划中真实存在过的已删除模块（例如旧 `service/gateway.py`）可保留为历史说明，不伪造它们现存；所有仍代表当前路径的引用改为 Task 1 映射。目录名和功能目录日期保持稳定。

- [ ] **Step 3 — 更新未来判断图命名。**在判断图设计添加同一规则：`strategy/base_policy.py` / `BasePolicy`、`strategy/qq/qq_intervention_policy.py` / `QQInterventionPolicy`、`decision_graph/graph.py` / `DecisionGraph`、`decision_graph/runner.py` / `GraphRunner` 为拟用命名；这些不是本次新建模块。保留该文件本轮之前的未提交语义修订；不在此修正批次/水位等其他设计问题。

- [ ] **Step 4 — 最终核查。**检查文档引用：

```bash
rg -n '(QQConnector|QQHandler|QQService|QQObservationCodec|QQObservation|QQStrategy|IcarusClient|JevClient)\.py' \
  apps/argus --glob '*.md' --glob '!**/.venv/**'
git diff --check
git status --short
```

本计划的原→新映射表允许包含旧名，其余命中需是明确的历史记录而非当前使用说明。任务 2 是纯文档改动，无需重跑模型/真实服务；交付时报告 Task 1 的测试和编译结果，不声称决策图已经实现。

## 完成判据与交付

- 8 个源码模块和 5 个测试文件均已按表重命名，没有大小写并存副本。
- 类名、公共方法、存储格式、配置键、API 与实际业务行为没有变更。
- 新 import 冒烟成功、测试收集集合不减少、最小测试与隔离后的 Argus 全套通过、控制命令测试与编译/diff 检查通过。
- 现状文档和后续设计使用同一规范；决策图仍只是设计，待另写功能实施计划。
- 用户的运行配置、进程、数据库和密钥未被改动；无提交、推送、服务重启。

**推荐执行方式：**原生逐项执行。改名和导入跨文件相互依赖，应在一个工作区内一次完成；不值得为每个文件派发独立实现代理。执行方式需在用户审阅本计划后确定。
