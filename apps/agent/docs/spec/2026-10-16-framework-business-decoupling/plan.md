# Agent 框架与业务解耦｜迁移计划

> 状态：**主体已实施并验证，未提交**。实际验收与偏差见[plan-validation.md](plan-validation.md)；TUI保留既有13项快照失败。
> 当前实现见 [arch.md](arch.md)，D1–D42目标见 [plan-target-architecture.md](plan-target-architecture.md)，
> 详细文件/接口/测试任务见 [plan-tasks.md](plan-tasks.md)。后续实施需先审阅整份任务计划。

## 1. 本轮确认与原草案修订

### 已确认

- 保留现有存储语义：Blackboard完整模型Message经插件状态快照/恢复；SessionStore持久化公共RuntimeUpdate历史。
  不把二者合成新权威源，不新增Message表，不从UI投影恢复模型。
- Hook采用改值方向：只改既有调用位置上的值，不改变调用次数、路由或控制流；记录与日志另行承担。
  实施时修订AGENTS对应红线，本轮不直接修改仓库规则。
- 用户后续已授权实施、安装开发工具与一次128-token受限真实模型smoke；仍不自动变更Git。

### 对原草案的纠正

| 原草案问题 | 修订 |
|---|---|
| Gateway共用Agent venv | 两app各有私有venv；两边及CI临时环境都安装本地Agent distribution |
| editable后立刻清所有PYTHONPATH | 只解除Agent新包自身的cwd依赖；Gateway/TUI及根packages既有bootstrap暂保留 |
| P1/P2表混入能力注册、tier、配置数组化、应用拆分 | P1/P2只做机械迁移；行为改造有独立任务与验收 |
| 整个LLMFactory/AgentFactory搬core | 实际厂商创建和ConfigModel留app；先注入再下沉通用AgentAssembly |
| 整个PersistenceRuntime搬core | 先切开身份/路径/资产设施与state/trace/log/脱敏后端，禁止框架回引插件 |
| core/capability包与capability.py同名 | Agent类放core.agents，RPC能力放core.capabilities |
| 新ConversationStore作为必须接口 | 保留现有存储语义后无真实框架调用者，本次不新增；只抽真正通用SQLite连接设施 |
| P3先按尚不存在的registry查能力 | 业务ID解耦与provider命名一起在P5a完成，TaskChannel先转基座设施 |
| 所有跨插件依赖机械事件化 | 运行期查询才走请求/响应；装配期Region由应用最终连线，基座设施直接注入 |
| 响应在普通handler入口拦截 | 响应在调用者普通inbox入队前完成配对，真实队列测试证明不自锁 |
| @handles集合必须等于全部consumed | 业务事件与能力请求各自严格校验；能力响应不进handler/订阅图 |
| 只锁17方法签名 | 锁19公开入口、构造、is_running属性、返回/异常身份、订阅、wire；分页新增兼容入口 |
| mypy空包通过即验收类型 | 随core/runtime迁入代码增量纳入，再纳入app；不得全局ignore内部类型错误 |

原 `arch.md` 是目标讨论，与仓库“架构文档描述现状”规则冲突；现将目标移至plan-target-architecture，
arch恢复为已核实的源码/测试现状说明。D编号保留，不将计划伪写成已实现。

## 2. 执行策略

1. **基线先行**：先记录当前测试失败身份和公开行为，不假设现有全绿。
2. **机械迁移连续完成**：安装/资源→框架路径→业务路径。每步维护canonical类型身份，中间态不长期停留。
3. **真实依赖先切开**：模型创建、会话资产、共享TaskChannel和默认业务配置完成注入后，才宣称框架独立。
4. **契约基础先于消费者**：manifest/ID/provider/tier→声明handler→真实总线请求→逐插件改造。
5. **应用拆分类似逻辑提取，不重写存储/锁语义**；Hook与记录器最后切换。
6. 每个逻辑切片写最小测试、RED/GREEN、应用回归；仅用户另行授权时才提交，不自动建分支/commit/push。

## 3. 阶段与任务

