# Argus 插件重构验收记录

日期：2026-11-02。分支：`feat/argus-plugin-refactor`。基线`feature@eaa3035`，包含本轮fetch后的`origin/feature@c019e9a`；未合并旧feat/argus，未commit/push/merge。本轮使用5个子代理，后续修正复用它们，未继续扩张代理数量。

## 1. 已完成实现

- 公开Icarus Host、manifest/contracts/EventBus/Capability复用，通用可信builtin分组发现，具体插件平级注册。
- main/application/config启动装配，仓库`icarus.toml`注册opt-in Argus，默认栈不自动启动。
- PostgreSQL严格两表observed_items/channels，消费水位、有限pending/重试、当前轮投递/确认均在Channel中，无关联/wake/规则历史表。
- QQ账号/飞书租户隔离、先入库再通知，Agent控制回流同样先存事件。
- 具体历史/机械/语义/记忆/关系/知识函数能力；语义与外挂真实配置入口、缺配置明确unavailable。
- 文件规则lint/compile/受限子进程执行、不可变hash路径、错时历史查询、陈旧判定重算。
- Bridge稳定wake/Session复用、Agent实际执行链、User Prompt规则编写指导、7工具通过现有lazy入口限定调用范围。
- 真实送达与处理确认分开；全冻结集合确认后事务更新，允许不更新；回执按task/binding幂等，finish撤销权限。
- 局部故障隔离、超大投递有界失败、暂停/完成遗留轮次对账等审查修复。

## 2. 最终门禁结果

以下为最终代码实际执行结果，不是历史分支记录或代理预期：

| 检查 | 命令 | 实际结果 |
|---|---|---|
| Argus完整套件，含真实PG和完整TCP E2E | `ARGUS_TEST_DATABASE_URL=<专用测试库> bash apps/argus/scripts/test.sh` | **84 passed，37.16秒** |
| Agent完整套件 | `make test-agent` | **1282 passed，61.80秒** |
| Gateway完整套件 | `make test-gateway` | **52 passed，12.67秒，1项原有弃用warning** |
| 仓库生命周期/基础设施 | `apps/agent/.venv/bin/python -m pytest scripts/tests -q` | **460 passed，38.39秒** |
| Agent/框架类型检查 | `make typecheck-agent` | **280个源文件，no issues** |
| 应用编译 | 各应用测试脚本compileall及Argus显式compileall | 退出码0 |
| 变更空白检查 | `git diff --check`，另扫描新增源码/文档 | 无诊断 |

最终各测试命令原退出码为0，没有用skip/xfail/continue-on-error掩盖已知失败。Argus Store测试使用本轮专建可销毁PG容器；应用级E2E进一步创建随机*_test数据库，结束后删除。未触碰共享生产/Mem0业务数据库，也未创建真实生产角色。

### 本轮遇到并如实记录的中间失败

- 分组插件发现初始2项失败；添加最小通用builtin递归后通过。
- 两表Store测试先发现来源提交顺序、历史修订边界、确认错误形状、删除订阅残留、旧修订重试等真实缺陷，均有red→green回归。
- 集成旧工作树产物时，发现原基线无lazy工具，不能整文件覆盖新feature；使用窄补丁保留新feature能力，补通用Session工具allowlist。
- 全Agent首次出现新测试文件包名冲突，补测试目录__init__；发现原硬编码插件数量15，新增Argus后更新为16并保持懒发现断言。
- 一次并行负载下原Skill `test_submit_returns_queued_then_background_job_succeeds_and_notifies` 报通知数0（1 failed/1281 passed）；未修改无关Skill代码/测试，单项复跑通过，随后完整Agent **1282 passed**。保留此中间时序性失败，不把首次运行写成全绿。
- root三项旧拓扑断言认为Argus根本不存在，按新增opt-in应用更新为“已注册但不在默认栈”，完整460通过。
- 类型检查4项新错误已修复，无新增ignore/suppression。
- 一个HTTP非法控制类型原抛异常，补422错误映射和聚焦回归，完整Argus84通过。

## 3. 模拟真实E2E具体证明了什么

测试：`apps/argus/test/argus_app/test_closed_loop.py::test_two_sources_real_tcp_agent_confirmation_and_rule_rewrite`。

使用真实Uvicorn TCP监听的Argus和Gateway、生产AgentRuntime/SessionRuntime/AgentPlugin、实际Icarus Host/总线/定向能力、真实PostgreSQL事务和独立Python规则worker。只有模型响应由本地确定性HTTP/SSE fixture生成；不调用付费模型、不向真实QQ/飞书发送消息。

