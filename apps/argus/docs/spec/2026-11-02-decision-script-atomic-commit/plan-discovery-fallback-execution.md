# Discovery Fallback: implementation and validation record

日期：2026-11-02。状态：**独立审阅已完成，两项Important经RED→GREEN修复；最终离线Argus257、Agent1320、Gateway52通过。原始六条JEV发现6/6通过，独立LLM兜底后端1/1通过。** 本次未自然触发低置信度自动兜底，也未验证完整业务闭环，不代表已部署或生产启用。首轮权限失败保留为历史。

## Checkpoint 与修改范围

用户要求先提交已有代码，再开发；checkpoint 分为：
- `4d5db51`：工具发现与执行权限解耦。
- `346d6aa`：既有 Argus 原子决策提交、隔离执行及跨应用测试。
- `373b47e877324c6c5764a211ce2817bb519ac5c1`：既有设计、验证记录及本特性计划。

本次后续修改以 `373b47e` 为基线，仍未提交或推送。两个 `draft_*_folder/` 为会话外既有草稿，未加入 checkpoint，也不属于本实现。

## 已实现

1. JEV discovery 单次 Choice 请求，读取原生 `Choice.confidence`，保留0.7门槛；独立 Noul 已移除。普通 semantic match 保留0.5门槛。默认 OpenRouter 模型标识修正为 `~typesafe/jev-latest`。
2. 普通无工具 LLM 分类后端：复用 BaseLLM/Message/TextPart/DiscoveryDecision，只调用一次 ainvoke，校验 JSON、理由长度、候选身份、截断和意外工具结果。
3. 现有公共 factory/两个 SDK 适配器增加可选 max_retries；未配置时保留既有行为，兜底显式为0。同步和异步实际 HTTP 500 请求计数测试均证明无补发。
4. 现有 Observation JSON 保存认领 token、候选版本、25秒期限、完成结果及错误，不新增表。同修订最多一次兜底尝试；缓存可重放，未知结果不重发。修订或候选变化不得应用过时答案。
5. WorkflowRules 在规则执行/数据库事务/discovery锁之外等待20秒模型调用。只有宿主实际观察到 semantic 低置信度 defer 才触发；容量超限、普通规则defer和模型服务错误不触发。取消持久收尾后继续传播。
6. 显式开关默认关闭、复用既有命名模型配置，缺必要配置启动失败。App立即登记所拥有的资源，覆盖部分启动失败；外部注入对象不由App关闭。
7. discovery-only 验收入口与独立 fallback 预算：保留六条原始初始请求，另有候选续接、同来源新话题、引用/否定/闲聊/歧义样本。报告明确区分分类测试与持久业务闭环。

## 新鲜离线验证

- Argus：`bash apps/argus/scripts/test.sh`，**251 passed，56.12秒**。
- Agent：`make test-agent`，**1320 passed，64.62秒**。
- Gateway：`make test-gateway`，**52 passed，14.10秒**；一个既有依赖弃用警告。
- `make typecheck-agent`：280个源文件无错误。
- `compileall` 与 `git diff --check`：通过。
- 关键 RED→GREEN 日志和分任务完成记录位于 ignored `.superpowers/sdd/plan-discovery-fallback/`；不把 Python 方法调用次数冒充 SDK 实际请求次数。

checkpoint 前 Agent 首轮有一项 skill job 通知时序测试失败，未改实现，单测及完整1294项重跑均通过。本次1320项整套新鲜运行无失败。

## 本轮真实模型验收

入口：`manual_real_scenarios --mode discovery --discovery-initial-only --fallback-model perception`。
预算：JEV最多6次，fallback最多6次；普通命名模型 perception 对应既有 deepseek-v4-flash 配置。未修改持久配置或凭据。

