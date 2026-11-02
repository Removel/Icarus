# Argus 已实现契约与调用顺序

更新于2026-11-02。对应当前`feat/argus-plugin-refactor`首期代码，不是远端已部署声明。[当前架构](arch.md) · [执行记录](plan-execution.md) · [验收记录](plan-validation.md)。

## 1. 两表事实与总线

`ObservedItem`实现类型名为 `contracts.models.Observation`：id/source_key/message_id/kind/revision/observed_at/content/detail。数据库另有position入库位置，cursor为该位置字符串，不以UTC时间抗时钟回退排序。

`Channel`：id/title/goal/progress/lifecycle/version/subscriptions/rule_path/rule_hash/runtime。没有其他业务表；runtime只存有界pending和一个当前round，不存历史轮次。version只为业务更新CAS，不随送达/消费变化。

- ObservationRecorded：持久事件已提交后，由协议插件publish，包含observation_id/source_key。
- RulePassed：规则结果已提交后publish，只带channel_id；Bridge从Store复核并冻结当前可投递项，不盲信Bus传入正文/版本。
- RuleControlRequest：typed Capability，channel_id + args.observation_id，响应是传输status和data。data中的`applied`才表示业务成功。
- 普通Bus按来源插件身份路由；Channel/source_key匹配由业务规则插件负责。通知丢失依据Store状态重扫，Bus不代替DB。

## 2. Agent回流

`POST /agent/events`，Bearer `ARGUS_AGENT_API_TOKEN`：

```json
{
  "binding_id": "当前轮绑定",
  "request_id": "本操作稳定ID",
  "kind": "agent.result_submitted",
  "payload": {
    "items": [{"observation_id": "具体修订UUID", "status": "handled"}]
  }
}
```

AgentProtocol先持久控制事实，再request规则插件。控制source_key为agent:<binding_id>，message_id=request_id，输入hash不可变；相同请求重放读固定result。外部消息入口不接受正文自报控制kind/身份。

| kind | payload | 规则层固定行为 |
|---|---|---|
| agent.delivered | observation_ids | 仅冻结项可标实际送达 |
| agent.result_submitted | items:[observation_id,status,reason?] | 仅已送达冻结项，handled/no_action，不允许部分非法批次半提交 |
| rule.check_requested | source,event_refs? | AST/lint、受限试跑并写候选hash，不激活 |
| channel.update_requested | expected_version,progress?/source_keys?/rule_hash?/lifecycle? | 全轮已确认+绑定有效+CAS+文件验证后一次最终更新 |
| agent.finish_requested | update=committed/unchanged | 全轮确认并与是否已发布一致，清当前轮 |

原始控制payload不在持久化前被添加rule_path，保持幂等输入稳定；路径由channel_id/hash服务端推导。外部客户端不应传rule_path。少量内部Store测试可直接使用可信路径；HTTP规则handler仍复核canonical路径/hash。

响应为 `{applied:true,...}` 或 `{applied:false,code:...}`。HTTP200或CapabilityStatus.OK都不等于业务applied。`complete_control`只允许成功完成rule.check；其他操作只能登记rejected，成功必须走apply_control门禁事务。

错误包括events_not_confirmed、invalid_confirmation、invalid_binding、stale_version、request_conflict、update_already_committed、source_forbidden等。确定性失败原ID结果固定，补齐前提后用新ID申请；未知结果按原ID重试，不换ID盲做副作用。当前未单独提供原request结果GET路由，相同POST提供幂等恢复。

## 3. 读取与管理

同Bearer鉴权：

- POST /channels：title、goal、source_keys、rule_source；受信管理入口创建，不是后台绕过确认的更新路径。
- GET /channels：受信管理列表。
- GET /channels/{id}?binding_id=...：绑定限域读取，返回当前rule_source。
- GET /observations/{id}?binding_id=...：本Channel订阅范围内的平台事实，不读其他控制记录。
- GET /channels/{id}/history?binding_id=...&limit=20&before=...&text=...：limit1..50，绑定Channel检查。

内部Store历史query支持具体observation_id或时间before；应用边界身份与来源范围仍必须验证。历史读取不确认记录、不推进cursor，也不扩大本轮处理集合。

## 4. 实际规则SDK

每文件只允许一个同步函数，不支持早期async三参数/声明头草案：

```python
def decide(event, channel):
    history = capability("history", {"limit": 20})
    found = any("review" in row["content"]["text"]
                for row in history["observations"])
    return {"passed": found, "reason": "review evidence"}
```

event/channel为普通字典，Channel只含业务快照，不含runtime/binding；Observation不传detail。规则source≤32KiB、IPC输入≤2MiB、结果/能力回复≤128KiB；规则执行另受总时长和OS资源限制。

函数映射为：

| 函数名 | 结果与约束 |
|---|---|
| history | observations数组；只查订阅来源，固定source_bounds，limit1..100 |
| mechanical | 条件比较结果；纯Python比较也允许 |
| semantic | typed match布尔值；真实后端在model_provider，最多1次/判断 |
| memory | 固定user/agent/workspace配置范围内召回；脚本不能覆盖身份 |
| relationships | 固定关系服务只读查询 |
| knowledge | 固定kb配置，只读查询；脚本不能切知识库 |

最多3次Capability调用。未配置、超时、越权和非法返回独立失败，不伪造False/空结果。Decision为passed/reason/evidence_refs/defer，passed+defer不能同时true；ignore只推进cursor，passed/defer/error保存必要pending。rule Engine.evaluate为宿主异步方法，不改变脚本入口的同步语义。

## 5. Bridge HTTP与确认顺序

Bearer `ARGUS_GATEWAY_TOKEN`，Gateway仅薄转发至同AgentRuntime：

- POST /argus/session {channel_id,request_id} → {session_id}。
- POST /argus/wake {channel_id,wake_id,binding_id,channel_version,session_id,items,argus_url} → {task_id,status}。
- POST /argus/status {wake_id} → {task_id,session_id,status}。

items≤20且Gateway完整包络≤512KiB，Store先以384KiB完整items预算冻结。过大单条保delivery_item_too_large显式耗尽错误，不截断/假确认；可继续处理其他小项。稳定wake指纹拒绝更改目标/内容。保留未知占用，已知终态可reconcile，确认项不重投，未确认最多3次有限重投。

实际顺序：recorded → 最终模型视图完整才delivered → Agent明确handled/no_action且远端接受才confirmed → 全冻结集合confirmed后才可update → finish(committed或unchanged)。新到事件留后续轮，不改变本轮集合。Agent本地guard与Argus服务端事务门禁同时存在；验收通过直接HTTP证明即使绕过本地guard仍不能提前更新。

示例：先确认#1、未确认#2时更新失败；确认#2后以expected_version=7发布为8；重放相同成功请求仍返回8；无需更新则全部确认后unchanged结束。确认已接受但发布失败不撤销确认，不能重做已完成外部动作。

## 6. 可靠性和边界

pending≤256，满则背压不推进cursor；每项有配置版本、有限判断重试和有限投递次数，陈旧非冻结修订/移除来源关联会退休。各Channel恢复隔离；paused/completed仍对账已有轮次但不开新轮。

源码只在独立`-I`子进程执行，AST限制+OS limits+kill/wait，不是恶意多租户安全沙箱。规则文件不可变hash定位，先制品后指针，DB失败保旧规则。没有旧数据迁移、独立PG容器或自动生产角色创建。

首期未提供自动发现、任意外部动作工具、多机调度、自动留存GC或无限历史幂等；部署边界与实际测试见[架构](arch.md)和[验收记录](plan-validation.md)。
