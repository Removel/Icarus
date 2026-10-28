# Icarus 仓库基建设计

- 日期：2026-10-04，首次进入 Git 前核对日期。
- 状态：第一批已实施并补齐源码镜像、Actionlint、ShellCheck 和隔离服务验收；未提交，全量测试既有红基线继续如实记录。
- [跨应用约定](../../../spec/2026-10-04-vendored-app-slimming.md) · [第一批实施计划](plan.md)

## 背景与目标

裁剪 Mem0/OpenKB 不应仅删除上游 Makefile/CI/说明，还需要在 Icarus 层接住仍然必要的开发、测试和构建能力。本设计归仓库控制面，不归某个业务 App；不创建 `apps/infra` 或根聚合 Python 包。

确认范围：CI 补齐与测试接入、文本规范、治理文档、SDD 风格、app-owned Docker/依赖/脚本约定及校验。未选择统一全仓 lint/format，不把这一项加回实施范围；可复用应用已有配置进行有限检查，但不重排存量代码。

## 清理前基建与本批变化

下列前三项是实施前基线；本批已补服务 dev/test 入口、根约定与 CI，现有运行生命周期保持原样。

- `bin/icarus` → `scripts/icarus/main.py` 是安装/启停/状态控制面；Makefile 只转发。
- `scripts/install.sh` 目前安装 agent/gateway/tui 的私有环境；mem0/openkb 普通安装由控制面构建 Docker，`--dev` 当前拒绝。
- `scripts/test.sh` 跑控制面 unittest 和 agent/gateway/tui 套件，再 diff check；两个 vendored 服务没有根测试入口。
- 根仅少量 `.gitignore`、快照 whitespace `.gitattributes`、AGENTS、第三方声明，无根统一依赖或虚拟环境。
- PR6 尚 OPEN，head `87d12c19bb900e6ce7b21b411ffb5039ecb5018c` 带 backend-contracts/images/webui workflows、app-specific history/report tests 和文档；本批需能在合入前独立工作、合入后正确衔接。

文档路径规则来自根 AGENTS 与 `spec/2026-09-21-feature-document-layout.md`。当前应用详述见 [Mem0 设计](../../../apps/mem0/docs/spec/2026-10-04-vendored-app-slimming/arch.md) 和 [OpenKB 设计](../../../apps/openkb/docs/spec/2026-10-04-vendored-app-slimming/arch.md)，本设计不重复删除清单。

## 目标职责与目录

```text
.github/workflows/              # 应用契约、镜像、workflow/compile 校验；接 PR6
.editorconfig
.gitattributes
.gitignore
CONTRIBUTING.md
AGENTS.md                       # 简短约束与入口地图
THIRD_PARTY_NOTICES.md
.agents/
├── README.md                   # 开发技能来源/边界
└── skills/                     # 五个 Mem0 bundle + OpenKB 导航
skills/README.md                # 生产基础 skill 约定，本批无 loader
scripts/
├── icarus/                     # 唯一生命周期控制面
└── tests/                      # 控制面与仓库约定契约
Makefile                        # 一层转发，不复制业务
spec/<date>-<feature>.md        # 跨应用入口、公共契约、依赖、索引
docs/
├── templates/spec/{root,arch,plan}.md
└── spec/2026-10-04-repository-infra/{arch,plan}.md
```

仓库约定文件留其标准位置，不为了“infra 统一”全搬到一个 infra/ 目录。各 app 的 Dockerfile/Compose/lock/deps/scripts 仍归对应 app；根只决定入口与检查，不改变依赖方向。

## 安装、测试与 CI 接口

### 私有开发环境

两服务提供 `apps/<service>/scripts/install.sh --dev`，无需运行 Secret/Docker、不启动服务、不读写 `.env`，仅创建 app .venv。根 `icarus install <service> --dev` 转发此入口，普通服务 install 保持构建镜像语义。

全仓 dev install 保持现有 agent/gateway/tui 安装与两服务 build，并额外准备两服务 dev 环境。根没有 requirements/pyproject/.venv；开发安装失败原样中断，不继续宣称完成。

### 测试入口

- `make test-mem0`/`make test-openkb`：明确离线契约子集，本地与 CI 唯一入口。
- `make test-mem0-full`/`make test-openkb-full`：完整保留套件，非零真实返回。
- 默认 `make test` 聚合两服务子集及已有各 app suite；已知红基线不改成 skip/continue-on-error，按同环境 nodeid 对比。
- pytest/compile 由 app 解释器、app CWD 执行；避免 Mem0/OpenKB 同名 tests 或 package 串环境。服务 boot/data 仍 Docker。

