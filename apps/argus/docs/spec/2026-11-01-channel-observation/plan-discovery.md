# 无人订阅来源的初始发现补充

2026-11-02。初始发现已补齐并完成确定性回归；用户随后授权少量付费模型验证，结果及停止的架构决策见[真实测试记录](plan-real-model-validation.md)。保持两表、无迁移、无真实平台内容。

## 决定与范围

- workflow_rules内部增加初始发现，不增加平台协议业务、不创建虚拟系统Channel、不新增插件框架。
- 受信初始规则文件走同一个受限Python引擎。默认机械识别明确请求，可通过ARGUS_DISCOVERY_RULE_PATH换成调用受控能力的脚本；只在无人active/paused订阅的来源执行。默认开启，可显式关闭。
- 脚本入口仍def decide(event,channel)，发现上下文mode=discovery、source_key、单来源subscriptions，无伪造持久Channel身份。初始历史查询仅授权当前来源。
- 初始Decision.passed后，服务端固定逻辑幂等创建Channel，以来源+消息身份确定UUID；目标/标题取首条请求，初始Channel规则默认不通过，首条触发事件直接写pending交Bridge，不等下一条。
- Agent首先处理/确认首条事件，再按原门禁发布后续规则。已有Channel不走发现，paused关注抑制重复发现；完成清订阅后新消息可发现新事。
- 发现消费结果/有限错误重试保在已有observed_items.detail.discovery；创建Channel+首次pending+消费结果同事务。配置订阅变化与发现创建用同一轻量事务协调锁，模型运行不持数据库锁。
- 验收增加：空来源忽略、首次请求创建、重放幂等、订阅竞争、paused抑制、同来源历史读取授权、失败有限重试、丢Bus重启扫描，以及从无Channel开始的真实PG/TCP/Agent闭环。

## 步骤

- [ ] 写Store与规则发现聚焦失败测试，确认缺少行为。
- [ ] 补两表原子创建/发现回执和后台恢复。
- [ ] 接同引擎、能力、配置文件和规则层，补明确默认脚本。
- [ ] 从零创建E2E，保留先确认后发布与下一轮新规则验收。
- [ ] 受影响应用套件、类型/编译/diff；更新架构/函数例子。复用已有审查代理，不新增子代理。
