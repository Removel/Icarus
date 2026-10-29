# 当前 CI 检查与发布架构

本文描述已实现的配置，源码基线为 `885a4318cfaec7742da9ff0986ceb09f5b79f234` 加本次 CI 改造提交。配置已按授权推送到个人仓库 `xilele777/Icarus` 的 `feature`、`feat/webui`，进行真实 Actions 验证；尚未迁移 upstream 或启用分支保护。实施范围与后续任务见 [plan.md](plan.md)，验证证据见 [execution.md](execution.md)。

WebUI 四分片与耗时报告是后续变更，按用户授权提交并推送至个人仓库分支验证；其验证记录由 WebUI 自有的
[测试性能计划](../../../apps/webui/docs/spec/2026-10-29-test-performance/plan.md)维护，不将旧运行视为分片方案的远端验收。

## 调度与结果

[ci.yml](../../../.github/workflows/ci.yml) 对目标为 `feature`、`dev`、`main` 的 PR 和这些分支的 push 运行，也支持手动和 reusable 调用。入口无路径过滤；`changes` 获取完整历史，由 [changes.py](../../../scripts/ci/changes.py) 计算变更与预期任务。PR 使用 base/head 的 merge-base 分析影响，测试仍运行 checkout 的合并提交；push 使用 before/after。没有比较基线或 Git 无法比较时保守选择全部任务。

应用变更选择应用及其消费者；共享输入、新应用和未识别路径选择全部。只有明确的仓库文档路径可跳过应用任务，运行时 Markdown/JSON 不按扩展名豁免。Git 的 NUL 分隔记录保留删除和重命名前后路径。

`syntax` 总是运行。根控制层、后端、TUI、服务、WebUI 和镜像按输出调用现有 reusable workflow；镜像等待所选快速/应用检查成功后执行。`ci-required` 使用 `always()` 汇总全部依赖，由 [check_results.py](../../../scripts/ci/check_results.py) 对照预期任务验证。预期任务缺失、跳过、取消、失败，或检测失败均不能通过；只有明确不适用的任务可以跳过。

`ci.yml` 是普通 push/PR 的唯一自动入口。应用、语法与镜像 workflow 仅保留 `workflow_call` 和 `workflow_dispatch`，由统一入口复用或按需手动运行，不再因同一事件重复启动独立 workflow。首次真实 push 全量和纯文档验证成功后，按用户要求直接移除了旧自动触发器；分支保护的必需检查配置仍是独立的仓库设置。

## 验证执行单元

- [lint.yml](../../../.github/workflows/lint.yml) 运行 actionlint 与 [checks.py](../../../scripts/ci/checks.py)。后者逐个检查 Git 跟踪的 Shell 脚本、编译所有维护的 Python 源码目录，并对事件对应的 Git 差异做空白检查。手动运行检查所选提交，根提交才与空树比较。Shell 输入按 Git 的 LF 约定规范化后检查，不重写工作区。
- [control.yml](../../../.github/workflows/control.yml) 使用 Agent 私有环境运行 `scripts/tests/`，不安装根环境。
- [backend-contracts.yml](../../../.github/workflows/backend-contracts.yml) 保留 Windows 的应用自有契约入口、Gateway 双平台套件；Linux 运行 Agent 完整套件、mypy 和 wheel 安装检查。wheel 校验不再继承 system-site-packages。
- [tui.yml](../../../.github/workflows/tui.yml) 安装 TUI 自有开发依赖并运行完整套件，包含当前布局和交互验收。最新 TUI 已移除旧 SVG 快照，本次未恢复它们。
- [apps.yml](../../../.github/workflows/apps.yml) 根据传入应用数组执行 Mem0/OpenKB 既有默认契约和 rela-mem 离线测试；rela-mem 另通过 [ci-integration.py](../../../apps/rela-mem/scripts/ci-integration.py) 创建临时 PostgreSQL/FalkorDB，运行全部测试后清理本次容器和匿名卷。
- [webui.yml](../../../.github/workflows/webui.yml) 固定 Playwright 1.58 支持的 Ubuntu 24.04，默认测试在四个独立 runner 分片；第一组先执行 lint/format/typecheck/build 并复用该构建验证生产服务。各组独立上传报告，汇总门禁检查完整 collection 每项恰好执行一次及所有分片成功，耗时按 setup/call/teardown 展示。细节见 [WebUI 测试架构](../../../apps/webui/docs/spec/2026-10-29-test-performance/arch.md)。[应用测试入口](../../../apps/webui/scripts/test.py) 在临时端口以 strictPort 启动 dev server，移除继承的真实服务 opt-in，以不可用的 loopback 端点及测试凭据覆盖根 .env 的代理配置，返回原测试退出码并清理进程树。服务集成测试仍可显式替换为其自有临时上游。
- [extended.yml](../../../.github/workflows/extended.yml) 每周或手动在 `feature` 的精确 checkout 上运行 Mem0/OpenKB retained 全套，保留实际失败；只有该文件进入 GitHub 默认分支后定时触发才生效。

