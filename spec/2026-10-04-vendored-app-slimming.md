# Vendored Apps 瘦身与仓库基建｜跨应用约定

- 日期：2026-10-04；尚未进入 Git，首次提交时核对日期，之后名称保持稳定。
- 状态：第一批已在隔离工作区实施并补齐源码镜像、Actionlint 与真实隔离服务验收；未提交。既有全量测试红基线仍按原样记录，第二批尚未执行。
- 范围：Mem0、OpenKB 与仓库控制面；本文件只记录共同约定、阶段依赖和文档索引。

## 背景与目标

Mem0 与 OpenKB 以普通源码导入 Icarus，包含上游插件分发、演示、文档站、独立工具和开发基建。目标是保留 Icarus 需要的服务核心与维护能力，去除无关外围，把可复用的开发基建归入仓库控制面，并把开发 skill 与生产 skill 分开。

本需求影响至少两个应用，以及共享安装、测试、构建和第三方声明契约，因此保留仓库级入口。具体文件清单、实现细节和测试步骤归各自功能文档，不在此重复。

## 文档索引与所有权

| 所有者 | 具体设计 | 实施计划 |
| --- | --- | --- |
| Mem0：SDK/服务保留、外围裁剪、开发 skill 迁移、私有测试环境 | [Mem0 设计](../apps/mem0/docs/spec/2026-10-04-vendored-app-slimming/arch.md) | [Mem0 第一批计划](../apps/mem0/docs/spec/2026-10-04-vendored-app-slimming/plan.md) |
| OpenKB：CLI/API/Workbench 保留、外围裁剪、导航 skill 迁移、锁定测试环境 | [OpenKB 设计](../apps/openkb/docs/spec/2026-10-04-vendored-app-slimming/arch.md) | [OpenKB 第一批计划](../apps/openkb/docs/spec/2026-10-04-vendored-app-slimming/plan.md) |
| 仓库基建：文本/SDD/治理、安装聚合、测试与 CI、共同声明及验收 | [仓库基建设计](../docs/spec/2026-10-04-repository-infra/arch.md) | [仓库基建第一批计划](../docs/spec/2026-10-04-repository-infra/plan.md) |

文档布局沿用根 `AGENTS.md`：应用在 `apps/<app>/docs/spec/<date>-<feature>/`；不归任何应用的仓库基建在 `docs/spec/<date>-<feature>/`。根 `spec/<date>-<feature>.md` 仍是跨应用单文件入口，不为整理另建 `apps/infra`。

## 已确认的共同决策

1. **冻结上游来源**：Mem0 来源 `c7ee362aff94a369af70f13f2b4f853f6793ff4c`，OpenKB 来源 `ff54396e575ee6feb0113b631a34caa082b441cc`。不自动同步整份上游，但继续维护 Icarus 补丁与安全修复。
2. **分两批**：第一批裁外围、迁开发 skill、建立基建；第二批旧 UI 收敛须先有统一 WebUI 的实际验收。
3. **Skill 分层**：开发期为根 `.agents/skills/`；生产基础 skill 源为根 `skills/`。第一批不新增生产技能加载/同步机制。OpenKB deck 三件套是单应用运行时资产，仍留在 OpenKB 包内。
4. **测试保留**：两应用的原测试套件保留，使用各自 dev `.venv`。默认根/CI gate 为明确的离线契约子集；完整套件另有 `*-full` 入口，真实返回失败，不藏基线。
5. **基建统一入口、不聚合环境**：CI、文本规范、治理和 SDD 归仓库；Dockerfile、Compose、依赖声明、lock、运行资源与开发脚本归应用。第一批不建立公共镜像层或全仓 lint/format 重排。
6. **CLI 不混淆**：根 `icarus` 为生命周期控制面；Mem0 standalone CLI 及对应 skill 裁撤；OpenKB cli.py 被服务与初始化调用，第一批保留，不拆后端。
7. **许可随源码与技能走**：保留两 app LICENSE、各 skill 原 LICENSE/版权，更新根 THIRD_PARTY_NOTICES 与 app MODIFICATIONS，记录完整来源及裁剪边界。

## 实施依赖与公共接口

第一批顺序：仓库文本/文档/ignore 约定 → 两应用各自保全与裁剪、dev/test 入口 → 根安装/测试聚合 → CI → 跨应用验收。两份 app plan 不重复改根控制面。

| 接口 | 语义 |
| --- | --- |
| `apps/<service>/scripts/install.sh --dev` | 只建应用私有测试环境，无需 Secret/Docker，不启动服务 |
| `icarus install <mem0|openkb> --dev` | 根控制面转发上述 dev 安装 |
| `icarus install <service>` | 保持 app-owned 镜像构建语义 |
| `make test-mem0` / `make test-openkb` | 应用离线契约子集；本地与 CI 同入口 |
| `make test-mem0-full` / `make test-openkb-full` | 全部保留测试，返回真实失败；缺依赖与业务失败分别记录 |
| `icarus start/stop/status` | 第一批不改服务数、数据路径、健康检查和生命周期行为 |

PR6 在本次设计时仍 OPEN，审查 head 为 `87d12c19bb900e6ce7b21b411ffb5039ecb5018c`。第一批可基于当前 feature 完成，不能引用尚未合入的 WebUI/报告测试；PR6 合入后保留其 Mem0 history、OpenKB source/report/template、app docs/spec、根 CI 改动，并按实际代码收敛重复 job。

## 第二批门槛

PR6 合入不等于旧 UI 已可删除。必须确认 WebUI 的真实服务路径可用，区分已替代能力与旧 UI 独有能力，取得接受功能缺口的确认，才按应用另写第二批 plan。

第二批涉及 Mem0 dashboard 与 OpenKB frontend 及各自构建/服务数量调整；后端 API、deck skill 和密码恢复/日志清理等运维脚本不因“删 UI”自动删除。不得在第一批提前修改这些部署契约。

## 共同验收与变更边界

- 验证依赖涵盖运行、打包、测试、运维四类，不能以一次外部 grep 零命中认定可删。
- 保留完整源码资源与许可证；默认子集无新增失败，全量按同环境失败 nodeid 比较；缺依赖不称为全绿。
- 两镜像仍从仓库源码构建，第一批保持 Mem0 三服务、OpenKB 一服务；构建成功不等于 pip 消费了 poetry.lock/uv.lock。
- `.env`、已有知识库/历史数据库、用户 ignored 文件不进入清理范围；真实服务/model smoke 用隔离数据并另获执行授权。
- 按应用和逻辑功能审查变更；未授权不提交、推送或合并。第一批在用户批准的隔离 worktree 实施，具体执行证据见各计划末尾；第二批 UI 收敛仍未执行。
