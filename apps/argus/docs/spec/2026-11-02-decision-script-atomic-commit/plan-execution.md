# 决策脚本与业务提交实施记录

2026-11-02，分支`feat/argus-plugin-refactor`。实现从`c373c07`之后开始；本轮未提交、未推送，未部署。旧开发快照a97d226/11bc050/c373c07保持不变且无联合作者。新设计、代码、测试和本文仍在工作区。

## 已实现与证据

| 范围 | 实现 | 测试证据 |
|---|---|---|
| 原子业务提交 | 单次agent.commit_requested；完整results、状态/订阅/规则引用、version、轮次与回执共同事务；移除旧Store多步控制，协议拒绝旧kind | 新提交测试先5失败后通过；Store全部58通过，覆盖并发、关闭binding回执、DB回滚、无变更不增版本、失败保pending |
| 文件级规则/纯静态检查 | main＋helper/安全计算库/try；顶层静态声明策略、大小和危险能力检查；check不执行/不写候选 | 新静态26失败→26通过；完整SDK43通过 |
| OS隔离 | installed unshare命名空间＋空只读chroot＋clearenv/FD清理/rlimit/no_new_privs/libseccomp；不可用拒绝，无宿主fallback | 新隔离4失败→4通过；真实阻止宿主文件、socket、fork、写文件；stdout伪造不进入IPC；probe available=true |
| 可选LLM风险审查 | 默认关闭；enabled时不确定/异常拒绝；源码hash/策略/目标绑定、预算和有界cache；在事务外 | 2失败→2通过；未做付费安全审查质量声明 |
| 精简Agent工具 | 五Argus工具＋授权memory_recall/read，模型只填业务字段；自动ref/ID/版本，固定持久callback/receipt | schema与工具新测试先失败后通过；实际Gateway/Agent工具与严格快照12通过 |
| 恢复 | GET提交状态无写操作；终态/取消后只查询既有意图，pending/not_found保留未知轮次 | 终态回执4失败→4通过；Agent插件14通过 |
| 工具发现解耦 | search只描述，execute按当前registry/schema/插件策略；保留Run tools快照 | 新测试2失败→通过；相关216通过；四种Agent模式与实际Session跨轮回归73通过 |
| 持续发现 | 来源订阅不阻断发现；可信候选Choice＋置信度；active topic去重、context版本复核、completed不自动重开 | typed发现2失败→通过；持续Store3失败→3通过；包含在Argus完整套件 |
| 付费测试入口 | 取消DeepSeek semantic替身，使用配置typed JEV；agent/场景/semantic/safety独立预算；自然任务输入；鉴权错误止损 | 预算2失败→2通过；CLI help/compile通过；真实JEV只试1次后AuthenticationError停止 |

真实PG/Argus/Gateway TCP、生产AgentRuntime、隔离规则的确定性闭环17通过，不是假协议握手。当前完整回归：

- Argus：156 passed，47.09秒，真实隔离PostgreSQL。
- Agent：1292 passed，60.36秒。
- Gateway：52 passed（本轮使用具FastAPI依赖的Argus环境）。
- 仓库scripts：460 passed，37.50秒。
- Agent typecheck：280源文件无错误；compileall及git diff --check通过。

独立审阅已完成，发现3个Important、无已验证Critical；三个问题均在一次修复轮中复现并修正，最终验证见末节。上表保留阶段证据，不以早期计数替代最终结果。

## 实施裁决与环境

1. 在用户当前功能分支原地实施，不创建新branch/worktree，不自动提交；保留两个draft实验目录。
2. 计划helper无法解析`Task R1：`，用独占gitignored ledger记录任务/接口和red→green证据，不为工具格式更改业务计划。
3. Linux docker不可用，但已安装Windows Docker引擎可用；为本次创建独占可销毁PG容器，不接生产数据库。
4. 下载的bwrap包执行被权限策略拒绝，未执行或安装；使用系统已安装unshare实现同等命名空间边界，不绕过下载代码限制。与计划的bwrap实现路线不同，已实际验证而不宣称bwrap已装。
5. 旧控制流程测试迁移至单次commit；未通过兼容适配层伪装旧协议可用。普通Python宽松语法只在OS隔离接线后启用。
6. 发现分类需要布尔之外结构，扩展现有semantic discovery模式而不新增通用workflow框架；不要求所有规则都调用semantic。

完整命令输出与进度暂存在gitignored `.superpowers/sdd/plan-2026-11-02-decision-script-atomic-commit/`。不是需要提交的业务数据或平台附件。

## 未验证/边界

- JEV预检命令：`apps/argus/.venv/bin/python -m apps.argus.test.manual_jev_semantic --authorize-real-model --max-requests 1`。真实使用ARGUS_JEV_API_KEY、SDK默认路由，首条AuthenticationError。没有切换其他key/伪造成功；完整自然场景付费闭环被此前置阻塞，没有继续追加请求。
- 真实QQ/飞书账号、外部数据、预约/发消息、生产权限、吞吐和多副本没有验证。
- 静态策略和OS限制不是任意Python恶意程序安全性的数学证明，也不是多租户认证；不启用任意第三方依赖/反射/写能力。
- 数据库commit不能回滚外部动作；后台会话仍未开放任意写工具。
- 结果未知的已持久意图可能保持unknown等待对账，不能为了自动恢复率盲重放或标成功。

## 独立审阅与最终验证

独立审阅发现并修复：

1. 标准库公开属性链可访问真实模块/builtins/worker emitter。静态绕过2条先失败；新增明确计算型导出表、module facade、锁定import入口，禁止任意模块对象透传。真实隔离的别名路径回归和正常from-import均通过。一次暂时恢复旧导入的运行时mutation检查被工具权限拦截，未绕过，立即恢复安全实现；审阅者此前已独立复现实际泄漏。
2. GET对账或持久outbox等待期间取消后仍POST。两条取消时序先失败，真正POST前重新检查取消；修复后均无写请求。
3. Workflow宽泛异常捕获把瞬时DB错误固化为拒绝。真实PG在_save后注入异常先失败；仅确定性校验错误保存拒绝，事务/文件基础设施错误保留pending，解除故障后同一控制请求committed。

上述聚焦组合56通过；未再次派审阅代理。修复后最终门禁：

- Argus **163 passed，49.85秒**。
- Agent **1294 passed，62.43秒**。
- Gateway **52 passed，15.39秒**；曾因status新增commit_state的旧断言失败1项，已对齐明确响应契约并整套重跑通过。
- scripts **460 passed，37.50秒**。
- typecheck **280源文件无错误**；compileall/diff检查通过；真实隔离probe available=true。
- `make check-package-agent`通过，wheel安装与16个插件manifest发现成功。

无延后Minor发现。代码与新文档未提交、未推送；保留独占ledger因为尚无Git提交承载证据。JEV鉴权/真实付费场景仍为上节所述外部阻塞，不宣布全量线上验收成功。

## 后续真实JEV验收（2026-11-02）

本节更新此前外部阻塞结论：用户明确8009是JEV API地址，服务恢复连通后，直连`http://100.70.8.11:8009`成功。两轮同一24条语义矩阵均24/24；完整业务首场景在发现阶段连续3次score=0.4639而defer，最终exhausted，未建Channel、深度Agent调用0次，其余5场景未执行。不是预算耗尽或网络问题。总成功连通后JEV调用51次，不含更早失败尝试；未取得token/账单。

详见[真实业务验收报告](plan-real-model-validation.md)。API可用不等业务闭环通过；先前鉴权失败保留为历史，不能继续称当前API不可用。测试专用PG容器已清理，报告后未追加付费重试或静默修复。
