# JEV直连与真实业务场景验收记录

日期：2026-11-02。状态：**JEV真实语义样本通过；真实业务闭环未通过。** 本文依据本次实际运行输出和当前源码整理，不是生产上线证明，不替换历史失败记录。测试结束后未追加付费重试、未修改阈值或脚本来改变结果。

## 1. 结论

1. `http://100.70.8.11:8009` 是JEV HTTP API服务，不是代理。按此base_url、对目标NO_PROXY直连后，请求已成功。
2. 两轮相同语义矩阵，每轮24条，均24/24符合人工预期。覆盖6个业务主题的同义正例、否定、引用与无关文本；重复同一矩阵不增加独立样本量，不能宣称通用准确率100%。
3. 完整链路进入第一个仓储接口评审场景，但初始发现3次均defer，未创建Channel、未唤醒深度Agent；事件发现状态最终exhausted。600秒等待后脚本按失败停止，退出码1。
4. 其余5个完整业务场景未运行；规则编写、JEV辅助规则执行、跨源跟进、原子commit及最终completed均未在本次真实模型业务运行中得到验证。已有确定性测试不能替代此项真实验收。
5. 失败不是预算耗尽、网络不通或模型HTTP协议错误；问题发生在发现业务判断和测试等待/诊断设计中。

## 2. 测试配置与方法

- API base_url：`http://100.70.8.11:8009`，SDK请求路径`/v1/systemone`。
- SDK model字段：`typesafe/jev-1.13`。这是客户端请求标识，不推断远端实际部署版本。
- 继续使用现有ARGUS_JEV_API_KEY，不展示密钥、不自动换key；base_url和NO_PROXY仅本次进程覆盖，未写入持久环境配置。
- 深度Agent配置模型：deepseek-flash；本次完整链路没有实际调用它。
- QQ/飞书消息为合成官方格式HTTP，真实Argus/Gateway Uvicorn、Icarus Host、正常AgentRuntime和临时PostgreSQL。没有真实群聊读取、消息外发、预约或付款。
- 每轮矩阵包含24项，业务目标和人工标签固定；不让测试脚本预写正确规则或教模型工具调用顺序。
- 请求记录不包含Authorization、密钥、原始错误HTTP body。合成业务与评估输出保存在权限0700目录内。

### 运行入口

`apps/argus/test/manual_real_scenarios.py`提供semantic/full模式，复用`manual_real_model.py`的真实业务链路及`real_scenarios.py`的自然业务样本。

本次首次连通后先单独执行semantic矩阵24次，再运行full模式。full会再次预检24次，然后才进入业务链路。这是重复预检，不是48条不同样本。

## 3. 语义矩阵结果

| 业务主题 | 同义正例 | 否定 | 引用规则/条件 | 无关消息 | 每轮 |
|---|---|---|---|---|---|
| 仓储接口评审 | 符合 | 符合 | 符合 | 符合 | 4/4 |
| 家庭体检预约 | 符合 | 符合 | 符合 | 符合 | 4/4 |
| 库存服务生产发布 | 符合 | 符合 | 符合 | 符合 | 4/4 |
| 洗衣机送货 | 符合 | 符合 | 符合 | 符合 | 4/4 |
| 供应商到账跟进 | 符合 | 符合 | 符合 | 符合 | 4/4 |
| 空调修复/试机 | 符合 | 符合 | 符合 | 符合 | 4/4 |

两轮结果相同。矩阵调用的是标准semantic match接口，附有明确的业务criterion，不是初始事项发现的Choice＋confidence接口。这两种任务必须分别评价。

- 第一轮：UTC12:10:49.909至12:10:55.345；24次调用耗时相加5.413秒，中位0.167秒，最大1.244秒。
- full内预检：UTC12:12:06.932至12:12:11.246；24次调用耗时相加4.302秒，中位0.167秒，最大0.363秒。
- 上述为本机测得单次调用墙钟，不是服务端推理耗时或生产SLO。

## 4. 完整业务链路结果

| 计划场景 | 实际执行 | 结果 |
|---|---|---|
| work_review：QQ起始、飞书结论 | 已发送QQ首条；平台入库HTTP200 | **失败：发现3次defer，未建Channel** |
| life_health：体检预约 | 未执行 | 前一场景失败后停止，不能记为失败或通过 |
| work_release：生产发布 | 未执行 | 同上 |
| life_delivery：送货 | 未执行 | 同上 |
| work_payment：付款状态 | 未执行 | 同上；不包含实际付款动作 |
| life_repair：维修 | 未执行 | 同上 |

完整链路时间UTC12:12:14.529至12:22:15.815（北京时间20:12:14至20:22:15）。

首条合成请求：

> 请帮我跟进仓储接口评审，目前还没有最终结论。后续可能从飞书来源 feishu:synthetic-tenant:chat:oc_review_synthetic 收到消息，也请一起关注。只整理和跟进，不要替我发消息。

服务端已入库Observation `7c77741c-f1d3-44ac-86f1-ed47e34595b2`。发现上下文候选Channel为空。JEV发现调用3次均返回：

```json
{"match": false, "score": 0.4639, "reason": "typed discovery judgement", "discovery": {"action": "defer"}}
```

单次耗时0.435/0.419/0.383秒，相加1.237秒。现有代码score<0.7即defer。经过既有5/30秒退避、最多3次后，事件detail.discovery为：

