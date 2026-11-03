# 回应驱动 Channel：执行与验证记录

日期：2026-11-03。基线 `43312c487fd02307f5745fef456c586bd08bea94`。用户在取消额外业务state提案后明确授权“写完之后就可以开始开发”，最终线上消息由用户验收。

**当前状态：实现和本地回归已完成；独立审阅仅部分完成（推理流异常中断），两项已报告Important均已复现修复。改动未提交；用户另行授权后，本机验收服务已启动并通过健康/只读接线检查。** 不把本地假QQ发送或确定性模型当成真实线上验收，也不把不完整审阅称为全量通过。

## 范围与对应文件

### 不再做的state改动

五份设计/计划已同步取消Channel.state、CommitPayload.state、ChannelSnapshot.state及Argus列迁移脚本。继续用goal/progress/规则/Session历史交接，不新增Argus表或业务列。Agent的argus_session_links为实际Session关联表，不是业务状态字段，仍有具体调用者。

### 已实现主链

1. `apps/argus/src/argus_app/plugins/protocols/qq_protocol/plugin.py` / `application.py`：已鉴权心跳、notice、request无操作应答；message与message_sent保留conversation/self/peer/reply/@元信息，self私聊使用target_id，临时私聊不再误当群聊。原始body签名仍先验，未知/非法输入不放行。
2. `plugins/workflow_rules/discovery.py` / `plugin.py`：回应驱动criterion在规则层，历史最多8条按当前Observation截止；宿主覆盖伪造conversation，perception获得同一任务。创建Channel安装可处理后续相关消息的规则，首条只seed一次，active commit省略rule_source仍能继续回应。
3. `model_provider/semantic.py` / `discovery_fallback.py`：适配显式criterion/context，不再把闲聊硬编码成ignore；保留0.7原生Choice门槛及单次20秒perception，无工具且SDK重试0。review_required仅可用于defer。
4. `apps/agent/src/agent_app/application/controllers/session_api.py`：不再强行替换Argus会话工具集合；普通默认能力保持，显式工具限制也保持，不因来源扩权。
5. `apps/agent/settings.json`：声明Node stdio snowluma，通过环境插值引用JS路径/endpoint/token；旧硬编码凭据包装器未使用。本机忽略拓扑只新增公开JS路径，未更换密钥或修改SnowLuma。
6. `application/services/argus_sessions.py` / `repositories/session_entities.py` / `session_store.py`：原Agent SQLite中持久reserved/ready关联，一Channel多Session、同key重启稳定，旧Session唯一首次采用，删除后的键失效。Channel不依赖固定派生Session身份。
7. `argus_protocol.py` / Gateway `src/protocol/argus.py` / Argus Bridge：冻结session_key并显式选择/验证Session；旧无key在途轮明确待对账，不猜新关联。
8. 双侧commit/tools：next_session=new与现有progress/规则/确认原子提交，重复回执不生成多个key；旧任务回调按wake里的精确Session写journal。
9. Bridge恢复：可能已执行却没有确定commit时replay_safe=false，原轮保留execution_outcome_unknown；即使选择另一Session也不能绕过未定执行重跑。Argus没有新增QQ发送器。

## 本地验证

### RED→GREEN证据

- QQ协议：新增6项失败→11协议及4真实Host测试通过。
- Agent能力：2项强制剪工具失败→能力/Argus聚焦18通过。
- rule-owned任务：5项adapter失败、3项响应规则失败、可信context/转发及后续身份分别失败后修复→adapter46、workflow27及后续9通过。
- Session关联：3项缺selector、1项DTO缺字段失败后→选择/store15及实际Gateway key接线通过。
- next_session：冻结key缺失2项失败后→PG commit/session16通过。
- 精确callback身份：新增字段通过后仍观察旧identity调用失败，再修复→Argus工具/Session22通过。
- Bridge未知执行：missing replay_safe、跨Session可重放、round缺未知诊断分别失败后修复→恢复/bridge19通过。

日志在 ignored `.superpowers/sdd/plan-2026-11-03-response-oriented-channels/`。以上聚焦计数有重叠，不相加冒充独立覆盖量。

### 审阅修复后的最终完整套件

- Argus：**292 passed，57.58秒**。
- Gateway：**55 passed，18.83秒**；一个既有Starlette依赖弃用警告。
- Agent：**1326 passed，2 failed，66.54秒**。失败集合与开发前基线一致：
  - `test_session_runtime使用runtime_update并保留单session行为`
  - `test_session_runtime_e2e持久化完整agent_run历史`
  两者均在Session停止阶段报 `RuntimeError: drain:`，开发前整套和单文件已经复现。没有改实现/断言、增大超时或skip掩盖它们；不能宣称Agent整套全绿。