测试报告失败也上传。外部 Actions 固定完整 commit SHA，[Dependabot](../../../.github/dependabot.yml) 通过面向 `feature` 的更新 PR 维护。应用依赖的锁定现状未被本次改写，不能据此宣称全部构建可复现。

## 镜像与发布

[images.yml](../../../.github/workflows/images.yml) 以所选应用为矩阵构建并加载 amd64 镜像，不发布。通用 [smoke_image.py](../../../scripts/ci/smoke_image.py) 读取应用测试目录的 `image-smoke.json`，创建随机命名的 internal Docker network、临时依赖服务和应用容器，等待镜像声明的健康检查成功。没有宿主机服务端口、开发者配置或真实模型访问；清理仅操作本次创建的资源。

smoke 发现 Mem0 生产镜像缺少 psycopg 所需 `libpq`；[Dockerfile](../../../apps/mem0/server/Dockerfile) 已补 `libpq5`。测试夹具选择 OpenAI embedder 的初始化模式，端点为容器本地不可用地址，不下载 FastEmbed 模型或发送推理请求。

[publish-images.yml](../../../.github/workflows/publish-images.yml) 限定在 `Removel/Icarus` 发布。准备步骤验证 tag、checkout、触发 SHA 一致，正式版本属于 `main`，预发布属于 `main/dev/feature`。随后复用完整 CI，才允许构建各应用的 amd64/arm64 产物。

各架构构建一次，先加载并 smoke，再登录 `DOCKER` Environment 推送候选；记录应用、架构、commit 和 digest。最终 [release.py](../../../scripts/ci/release.py) 检查全部八条记录与既有版本后，按 digest 合成 manifest，不重建产物。正式版本才能更新 `latest`，低版本不能覆盖较新版本，相同版本不能替换为不同平台 digest。发布 workflow 跨 ref 串行且不中断运行中的发布。

旧 `latest` 若没有版本 annotation，正式晋级会明确失败，要求先核验并建立版本基线；预发布无需读取或修改 `latest`。手动 `dry_run` 默认开启，仅演练最终标签晋级，前序可信候选构建/推送仍会执行。晋级后重新读取 manifest，核对平台 digest，并按 registry 原始字节计算最终 manifest digest；中途失败仍保存已完成应用与失败状态。版本、commit、平台/manifest digest 和验证记录上传 90 天；长期发布归档、签名、SBOM 与扫描仍是后续工作。

## 信任与当前限制

普通 CI 维持 `contents: read` 和不持久化 checkout 凭据，不消费发布 Secret，不以高权限 PR 事件运行用户代码。仓库的生产拓扑没有被 CI 测试调度改写，应用继续拥有依赖和测试语义。

统一门禁已在个人 fork 的真实 push 全量运行通过，四个 amd64 镜像的构建/启动及 `ci-required` 均成功。upstream 的真实 PR 事件、ARM 验证、仓库 Ruleset/必需检查切换、DOCKER Environment 设置和真实发布尚未执行。具体本地失败与未验证范围必须以执行记录为准。
