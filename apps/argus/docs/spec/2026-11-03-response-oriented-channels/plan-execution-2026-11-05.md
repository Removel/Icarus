# 语义输入、安全投递与 Channel 命名：本轮执行记录

日期：2026-11-05；代码基线 `e2ca65c`，工作分支 `feat/argus-plugin-refactor`。用户明确授权开发，并追加“判定新建之后、实际创建之前，用 LLM 总结 Channel 名称”。本记录不覆盖 [2026-11-03 历史执行记录](execution.md)。

## 已写入工作区的实现

- Agent 新轮次不再拼接整份 GUIDANCE/内部 DTO。短稳定原则由 ArgusPlugin 静态贡献进系统提示词；当前语义消息、必要来源和目标直接进入用户上下文。
- 新增只读 `argus_guide`，主题 overview/rules/sources/commit，复用现有工具发现/执行。SDK 明确标为绑定快照，修改规则前可刷新；配置不冒充健康。
- 会话日志持久化 mN/sN 别名，旧绑定保留数字引用。新视图拒绝 raw UUID；提交/安全重试前核对完整别名日志，缺失/冲突失败关闭。历史不能被确认成本轮工作；累积来源别名不等于当前来源授权。
- 通用 `semantic_text` 可选输入贯通 SessionAPI/Host/UserInput/Event。Memory 查询/缓存/结果使用一致主题；None 保持旧 fingerprint，Persona 安全检查仍看完整输入。
- Agent 用类型化接收前错误与持久提交阶段区分 not_admitted/accepted/unknown。原 wake 可安全恢复；已入队或不明异常不重放，attempt 防迟到覆盖。
- Gateway 不再裁掉接收结果；Argus 选择 Session 后即保存，发送前持久化完整不可变 wake。丢响应先查原状态，重启/配置变化不能重建同 wake 导致指纹冲突。
- QQ/飞书保留来源事实、昵称/时间/引用和媒体元数据；区域与内容类型分开。QQ 的原始 CQ 字符串及空 message 的回退都先解析再过滤，不让签名媒体 URL混入正文。默认规则的纯媒体/空白配文不再被当成空消息。
- 新 Channel 名称通过可配置的工具无关 LLM 概括，默认 perception；严格1–60字符，10秒、256输出tokens、SDK重试0。Observation 持久一次性claim/结果，成功复用，超时/无效/缺配置回退首句，不改goal或路由。主新建路径在发现锁外命名，SQL事务不含模型调用。

## 测试与静态检查

### 已实际运行

- 原始修复完成并经审阅修正后，`make test-agent`：**1355 passed，95.26秒**。
- `make test-gateway`：**56 passed，31.84秒**，1个Starlette/anyio依赖弃用警告。
- `make test-tui`：**526 passed，212.25秒**；未修改TUI实现/测试/快照。
- Argus 无数据库的配置、model_provider、rule_sdk、协议、来源、桥接及部分workflow集：最终 **138 passed，6.44秒**（新增命名恢复锁回归后复跑）。
- 新命名模型/流程聚焦用例共11项，包含严格输出、工具拒绝、失败回退、结果复用、perception预算及恢复时不占发现锁；没有真实付费调用。
- `make typecheck-agent`：**286源文件无错误**。曾暴露7项新增注解/kwargs错误，已修正并复跑通过。
- Agent/Argus/Gateway compileall、`git diff --check` 和本特性目录内相对链接检查通过。
- 最后删除未使用旧GUIDANCE常量后，Argus工具+接收聚焦集41项通过；命名聚焦10项通过。

### 未通过环境门禁，不能称为通过

`bash apps/argus/scripts/test.sh` **退出1**。当前WSL没有可用Docker或本地PostgreSQL；Windows docker.exe存在但Docker引擎不可连接。未启动引擎、生产服务或改用生产库。

因此本轮新增 `set_round_session/submission/request`、命名claim/完成/过期的真实PostgreSQL事务及完整Argus TCP闭环仍待运行。已写真实PG测试并更新闭环fixture，但没有数据库运行证据；离线替身测试不替代此门禁。

## RED→GREEN与独立审阅

接收9项、语义视图/引用/指南6项、语义输入3项、来源4项和命名8项初始失败已观察，再最小实现转绿。附加raw CQ、空message回退、空白配文、旧来源别名、直接commit缺别名证据、恢复wake缺证据、丢响应后配置变化的测试均先失败再修正。