断言覆盖：

1. QQ带有效签名事件先入库，消费cursor前进，无深度Agent调用。
2. 飞书官方message fixture后到，包含可信tenant身份；规则query读取先前QQ事实，调用一次语义能力后通过。
3. Bridge经真实HTTP确保Session并提交冻结当前轮；Agent通过lazy tool_search/tool_execute调用适配工具，无法通过此工具表获得bash/MCP。
4. 模型第一次工具调用期间直接HTTP尝试提前更新，服务端返回events_not_confirmed；适配插件本地也阻止提前更新。
5. Agent确认本轮事实，受信协议层记录控制事件、规则固定handler接受，之后check候选规则并发布。
6. Channel版本1→2、progress正确、rule_hash切新文件，当前轮和pending清空；模型完成文本实际生成。
7. 后续事件用新规则忽略，cursor已前进而模型调用量不增加，证明不是“只是投递成功”或“没运行所以没唤醒”。
8. DB实际表集合严格为observed_items/channels；能查到提前拒绝和成功发布的控制结果。

另外Gateway生产Runtime测试验证稳定wake重试、重启不重复模型任务、身份/指纹冲突、动态指导在User而不在System、完整视图才确认送达。

## 4. 简审与修复

独立审查先后确认并修复：

- 移除订阅后非冻结pending不退休导致永久容量占用。
- 旧修订defer/error重试与新修订一起投递。
- 合法大事件在worker IPC输入被截断；改明确2MiB边界和剔除内部runtime/detail。
- 单个满队列Channel异常中断全应用恢复。
- 聚合wake超过Gateway512KiB，冻结后无远端task永久重试；改完整items384KiB预算及单项明确耗尽错误。
- paused/completed仍有遗留轮次却不进入终态对账。

最终限定修复复审：三项最终阻塞均ADDRESSED，没有发现修复引入的新阻塞；在单机受信部署范围内spec和quality均可接受。审查没有替代测试，测试也不替代权限/部署边界。

## 5. 执行中作出的裁决

| 裁决 | 原因 | 如果不合适的代价 |
|---|---|---|
| 当前feature上全新应用，只git-show复用旧Argus底层，不合并旧业务 | 新独立分支、无迁移要求 | 额外提取旧实现的集成工作 |
| 恰好两表，pending/当前round有界JSON；满则背压而不推进cursor | 用户明确取消关联与wake表 | 更高流量时可能需要重新评估事务热点，但不能擅自扩表 |
| 先单机受限Python子进程，不做独立Docker launcher/恶意多租户安全承诺 | 最小实现和可执行环境 | 面向不可信租户须另做更强隔离部署 |
| 规则入口采用同步def decide(event,channel)+capability(name,args) | 最小IPC可控编排，避免伪装尚未实现的async SDK | 未来升级SDK需改规则文件；没有旧数据迁移负担 |
| Session默认复用，冻结轮次全部确认后一次最终更新，确认前允许check | 将用户明确闭环转为可验证默认 | 若要子批次多次更新/每轮新Session，需调整轮次契约 |
| 未知提交保身份；已知终态只重投未确认事件且最多3次 | 防止丢事件与无限重试 | 外部动作自身不幂等时仍不能承诺exactly-once |
| 首期只开放7个Argus工具，不暴露任意bash/MCP | 暂无已确认外部写工具授权范围 | 实际平台动作需按工具追加明确权限接入 |
| 不自动发布飞书新版本/不commit/push/不改共享生产容器 | 本轮开发授权不扩张为外部发布或生产修改 | 后续需明确授权再同步外部文档/部署 |

## 6. 尚未验证/未包含

- 未使用真实飞书/QQ账号，不证明实际订阅权限、网络可达或真实编辑/撤回覆盖；飞书加密请求体明确不支持。
- 未花费真实模型调用，不证明真实规则生成质量、误判召回或生产性能SLO；100事件/秒为原计划建议，不宣称已做该基准。
- 自动Channel发现、自动留存/规则GC、管理WebUI、多机调度、统一runtime≤1MiB限制未作为首期完成项。当前源码限额和边界以arch.md为准。
- 未运行未受影响的TUI/WebUI/Mem0/OpenKB完整服务测试；本轮完整门禁范围是Argus、Agent、Gateway、仓库基础设施。
- 未执行Agent发行wheel的完整隔离安装门禁；已运行installed editable包懒发现测试、类型检查和compile。

本记录不把上述未做事项写成已通过。代码在独立开发分支待用户评审，未提交推送。
