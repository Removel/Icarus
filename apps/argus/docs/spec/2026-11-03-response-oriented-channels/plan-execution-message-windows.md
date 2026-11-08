# 来源消息窗口与插件交付：执行记录

日期：2026-11-05。阶段基线 `ae9f09f`。按用户要求，先保存当前代码，再提交文档，再开发；本阶段实现尚未提交或部署，不自动重启运行服务，不重放真实QQ消息。

## 已保存的检查点

- `3061aee`：Agent语义输入、回复引导、接收恢复及对应测试。
- `f54a630`：Argus来源/恢复/命名检查点，明确WIP；检查点真实PG套件349通过、2项命名并发失败，未伪称验收通过。
- `ac0d855`：Gateway受保护契约透传和本地E2E。
- `ae9f09f`：三应用消息窗口/插件交付计划及前期文档。

均未推送。draft_*、凭据、生产数据库和本机拓扑不在提交中。

## 已写入工作区

### 来源消息窗口

生产配置默认安静5秒、最长等待30秒；私聊/@不绕过。Store以Source锁和已入库position/observed_at封存最多20条消息的稳定窗口，成员保存在Observation.detail.message_window。窗口不是Channel，保留消息顺序、发送者、时间及引用/thread；容量以外消息留下一窗口，历史终态不全库重放。quiet=0可显式选择旧即时兼容策略。

每条规则仍判断自己的归属，但每次语义上下文获得完整current_window，不把“解释句”降为可省略的附件。较早历史是独立context，不改变历史截止。

`decide_window`同一Channel事务应用完整窗口中尚未判断的成员，失败回滚全部。完成后通知一次。新Channel首条seed不允许立即投递半窗口：begin_round要求来源游标覆盖窗口末尾。round保存冻结工作ID及涉及窗口的完整正文ID，未单独通过的原话也随正文交给Agent。

### ArgusPlugin动态交付

新绑定view_version=3，Bridge保存原始请求、短触发prompt和完整observation_prompt；不再将正文整包作为直接user_request提交。ArgusPlugin消费真实UserInputEvent，以PromptContributionEvent贡献正文，Blackboard统一组合。受保护会话必须等argus贡献；缺失、失败、超时不允许空语境启动模型。

模型输入中的messages是完整当前窗口，work_refs仅表示确认边界；history单列。窗口正文不能同ID替换冻结工作正文，不能跨未授权来源、重复或超总预算。旧v1/v2绑定不重渲染，新增可选空字段不改变旧请求指纹。

投递确认从实际最终User消息中的完整观察贡献验证；Blackboard的JSON编码先解码核对，不能只因短trigger出现就标已送达。后续历史中的旧plugin_context不能遮住当前上下文。工具只能确认本轮items，不确认非工作正文/history。

### 保留与不包含

普通权限、回执对账、同步/异步、稳定别名和未知外发不重跑保留。不改ReActAgent、不新增父子Plugin、不使用Hook调度。

本阶段**未实现运行中领取新工作**：没有available/read/wait的新收件箱协议或notify端点，执行中新消息留下一窗口，不悄改当前冻结集合。也没有做真实模型质量验收；“一轮完整看见”不等于模型一定不猜错。

## RED→GREEN

- 检查点已有命名并发RED重跑，事件级in-flight门禁+finally释放后，相关命名/发现6项通过。
- 5项真实PG窗口测试先因缺接口失败：quiet延长、max30秒放行、容量余项、Source隔离/终态兼容、seed窗口门禁与CAS回滚，后通过。
- 规则逐片通知用例先在pending只有1项时失败，改整批事务+一次通知后通过。
- 插件缺动态handler/只看trigger/丢解释句的4项先RED后GREEN；追加同ID改正文、512KiB、非法来源/重叠历史等契约测试。
- 实际JSON plugin_context可见性及多轮历史envelope回归先失败再修正。
- 真实PG→HTTP→AgentRuntime/ArgusPlugin→本地stdio MCP→原子commit：6条当前原文完整进入首次模型输入，其中1条不需要单独回应仍在正文，5个work_refs，1次假send后提交。没有真实平台发送。
- Gateway补充完整窗口/历史的3种发送结果场景（成功/拒绝/commit回执丢失），都保持1次假send、1次业务commit。
- required贡献失败/超时两种真实Session测试证明provider调用数为0。

## 独立审阅与修复

一次只读新阶段审阅发现Important：50条unread扫描页与20条来源窗口取交集，可能产生半窗口pending；多来源交错时填满256容量，而begin_round又等待窗口完成，导致无法排空。

新增16来源×20消息轮流入库的真实PG用例，先失败于pending中出现半窗口。增加Store.window_unread，获取完整封存窗口中的eligible成员，考虑已经seed/冻结/重试的状态；不以扫描页交集切窗口。修后完整处理320条、无重复确认，相关窗口/闭环8项通过。不增大容量、不跳过断言掩盖问题。

## 实际验证

- `make test-agent`：**1371 passed，63.91秒**。
- `make test-gateway`：追加必需贡献失败/超时测试后完整复跑 **61 passed，34.32秒**，1个既有Starlette/anyio弃用警告。
- `bash apps/argus/scripts/test.sh`：**360 passed，138.57秒**，真实专用临时PostgreSQL；runner内compileall通过。
- `make typecheck-agent`：**286源文件无错误**。
- Agent/Gateway/Argus compileall、`git diff --check`与文档相对链接检查通过。

未调用真实付费模型、未发送QQ/飞书消息、未改生产数据、未停止/启动线上服务。代码改动尚未加载进已有进程，不能说用户当前QQ体验已验证修复。需要后续明确授权和空闲状态检查后再重载。

## 实施取舍

- 纯窗口选择helper放infrastructure旁，避免Store反向依赖业务插件。
- 直接构造WorkflowRules的旧调用者默认quiet0，生产factory/config默认5/30；显式兼容不是按平台强信号绕过。
- 确认范围与正文范围分开，但正文完整可见；不是按是否值得回复筛掉原话。
- 当前请求只授权保存已有检查点再开发；新阶段实现保留工作区供审阅，没有自动追加commit或push。