### CI 所有权

基于现有 feature 先建立两服务 contracts/images；PR6 合入后保留 Agent/Gateway/WebUI 检查和第三镜像，删除重复两服务 job，默认子集只在 app scripts 定义。

workflow 要求：contents read、PR 无生产 Secret、checkout 不持久化凭据、超时、失败原样传递、日志 always 上传不掩盖失败。paths 包含对应 app、root scripts/Makefile/lock/build input；不得只按某些源码文件触发漏安装脚本。

完整检查/本地 baseline、Actionlint、compile、应用原 Ruff 的有限检查边界见计划。root 不建立统一 Ruff 规则或自动修复；既有 lint debt 不能在裁剪中无关重排。

## 构建与依赖归属

| app | 构建/依赖事实 | 本批处理 |
| --- | --- | --- |
| Mem0 | Docker context app、dev.Dockerfile、pip server deps + 本地 editable SDK；poetry.lock 被 COPY 但未消费 | 保留来源和输入，记录实际 pin 边界；必要 LICENSE 输入由 wheel/image 核验 |
| OpenKB | app context、Node Workbench build、pip .[web]；dev/CI uv sync --locked 消费 uv.lock | 保留 app lock/extras/Docker context，分别声明容器安装与 dev 锁策略 |
| Agent/Gateway/TUI | app requirements/private environment；Gateway 同进程依赖 Agent | 不在本批新增统一 pin 或安装到根 |
| WebUI（PR6） | app pnpm lock/Node static proxy/own Dockerfile | 合入后继承，不提前复制不存在应用 |

镜像 build 只证明当前源码构建可用，不能作为 poetry.lock/uv.lock 已在容器消费的证据。依赖审查不虚构“完全可复现”。不抽共享基础镜像阶段，不移动 lock 或业务 Compose 到根。

## 文本、治理与 SDD

EditorConfig 统一 UTF-8/LF/末尾换行和对应语言缩进；Makefile 用 tab，Markdown/快照保留有语义的尾空格。Git attributes 用单独文件 glob 定义二进制，不写 brace 扩展；保留快照 whitespace 例外，不做全仓 renormalize。

根 CONTRIBUTING 固化 feature 集成分支模型、按 app/逻辑提交、测试顺序、冻结上游/许可边界、开发与生产 skill 区别。根 AGENTS 保持规则地图，app AGENTS 保留适用的本地技术约束，丢掉上游 community CLA/vouch/发布流程。

SDD 分层：

1. 不可拆公共需求：根 `spec/YYYY-MM-DD-<feature>.md`，只放共同约束/索引。
2. 应用具体设计与计划：`apps/<app>/docs/spec/YYYY-MM-DD-<feature>/{arch,plan}.md`。
3. 仓库自有基建设计与计划：`docs/spec/YYYY-MM-DD-<feature>/{arch,plan}.md`。

日期为首次 Git 时间，不以当前工作日永久定名；没有自然 counterpart 不创建占位 arch。arch 区分当前事实与未实施目标，不成为禁止实现演进的文档。

SECURITY 上游渠道不能照搬；本批不虚构 Icarus 专用漏洞上报邮箱。如需要新增仓库安全受理文件，应先由维护人明确真实渠道，而不是声称项目未公开。

## 来源与数据保护

root THIRD_PARTY_NOTICES 保留原 full commit/URL/LICENSE，同时指迁根 skill 与 app MODIFICATIONS；skill 自带许可证不替换。Icarus 的 root LICENSE 不变，衍生源码保留作者归属。

裁剪既有用户数据、Secret/env、重启服务不属于基建；安装/测试/build 默认离线。真实模型或生产 smoke 必须单独授权并用隔离数据，禁止 stop 外部服务或删除 `$ICARUS_DATA_DIR/services`。

## 验收与阶段边界

第一批验收：控制面序列测试、外部 CWD/空格路径/错误码测试、SKILL/许可/Markdown 本地链接、CI ownership/触发路径、两 app default/full、Agent 服务配置、完整 Docker build、compile/diff。每项注明真实执行/缺依赖/未执行，不能用计划片段当证据。

第二批等 PR6 合入及 WebUI 真实验收、功能缺口确认后另写 app-owned plan，本基建第一批不改服务数量或删除 UI/运维入口。第一批已获得执行授权并留在隔离工作区；提交/推送仍需另外确认。实际结果和待验项目见 [执行记录](execution.md)。