| 阶段 | 任务 | 交付 / 行为变化 |
|---|---|---|
| P0 基线与工程闭环 | T01–T02 | 对外/存储守卫；pyproject、双venv/CI安装、资源策略。无业务行为变化 |
| P1 通用框架迁移 | T03 | core/runtime canonical路径、测试镜像、应用显式选择插件包。无业务行为变化 |
| P2 业务迁移 | T04 | agent_app、11插件manifest类路径、外部/脚本/fixture/resource闭环。无业务行为变化 |
| P3 注入与设施归属 | T05–T08 | AgentAssembly/厂商factory拆开；角色数组化；身份路径资产下沉；SessionHost/共享channel |
| P5a 契约基础 | T09–T10 | manifest v2、稳定事件ID、能力目录、按契约依赖/tier/失败级联、内核业务ID清理 |
| P5b 插件改造 | T11–T14 | @handles/build_tools、单总线能力请求、Region最终装配、Skill异步查询 |
| P4 应用拆分 | T15–T17 | Repository/通用连接设施、SessionRegistry/提交/资源、更新/历史/回收/SessionApi |
| P6 Hook与记录 | T18–T22 | 隔离值链、Agent/LLM/Tool挂点、runtime记录机制、插件后端、链诊断 |
| P7 收口 | T23–T26 | 分页兼容入口、wire模型、扇出/mypy/全插件边界/跨应用回归、现状文档 |

保留P编号用于追踪原草案，不代表必须按数字升序。**P5a/P5b在依赖图中先于应用最终收口P4**：
应用要复用已稳定的Host与插件端口，而不是先拆一次再为能力改造重拆。
T15 Repository可在不触及同文件时与P5独立推进；具体依赖见plan-tasks §2。

## 4. 各阶段必须保持的语义

### P0–P2

- 普通import和manifest反射路径一起迁；不允许新旧路径加载两份Event/Message/异常类。
- 不改model_settings、能力ID、业务事件、Hook语义、state/DB格式。
- wheel显式带11 manifest与默认配置；editable能看到源码不是资源打包正确的证明。
- Agent包从非repo cwd可导入；不承诺Gateway/TUI整应用脱离仓库可分发。

### P3–P5

- state.store仍供Host同步恢复；task.control/input.submit仍供Host直连，不能发给兄弟插件。
- 唯一新增运行期RPC是Skill读取Blackboard历史conversation.read；不为已删除设施能力/死声明造假RPC。
- TaskChannelRegistry每session唯一；排队/准备/运行中的取消信号不丢。
- Region定义在restore前由应用注册，以保留session Region快照恢复；所有start/自检及失败级联完成后，应用清理失效owner、freeze最终集合，bus最后启动。
- 默认required_plugin_ids不缩减；tier不暗中改变当前默认失败策略。

### P4

- SessionApi保持原构造接缝与19公开入口，AgentRuntime只是同类兼容导出。
- shared load/unload task、mutation_lock、title_lock、幂等指纹/上限、持久化成功后发布、失败锁存不变。
- SQLite原子sequence/软删除与Blackboard安全历史、压缩、thinking隔离不变。

### P6–P7

- Hook参数改写在并行性与策略判断前；结果改写在最终预算/外置截断前。
- Hook不得更改工具集合/调用ID、路由/关联、任务控制字段；已yield流不可事后改写。
- 记录后端最后关闭，保留停机尾记录；记录不产生可被自己再次记录的业务事件。
- history分页先做legacy投影再切片，保留原二元组接口与四字段wire；慢消费者溢出仍断开。

## 5. 验收与交接

每任务按plan-tasks列出的命令完成最小验证；每阶段跑受影响app套件。
最终至少运行：

```bash
make test-agent
make test-gateway
make test-tui
apps/agent/.venv/bin/python -m mypy apps/agent/src/icarus apps/agent/src/agent_app
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test apps/gateway/src packages
git diff --check
```

另验：wheel安装/非repo cwd/双venv/CI环境、真实离线Gateway+SessionApi、TUI首帧边界；
WebUI使用现有应用测试入口做wire消费回归，不为本次加入Agent实现依赖。
真实模型/服务smoke需另获执行与费用授权；不读取真实用户数据作为fixture。

最终 `arch.md` 只在代码/测试落地后更新；未完成任务不勾选，不把“编写完计划”写成“重构完成”。
所有新增接口与默认值先通过本计划审阅，实施方式和Git集成由用户另行决定。
