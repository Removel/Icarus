# 决策脚本与原子业务提交：当前实现

核验日期：2026-11-02。描述当前未提交工作区源码和确定性测试，不代表已部署或真实JEV验收通过。[设计](plan-design.md)是约束/默认选择，[计划](plan.md)记录实施范围；历史原型快照在c373c07。

## 1. 所有权和主链

协议插件持久`observed_items`→Event→workflow_rules→受控规则/只读Capability→Channel决策持久化→Bridge→Gateway薄鉴权/共享WakeRequest→正常AgentRuntime/SessionRuntime/AgentPlugin/ReActAgent。ReActAgent无Argus业务依赖；具体插件平级，助手对象不注册插件。

Argus仍只有observed_items/channels两表。控制Observation的detail保存绑定、原请求和固定回执；Channel runtime保存有界pending/当前round/发现元信息，不存无界执行历史。规则源在本Channel内容寻址不可变文件，数据库只保存路径与hash。共享PG使用独立Argus库与受限账号。

## 2. 规则检查与执行

- `rule_sdk/checks.py`：文件≤32768 UTF-8字节、AST≤4000节点，静态解析/compile，固定同步main(event,channel)入口、顶层声明策略、危险能力检查；返回code/line/column/stage诊断，不执行源码。
- `safe_modules.py`：明确计算型导出表；worker返回module facade而非真实模块对象，阻断标准库内部属性链取得builtins/sys/worker及伪造协议。
- `files.py`：检查后写临时文件、fsync、不可变link、目录fsync；读取复核大小/hash/策略。未引用文件不等于激活。
- `isolation.py`：使用已安装系统unshare启动user/mount/pid/net/ipc/uts命名空间；探测失败返回isolation_unavailable，无宿主执行fallback。不是下载bwrap后执行的路线。
- `worker.py`：可信启动预加载计算标准库；在空只读chroot内清env/关闭多余FD、设置CPU/内存/进程等rlimit、no_new_privs与seccomp，再执行源码。运行树不含宿主文件或凭据。stdout与可信JSON协议分离。
- `engine.py`：只读六能力名、最多3次/semantic1次、能力超时最长5秒、总墙钟；检查返回必须是dict，再用严格Decision验证。取消/异常均终止隔离进程组并wait。

允许普通helper/常量/try/isinstance及8个计算标准库根，禁止危险导入/动态exec/反射/私有属性等。安全策略是纵深防御，不是Python静态分析完备性证明；内核漏洞等风险不在“绝对安全”保证内。

## 3. 一次业务提交

Agent可见工具为channel_read/read/history/rule_check/commit；另经会话allowlist开放既有memory_recall/memory_read，不开放记忆写入、bash或任意MCP。工具技术ID、expected_version、hash/path由代码产生。

`rule.check_requested`纯静态返回，不试跑、不写文件、不调用审查模型。`agent.commit_requested`的固定handler先检查实际源码，可选typed安全审查，事务外准备文件；Store事务复核绑定/送达/全冻结results/版本，原子保存结果、状态/规则指针、订阅、version和pending/round收尾及committed回执。省略字段保持；未变更业务字段不递增version。旧confirm/update/finish入口已禁用，Store旧多步分支已移除。

callback在Agent现有journal中先写意图后HTTP；同内容同binding复用摘要ID和固定body。Argus查回执先于活跃binding检查，成功后round清空仍可恢复结果。只读GET commit状态不ingest/执行handler。终态任务对未回执提交只发GET，pending/not_found保持pending/unknown，不被当作成功或回滚；Argus Bridge不释放未知提交轮次。没有外部写工具，因此不以原子数据库事务宣称跨服务exactly-once。

## 4. 快照与取证

Agent-owned WakeRequest包含严格ChannelSnapshot和有界SDK metadata；id/version必须匹配，规则≤32KiB、SDK≤32KiB、消息总包络≤512KiB。Bridge冻结批次≤20项、items≤384KiB，保留完整消息。动态User Prompt包括可信指导、快照、能力配置状态和外来证据；不要求channel_read必须第一步。显式read才刷新本task版本，提交前不盲抓最新version。

工具发现与执行解耦见[Agent独立feature](../../../../agent/docs/spec/2026-11-02-tool-discovery-execution-decoupling/plan.md)。模型tools数组保持Run内冻结，search只提供说明，真实registry/allowlist/插件运行策略决定执行。

## 5. 持续发现

已有订阅规则处理与发现独立，不以来源owned跳过。发现使用独立DiscoveryDecision(ignore/existing/new/defer)；默认脚本经semantic discovery模式一次typed Choice＋Noul选择可信候选/new/ignore，置信度<0.7 defer；普通semantic match行为不变。

候选仅active/paused、同授权来源、最多20项，超限明确defer；context版本在短事务中复核。新事项topic_key为规范化文本hash，仅在活动事项中去重；completed不自动重开。旧事件修订终态不再发现，错误有有界退避，不变ignore。existing归属不绕过正常规则或唤醒paused。不同语义表述的去重仍需模型质量评估。

## 6. 验证边界

已有真实PG/TCP/正常Agent/隔离脚本的确定性闭环，以及静态不执行、OS文件/网络/环境/进程权限、原子回滚和回执恢复测试。付费脚本已改为真实配置typed JEV并独立预算；本轮单次预检仍AuthenticationError后停止，没有语义质量/自然场景付费闭环通过声明。最终套件计数和审阅结果另记[实施记录](plan-execution.md)，不把中间运行值写成最终结论。

未部署、未创建生产角色、未推送；不支持多副本调度、自动GC、任意平台写入或加密飞书体。