- `make typecheck-agent`：281源文件无错误。
- Argus compileall、`git diff --check`、本机拓扑validate通过。

### 实际本地进程/网络验证的含义

- Gateway新增e2e使用真实Agent、MCPv2 stdio子进程、工具schema与执行，发送工具仅向本地临时JSONL记录一条合成消息，**没有QQ账号或网络发送**。
- 模型HTTP fixture发起mcp_tool_search→mcp_tool_execute(snowluma/invoke_action)→argus_commit。三个独立场景通过：平台成功、HTTP/MCP传输成功但OneBot retcode=1401业务拒绝、平台成功后commit响应丢失并按原request_id查询回执。每个场景实际假send计数均1；业务拒绝提交no_action而非“已回复”，丢回执场景不会重新发MCP消息。工具原始envelope进入下一步模型视图，callback可恢复，重复wake不重发。测试是编排验证，不证明真实LLM一定正确处理每种返回。
- Argus真实PG/TCP e2e：首条创建→Agent active commit省略规则→第二条相关消息经保留规则通过，仍只有一个Channel和一个Session。
- 大任务换Session的实际持久选择与progress/key事务分别有真实SQLite/PG测试；不是生产上多Session并行工作流保证。

## 用户授权后的本机验收启动

用户确认“现在执行”后，检查到Gateway已加载含session_key的新契约，现有Argus Session没有active/queued任务，数据库无未结束round。Argus最初停止。

- 发现 `.env` 中SNOWLUMA_MCP_TOKEN与SnowLuma当前3010入站token不匹配，而QQ上报签名secret匹配。
- 经用户明确授权，仅把消费端MCP token对齐当前SnowLuma值，秘密文件权限仍0600；未更改SnowLuma API token或上报secret，未打印凭据。
- 停止/重新启动空闲Gateway加载新凭据；start argus检查到用户已启动的Argus实例running，未重复拉起或强制重启。
- 实际settings经Node stdio调用只读 `query_action/get_status`：OneBot status=ok、retcode=0、online=true；invoke_action工具可发现。QQ测试消息发送0条。
- Gateway /health=200 ready；Argus /health=200 ready、scan_error=null、rule_execution_available=true。
- 原body签名的合成heartbeat POST /protocols/qq返回200且ignored=true；不写聊天Observation、不唤醒模型。
- Gateway与Argus状态均running。历史ignored/exhausted不重放，用户使用新消息做最终聊天/业务验收。

尝试读取进程环境以对比token被权限拦截后未重试；改用服务RPC读取Session忙闲状态及应用配置/只读API验证，不绕过进程环境访问限制。

## 尚未执行与上线边界

开发验证阶段未做付费模型判断或真实消息发送；后续获用户单独授权的服务启动和只读OneBot检查已在上一节记录。仍未做主动真实QQ测试发送、最终线上消息验收、生产业务schema迁移或后续git commit/push。无新依赖安装。

MCP stdio握手和实际OneBot只读鉴权已通过，不再仅是静态工具列表验证；完整模型回复/发送/确认仍由用户用新消息验收。QQ上报签名secret与OneBot入站API token继续分开保存。旧ignored/exhausted消息不会自动重放。

self-message没有可信的发送回执关联账本，不能保证仅靠语义绝对抑制回声；默认保留既有reportSelfMessage配置，不自动开启。外部发送结果不明时保留未决，不能以新的Session或全轮重试弥补。

## 审阅

首次独立审阅被agent的worktree Git边界拦截，没有产出结论；主会话提供允许生成的静态review.diff后，请同一审阅者仅Read数据和普通源码，不重试被拒绝Git。随后推理API响应流解码错误中断，恢复得到部分审阅结论，**不是完整全功能审阅通过**。

已报告并复现修复的Important：

1. 宿主注入可信身份时覆盖了全部rule context，丢失自定义业务证据。增强same-task回归先失败于business_requirement丢失；修复为保留规则业务字段，仅覆盖宿主拥有的conversation/history。
2. JEV可能判规则提取文本，fallback却判原消息。修复第一项后同测试仍失败于text不等，随后冻结实际semantic text/criterion/context。另测semantic之后读取history不能改变已冻结任务，先RED再GREEN。相关workflow22项通过。

审阅还指出发送失败/回执丢失e2e缺口，已补上述三个真实stdio本地场景并通过；修复后整套结果如上。没有另起第二轮审阅、未声称平台语义质量或线上健康已被审阅证实。