原修复做过一次独立整变更审阅，发现4项Important：旧来源别名重获授权、丢响应重建wake、直接操作绕过别名持久证据、空message回退漏CQ过滤。均以回归RED→GREEN修正。临时私聊group目标与旧飞书chat目标遗漏被升级为正确性问题，2项投影测试RED→GREEN修正；SDK快照新鲜度通过明确标识及刷新路径处理。

随后用户追加的Channel命名未包含在原审阅范围内，另做窄范围审阅。该审阅发现恢复completed fallback时仍持有全局发现锁等待命名；补充 `test_recovered_new_fallback_names_outside_discovery_lock` 先失败，再将恢复动作移到锁外，最终离线Argus138项通过。未发起重复复审；仍不能把只读代码审阅当作真实PG验证。

## 实例隔离与仍存在的限制

一个Icarus实例是独立对象，外部来源和平台账号不是对象身份。临时独立数据根证明本地任务/回执不串；生产Memory适配器契约测试证明配置不同agent_id时请求范围不同。**这不证明生产后端已隔离**：部署必须配置独立业务库/数据根与记忆身份范围；若复用同范围，不会自动获得隔离。本轮未改生产配置或新造实例协调系统。

未完成真实QQ/飞书发送、付费模型语义/标题质量验证、生产部署或服务重启。没有通用外发exactly-once保证，也没有可信回声账本。CQ纯文本发送转义和跨主机文件上传需平台后端配合，未在通用MCP客户端硬编码修复。



## 当前代码检查点与下一阶段

用户要求先commit当前代码再写窗口文档。已分逻辑保存：3061aee（Agent语义/接收/回复引导）、f54a630（Argus检查点）、ac0d855（Gateway契约与E2E），均未推送；草稿、凭据、本机配置不在提交中。

提交前重新运行：Agent1360通过、Gateway56通过（1个依赖弃用警告）、typecheck286源文件清洁。Docker可用后完整隔离PostgreSQL套件实际为349通过、2失败：`test_inflight_name_does_not_reclassify_same_observation`、`test_empty_source_discovers_and_delivers_first_event_without_manual_channel`。均指命名在锁外等待期间同观察可重复进入发现；Argus提交明确标wip，未伪称全绿。后续阶段先修这两项，不放宽断言。

连续讲话问题已只读取证：同一Source六条消息已在首轮投递前入库，却被逐条判定/投递；其中一条解释句已接受但未进入任何工作wake。此记录不复制私人原文。新阶段见[plan-message-windows.md](plan-message-windows.md)，将按窗口组织全部正文并接回ArgusPlugin动态交付；第二阶段运行中领取后续工作暂不实施。


## 后续：明确外部平台回复契约

用户发现重启后QQ无回复。只读执行记录证明两轮已收到消息并commit，却没有调用平台发送工具，回答只在内部assistant流中出现。用户随后批准精确引导方案。

聚焦修改Agent的Argus稳定提示词、overview/sources/commit指南及argus_commit描述，明确：以Icarus主体参与交流；账号是自己的平台身份；普通聊天免工具只指生成答案，外部投递仍要工具；先行动/检查业务结果，再提交；内部final不是平台发送；无需行动用no_action，纯内部工作不强制发送。

当前消息增加reply_target，明确来源引用、QQ私聊user_id/群聊group_id、飞书chat/thread和当前平台消息ID；不从群发送者猜私聊目标，不把被引用的旧消息当本次回应消息。目标不明时不猜测。仍使用现有发现和正常MCP/CLI，不新增平台代发器、发送权限或通用回执认证器。

5项新增契约/投影断言先RED后GREEN，Argus工具33项及Gateway平台/语义E2E5项通过。完整`make test-agent`1360通过（69.08秒），`make test-gateway`56通过（24.91秒，1个既有依赖弃用警告）；typecheck286源文件无错误，编译/diff通过。脚本模型根据实际reply_target选择目标并调用本地stdio MCP，但这不证明真实模型必定遵循引导。

本次未重启服务、未清理旧任务、未做真实平台外发或付费模型测试。已加载的Session不会因源码修改自动换用新静态提示词，需另获授权后重载；不宣称线上问题已验证消失。