- JEV base：`https://openrouter.ai/api`，model：`~typesafe/jev-latest`。
- 实际 JEV 尝试 **1次**，首条返回 `TypeSafePermissionDeniedError` 后立即停止。
- fallback 尝试 **0次**；因此不能声称真实 fallback 已验证或六个发现样本已通过。
- 未运行持久 workflow、完整 Agent 业务闭环或真实平台操作。
- 私有报告：`/tmp/icarus-argus-discovery-20261102-231841-27406/discovery-summary.json`。
- 不重试权限失败，不换 key/路由，也不沿用历史29/29结果冒充本次验收成功。

## 用户再次授权后的真实重试

JEV显式通过 `http://127.0.0.1:7890` 代理；一般模型 `api.deepseek.com` 按既有连接方式加入 NO_PROXY，仅本次进程生效，未修改持久配置。仍用相同六条初始文本、原生confidence和0.7门槛，没有人为降低阈值或伪造defer。

| 初始场景 | JEV原生confidence | 结果 |
|---|---:|---|
| 仓储接口评审 | 0.98 | new，符合预期 |
| 家庭体检预约 | 0.94 | new，符合预期 |
| 库存服务发布 | 0.98 | new，符合预期 |
| 洗衣机送货 | 0.98 | new，符合预期 |
| 供应商到账 | 0.98 | new，符合预期 |
| 空调维修 | 0.98 | new，符合预期 |

- 本次JEV尝试6次、正确6条；fallback自动调用0次，因为所有样本都走高置信度快路径。
- 另对第一条合成请求直接调用配置好的LLM兜底后端1次，返回 `new` 及合理理由，耗时2.104秒；接口为既有 perception 命名模型对应的 deepseek-v4-flash，无工具且SDK重试0。
- 此独立调用证明兜底模型后端可调用，不证明由真实JEV低置信度触发、持久认领/恢复或业务Channel创建已经在付费链路得到验证。这些编排与持久语义目前由确定性PG测试覆盖。
- 本次合计新增7次模型调用：JEV6、独立一般LLM1。此前首轮失败1次仍单列，不计入这次成功样本。
- JEV报告：`/tmp/icarus-argus-discovery-retry-20261102-233433-17604/discovery-summary.json`。
- 独立LLM报告：`/tmp/icarus-llm-fallback-direct-xv3mt3q1/result.json`。

## 审阅与剩余边界

独立只读审阅未发现Critical，发现两项Important，均已在一次修复阶段补测试并修复：

1. 已经开始的另一份primary分类可能覆盖completed但尚未应用的fallback。四条真实PG并发/持久测试先失败；Store现在禁止普通结果覆盖started/completed的fallback_pending，认领失败者重新读取并恢复胜出的缓存。4条RED→GREEN，相关持久/编排集34项通过。
2. 宿主按字符截断的中文/JSON转义候选可能超过4096字节，导致认领后在本地失败而没有实际LLM请求。两条中文与转义样本先失败；宿主现在先构造同一份UTF-8 JSON字节有界摘要给JEV与fallback，保留全部候选身份，不静默删除候选。2条RED→GREEN。

修复后完整套件：Argus **257 passed，57.90秒**；Agent **1320 passed，63.80秒**；Gateway **52 passed，13.65秒**（一个既有依赖弃用警告）；compile/diff检查通过。未新建提交、未推送。最初指定的高能力审阅通道503失败，没有产生结论；最终审阅由可用Sonnet通道完成，未进行第二轮审阅。无延后Minor。

审阅没有评价真实模型质量、全面凭据审计或一般多副本调度；前者由用户后续授权的分类实测单列，凭据文件访问被拒绝后只做无秘密文件读取的pattern扫描，多副本整体仍不作保证。

兜底保证应用级同消息修订最多一次尝试，不保证外部模型服务 exactly-once 或崩溃后必有答案。未知结果保守保留；历史 exhausted 消息不自动重置。多副本整体调度仍不是本轮交付承诺，尽管持久认领已用多 Store 并发覆盖。
