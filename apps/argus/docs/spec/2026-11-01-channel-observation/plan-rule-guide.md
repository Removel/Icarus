# 规则文件编写与能力速查

更新时间：2026-11-02。以下针对当前实际实现，不是早期异步SDK草案。所有例子只包含合成内容。

## 1. 初始发现与Channel规则

没有active/paused订阅的来源，消息先入库，再由workflow_rules使用受信初始发现规则判断。默认机械识别“请帮/帮我/请跟进/提醒我/please help/please track/remind me”等明确请求，闲聊不建Channel；可用ARGUS_DISCOVERY_RULE_PATH替换整个发现脚本，ARGUS_DISCOVERY_ENABLED关闭。

发现命中后固定代码在两表事务中创建Channel、订阅当前来源、首条事件直接seed待办并保存事件发现结果。相同来源+外部消息身份确定Channel UUID，重投不重复创建。源码仍由规则插件文件管理，首个Channel规则默认不通过，等待Agent处理首条并确认后发布下一阶段规则。初始历史查询仅当前来源授权，不伪造系统Channel。

**限制：**已有订阅的来源不再独立发现新事，同群新话题不会自动拆出新Channel；默认初始机械规则不保证语义召回。业务判断可以扩展发现文件，但不应让每条消息都启动深度Agent。

## 2. Agent更新步骤

1. argus_channel_read取当前目标/进展/版本/订阅及rule_source。
2. 模型自行写候选源码，可先argus_rule_check检查、编译和试跑；传source与当前事件refs，返回rule_hash/诊断/样例Decision，不激活。
3. argus_confirm报告全部本轮handled/no_action，等待applied=true；最终视图送达与处理确认不是一回事。
4. argus_channel_update传expected_version、候选rule_hash和需要修改的progress/source_keys/lifecycle；不传任意rule_path。规则层复核文件与事务门禁。
5. 成功后argus_finish(update=committed)；不需要更新则确认后finish(unchanged)。版本冲突重读，不能盲覆盖。

## 3. 语法

每文件恰好一个同步函数，普通JSON字典输入，字典输出：

```python
def decide(event, channel):
    text = event["content"].get("text", "")
    relevant = "评审通过" in text
    return {"passed": relevant, "reason": "等待评审结论"}
```

支持普通赋值、if/else、for/while、布尔/比较/算术、字典/列表/元组、下标、列表/字典推导及生成器。支持len/str/int/float/bool/any/all/min/max/sum/sorted/range/abs，以及get/lower/upper/strip/startswith/endswith/count/split/items/keys/values等有限方法。

不支持import、async/await、类、额外函数/嵌套函数、装饰器、注解、顶层常量声明、任意属性访问、私有/dunder、open/eval/exec、直接文件/网络/SQL。规则可以循环但受CPU与墙钟上限，不是允许无限运行。

返回passed/reason必需；evidence_refs列表与defer可选。passed和defer不能同时true；异常是错误，不能自动变ignore。源码≤32KiB，一次最多3次能力调用，其中semantic最多1次。

## 4. 当前六个函数

统一 `capability(name, args)`，走现有typed Icarus Capability。没有配置的外部服务返回错误，不是假空结果。

| name | args | 返回 |
|---|---|---|
| history | source_keys可选，limit默认20且1..100，before/text可选 | observations标准事件数组；订阅授权范围和本次固定边界内最新修订；发现阶段只当前来源 |
| mechanical | text字符串、contains字符串数组 | match布尔；当前语义是所有子串都存在，不是完整DSL |
| semantic | text、criterion（或question），context可选 | 配置后返回match；现typed后端另有score/reason。最多1次 |
| memory | query、limit默认10且1..50 | Mem0服务JSON；user/agent/workspace由可信配置限定 |
| relationships | query≤256字符 | 关系服务/persons查询结果，limit固定20；当前不是任意图查询 |
| knowledge | query，kb可省略且不得覆盖配置 | 配置知识库的查询结果，非流式、不保存新报告 |

### 错时多来源

```python
def decide(event, channel):
    if "评审通过" not in event["content"].get("text", ""):
        return {"passed": False, "reason": "尚无评审结论"}
    history = capability("history", {"text": "接口", "limit": 20})
    has_request = any("评审" in row["content"].get("text", "")
                      for row in history["observations"])
    result = capability("semantic", {
        "text": event["content"]["text"],
        "criterion": "是否明确给出了评审通过结论？",
        "context": channel["progress"]
    })
    return {"passed": has_request and result["match"], "reason": "多源证据判断"}
```

历史读取不推进消费cursor，不自动确认被查询记录，也不扩大本轮待确认集合。函数的实际可用性和模型质量需分别验证，不把接口测试通过当真实业务语义正确。