```json
{"status": "exhausted", "attempt_count": 3, "error_code": "typed discovery judgement"}
```

之后没有Channel、Agent task、生成规则或业务commit回执。测试最终`initial_settled=false`、`pipeline_passed=false`。失败后未追加付费尝试。

## 5. 发现的问题及解决位置

### P1：发现门禁将明确新请求持续延后，阻断从零启动

**实证：** 清晰的跟进请求，候选为空，3次0.4639→defer→exhausted；无Channel，因此后续所有能力都没有进入。

**实现机制：** `apps/argus/src/argus_app/model_provider/semantic.py:44-55`在同次请求中分别提问route Choice和confidence Noul。confidence只有“所选归属是否有明确事实依据，而不是猜测？”的文本，输入state里没有已选route，也没有显式传递两问依赖。代码随后把该Noul值直接解释为分类置信度并用0.7门槛截断。

**能确定：** 当前发现门禁在此样本上导致漏发现；confidence请求没有包含所选route的显式条件。

**尚不能确定：** 0.4639的唯一成因、原始Choice到底选了new还是ignore、远端是否隐式关联两问。当前输出丢弃了原始route，不能把猜测写成已验证根因。

**建议：** 先保存原始typed分类与Noul结果，再把需要判断的命题明确表达为“这条消息是否提出值得建立新事项的请求”，或采用有明确候选条件的归属评估。阈值需要用发现专用正反样本校准，不能为了这一条过关直接调低到0.46。实现调整归model_provider与发现固定handler，不让上层按模型供应商协议分支。

### P2：测试脚本忽略发现exhausted终态，浪费等待时间

`apps/argus/test/manual_real_model.py:211-229`的settle只检查全局预算停止或已有Channel的轮次/pending；没有Channel时不识别detail.discovery.exhausted，也不识别ignored。

**实证：** 发现约几十秒内已结束为exhausted，但脚本等满600.16秒，最后仅报告“Unsettled round”。这不是供应商延迟，也不是Agent慢。

**建议：** 读取发现状态；exhausted立即返回discovery_exhausted及attempts/诊断，ignored返回明确“不构成新事项”的结果；error/deferred按照真实next_retry_at等待。将“未创建Channel”和“Agent处理未结束”分开计时。

### P2：报告缺失原始分类，阻碍准确诊断

`semantic.py:48-55`在score低时只返回defer，丢弃route；reason固定为typed discovery judgement。Store将这个固定reason写成error_code，无法区分是置信度门禁、输入歧义还是能力错误。

**建议：** 受信诊断记录保存候选ID集合/上下文版本、原始route、score、阈值、最终action和明确的decision_deferred_low_score原因码。不能把消息正文当控制信息，也不记录密钥/内部绑定。

### P2：预检覆盖不匹配真实入口；业务矩阵首场景阻塞后其余未执行

标准semantic match的24项全通过，但full从discovery入口开始，该入口的typed Choice＋Noul没有包含在预检。`manual_real_model.py:240-243`在首场景未settle时保守停止，保护不重叠执行，但导致其他5组业务未测。

**建议：** 增加空候选明确请求、闲聊、有候选延续、新话题、歧义等发现专用预检；多场景应按场景隔离数据库/应用或明确取消并对账旧轮次后继续，不能为了跑满6组直接让可能仍在运行的Agent任务互相叠加。当前停止行为是保守保护，不应仅删除break。

## 6. 预算、用量与未验证项目

| 项目 | 授权/设置 | 实际 |
|---|---|---|
| 深度Agent | 总240次、每场景80次 | **0次**，未唤醒 |
| JEV | 本次成功连通后的总120次 | 第一矩阵24＋full矩阵24＋发现3＝**51次SDK调用** |
| full内JEV | 另设96次，已扣除第一轮24次 | 27次 |
| 安全审查模型 | 未启用 | 0次 |
| 业务超时 | 600秒 | 首场景等待600.16秒 |
| token/账单 | 未取得JEV usage/账单 | **不可推算**；深度模型usage为空 |

以上51不包含此前连通/鉴权失败尝试。SDK配置max_retries=0，发现3次是业务层既定重试，不是SDK静默重试。没有耗尽请求预算，不能归因预算太低。

未验证：Agent自主生成规则、否定/引用在实际生成规则上的回放、实际业务commit恢复、同群不同事项的真实模型归属、真实平台权限和生产性能。本轮只证明这些人工样本的标准semantic判断与真实API可用，不证明整套系统可靠。

## 7. 原始记录与清理

- 首轮语义：`/tmp/icarus-argus-jev-direct8009-20261102-02/semantic-summary.json`。
- full预检：`/tmp/icarus-argus-live8009-20261102-01/semantic-summary.json`。
- 总结：`/tmp/icarus-argus-live8009-20261102-01/acceptance-summary.json`。
- 业务输入/发现回执：同目录`pipeline/events.json`。
- 业务详情与调用：同目录`pipeline/summary.json`。
- 本地命令输出：gitignored ledger目录`live8009-acceptance.log`。

没有生成model请求文件、Agent journal或规则文件，是因为Agent根本未启动，不是记录漏传。任务专用临时PG容器`icarus-argus-live8009-test-693d3ba8`已检查身份并停止（自动移除）；测试随机DB由脚本清理。未变更生产数据库，未提交或推送代码，本次整理不自动修复发现的问题或追加模型费用。
