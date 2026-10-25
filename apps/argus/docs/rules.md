# Argus 规则配置指南

本文说明当前实现支持的规则语法。规则文件是 `apps/argus/rules/qq.yaml`，运行配置是
`apps/argus/settings.json`。文中的自定义规则都是示例，不会自动写入运行规则。

当前递交后的处理端仍是 mock：`wake` 表示创建并递交叫醒请求，不代表真实 agent 已执行任务。

## 1. 默认规则与执行顺序

默认规则如下：

| 情况 | 命中规则 | 行为 |
|---|---|---|
| 普通私聊 | `private_message`，优先级 200 | 叫醒，不调用 Jev |
| 群聊中有人 @ 或叫机器人名字 | `mention_bot`，优先级 100 | 叫醒，不调用 Jev |
| 其他群聊内容 | `speak` / `stay_silent` | 启用 Jev 时，先判断 `can_speak`（是否适合说话），再判断 `speak_now`（现在就说吗）；两个都是 yes/no |
| Jev 关闭、失败或返回非法结果 | 无叫醒规则 | 忽略，并记录原因 |
| `can_speak` 或 `speak_now` 为否 | `stay_silent` | 忽略，`decision_detail` 保留两个概率 |

处理过程：

```text
保存观测
  → 调度时机：群聊去抖，私聊 / @ / 名称强信号尽快判断
  → 基本检查和预算选择
  → 按优先级查找第一条命中的机械规则
  → 若机械规则没命中，筛选符合 when 的语义规则
  → Jev 先回答 can_speak（是/否），再回答 speak_now（是/否）；两次都是是才唤醒
  → 保存忽略结果，或创建叫醒请求
```

基本检查会排除撤回、非待处理、已关联请求、自身消息、敏感内容和权限不足的记录。规则不能
让已经排除的消息重新参与。本轮没有可选消息时也不会调用模型。

**尽快判断不等于保证叫醒。** 例如某个群被高优先级规则设为忽略，即使 @ 机器人，仍然只是
尽快运行判断，最后不递交。同一聊天对象已有活跃请求时，先保存新消息，等当前请求结束后再判断。

## 2. 文件结构

下面是可独立加载的最小示例。实际默认文件还包含群聊语义规则。

```yaml
version: 1
source: qq

default:
  decision: ignore
  reason: "没有命中任何叫醒规则"

rules:
  - id: private_message
    description: "私聊即为明确寻址"
    priority: 200
    when:
      scope_kind: private
    decision: wake
    reason: "有人私聊 Icarus"

  - id: mention_bot
    description: "有人 @ 或叫机器人名字"
    priority: 100
    when:
      mentions_bot: true
    decision: wake
    reason: "有人直接找 Icarus"
```

### 文件字段

| 字段 | 要求与用途 |
|---|---|
| `version` | 正整数，表示规则版本，不是数据库版本；建议修改规则时递增 |
| `source` | 当前为 `qq`，必须与来源匹配 |
| `default` | 必须包含 `decision: ignore` 和字符串 `reason`；不允许默认叫醒 |
| `rules` | 规则列表，可为空列表 `[]` |

### 每条规则的字段

| 字段 | 必填 | 用途 |
|---|---|---|
| `id` | 是 | 非空、唯一的规则 ID；`none` 是保留值，不能使用 |
| `description` | 否 | 默认空字符串；语义规则会把此文字提供给 Jev，应写清判定标准 |
| `priority` | 否 | 整数，默认 0，数值越大越靠前 |
| `semantic` | 否 | 布尔值，默认 false；true 表示由 Jev 语义选择 |
| `when` | 否 | 适用条件；省略或 `{}` 表示恒成立 |
| `decision` | 是 | 只能为 `wake` 或 `ignore` |
| `reason` | 是 | 命中后保存的固定理由，不由模型自由生成 |

不支持的顶层键、规则字段、事实名称和操作符会被拒绝。普通机械规则如果省略 `when`，会无条件
命中并结束本轮规则查找，通常应只用于明确需要的兜底行为。

## 3. 机械规则和语义规则

### 机械规则

`semantic: false` 或省略该字段。按 `priority` 从高到低逐条匹配，**第一条命中即结束**。
同优先级按文件中的先后顺序，没有多条结果合并，也不是所有 ignore 永远优先于 wake。

只要机械规则命中，就不再调用 Jev，即使某条语义规则的 priority 更高。

### 语义规则

机械规则均未命中时，只把满足 `when` 的语义规则作为候选。例如下面这条可追加到 `rules`：

```yaml
- id: speak
  description: "在这个窗口里，Icarus 现在说话是合适/可以的：有人向 Icarus 提问、求助或等待回应；话题直接涉及 Icarus；群里有开放问题没人接；Icarus 有新的信息可以补充；现在插话不会打断别人。"
  semantic: true
  when:
    scope_kind: group
  decision: wake
  reason: "现在适合 Icarus 说话"
```

`when` 是候选准入条件，不是模型结论。所有符合条件的语义候选一起交给 Jev；Jev 先回答
`can_speak`（是否适合说话），再回答 `speak_now`（现在就说吗），两个都是 yes 才唤醒，
不生成新的规则或自由文本理由。`speak` 和 `stay_silent` 的描述分别作为 `can_speak` 的
true/false 说明；`speak_now` 的时机说明由服务提供。

语义判断的 `state` 是一个按时间排序的 `window`，每条包含 `sender`、`text`、`sent_at` 和
`current`。Icarus 自己的消息也作为上下文出现，但不作为当前待判断的候选。

窗口优先来自本地 `observed_items`；配置 `qq.history` 时，Argus 通过 SnowLuma 的
`get_group_msg_history` / `get_friend_msg_history` 回填缺失消息，按 `message_id` 去重，
历史不可用时回退到本地观测。

语义规则也按 priority 排序，但**高优先级不保证 Jev 选中它**。如果需要某条件必定叫醒或忽略，
应写为机械规则。当前 `can_speak` 和 `speak_now` 的阈值固定为 0.5，没有可配置的置信度阈值。

没有语义候选时默认忽略；Jev 关闭记录 `jev_disabled`，调用失败或非法结果记录 `jev_failed`；
任一问题为否映射为 `stay_silent`，并在 `decision_detail` 保留 `can_speak` 和 `speak_now`
两个概率用于统计。这些忽略不是稍后自动重新判断的等待状态。

## 4. 可用事实

条件针对的是**本轮预算内、通过基本检查的消息批次**，不是逐条消息独立运行。

| 事实名 | 类型 | 含义 |
|---|---|---|
| `source` | 字符串 | 当前为 `qq` |
| `chat_id` | 字符串 | `qq:<机器人账号>:<group或private>:<群号或对方账号>` |
| `scope_kind` | 字符串 | `group` 或 `private` |
| `item_count` | 整数 | 本轮选中的消息数量 |
| `mentions_bot` | 布尔值 | 本轮任一消息 @ 机器人或命中配置的机器人别名 |
| `is_self_only` | 布尔值 | 是否全部为自身消息；正常链路已提前排除自身消息，一般无需使用 |
| `text` | 字符串 | 本轮选中消息的短摘录，用换行拼接 |
| `sender_ids` | 字符串集合 | 本轮发送者 ID，保序去重 |
| `sender_names` | 字符串集合 | 本轮非空显示名，保序去重；不是稳定身份标识 |
| `permission_level` | 整数 | 当前聊天对象配置的静态权限档位 |
| `newest_sent_at` | 带时区 datetime | 本轮最新的平台发送时间 |
| `oldest_observed_at` | 带时区 datetime | 本轮最早的接收时间 |

时间事实目前是 Python datetime，不是字符串；没有方便的 YAML 相对时间表达式。不建议直接用
字符串比较这些时间，也不能写“最近十分钟”这样的表达式。

### 批次与摘录的影响

- 默认每轮最多 20 条，消息摘录总预算 20000 字符；单条过长时只用短摘录判断，原文仍可查询。
- 只有真正参与本轮判断的消息会被忽略或关联叫醒；预算外消息留待后续处理。
- 一条群 @ 触发叫醒时，递交的是本轮选中的整批消息，不只单独递交那条 @。
- `text` 条件只检查本轮摘录，不能保证匹配完整原文中的尾部关键词。敏感过滤则先检查全文。
- 历史上文只为 Jev 提供辅助文本，不参与这里的机械 facts。
- `sender_ids` 和 `text` 是分别聚合的。组合“包含用户 A”与“包含报错”不能证明报错就是 A 发的。
  当前没有逐消息绑定发送者和文本的条件语法。
- Jev 实际输入不包含 sender_ids、sender_names、chat_id 或定位字段；语义 description 不应要求
  模型根据它看不到的账号身份判断。需要身份筛选时，使用机械条件或语义规则的 `when`。

## 5. 条件语法

同一个 `when` 下的多个键默认是“并且”，同一事实下的多个操作符也全部需要满足。

### 等于的简写

```yaml
when:
  scope_kind: group
  mentions_bot: true
```

等价于：

```yaml
when:
  scope_kind:
    eq: group
  mentions_bot:
    eq: true
```

### 比较操作符

| 操作符 | 含义 | 示例 |
|---|---|---|
| `eq` | 等于 | `scope_kind: {eq: group}` |
| `ne` | 不等于 | `scope_kind: {ne: private}` |
| `in` | 事实值属于给定列表 | `scope_kind: {in: [group, private]}` |
| `contains_any` | 包含列表中的任意一个值 | `text: {contains_any: ["报错", "异常"]}` |
| `contains_all` | 包含列表中的全部值 | `text: {contains_all: ["部署", "失败"]}` |
| `gte` | 大于或等于 | `item_count: {gte: 3}` |
| `lte` | 小于或等于 | `item_count: {lte: 10}` |
| `exists` | 事实键是否存在 | `text: {exists: true}` |

注意：

- `in`、`contains_any`、`contains_all` 的右侧必须是 YAML 列表。
- `text.contains_any` 是区分大小写的子串匹配，不是分词、正则或语义匹配。
- `sender_ids.contains_any` 是集合成员匹配；ID 必须写为字符串，例如 `["123456", "789012"]`。
- `exists` 检查键是否存在，不检查值是否为 null 或空字符串。当前 QQ facts 都会提供上述已知键。
- 字段缺失时，除 `exists` 外的比较返回 false，包括 `ne`；类型不兼容可能在运行时被拒绝。
- 组合与事实名在加载时校验，但不是所有比较类型都能提前校验；离线加载成功不等于所有输入
  已经验证通过。自定义条件应再用代表性 facts 试算。
- 机器人别名识别会做 NFKC 和大小写归一化；这不改变普通 `text` 规则的区分大小写行为。

### all / any / not

下面表示：这是群聊，且满足“有人找机器人”或“出现求助词”，同时没有广告词。

```yaml
when:
  all:
    - scope_kind: group
    - any:
        - mentions_bot: true
        - text:
            contains_any: ["报错", "求助"]
    - not:
        text:
          contains_any: ["广告", "抽奖"]
```

`all`、`any` 接收非空条件列表；`not` 接收一个条件对象。可以嵌套使用。

## 6. 常见自定义规则

以下条目应加入实际文件的 `rules` 列表，不是替换整个文件。

### 某个群只记录，不叫醒

```yaml
- id: mute_specific_group
  description: "这个群只观测，不递交"
  priority: 1000
  when:
    chat_id: "qq:123:group:456"
  decision: ignore
  reason: "该群配置为只观测"
```

优先级高于默认 mention_bot 的 100，因此这个群的 @ 也不会叫醒；观测仍保存。替换 chat_id 中
的机器人账号和群号，不要只填写群号。

### 群里出现明确技术求助词时叫醒

```yaml
- id: technical_help
  description: "群里出现明确的报错求助"
  priority: 80
  when:
    scope_kind: group
    text:
      contains_any: ["Traceback", "部署失败", "帮忙排查"]
  decision: wake
  reason: "群内出现技术求助信号"
```

它低于默认的 @ 规则，但在机械规则阶段运行，不需要 Jev。如果担心误触发，可以改为语义
候选，并在 description 中清楚说明何种情况才算求助。

### 仅在指定账号参与时启用某个语义候选

```yaml
- id: selected_user_question
  description: "这批消息中存在需要 Icarus 回答的问题，而不是普通闲聊"
  semantic: true
  when:
    scope_kind: group
    sender_ids:
      contains_any: ["123456", "789012"]
  decision: wake
  reason: "指定账号参与的讨论中存在待回答问题"
```

这是候选准入，不是整个系统的账号白名单；其他默认规则仍有效。这里也不意味着该问题一定是
指定账号发出的，因为发送者和文本按批次聚合。

## 7. 修改、校验与生效

### 修改前置配置

仓库自带配置中，QQ 和 Jev 都默认关闭。

- 使用 QQ：在 settings.json 开启 `qq.enabled`，填写合法 bot_id 和 bot_names；环境中提供
  `ARGUS_DATABASE_URL`、`ARGUS_QQ_REPORT_SECRET`。
- 只用私聊、@ 和其他机械规则：不要求开启 Jev。
- 使用语义规则：还需开启 `jev.enabled`，通过环境提供 `ARGUS_JEV_API_KEY`。不要把密钥写入
  YAML 或 JSON。运行配置修改同样需要重启。
- `sensitive_keywords`、默认权限和分聊天对象权限在 settings.json 中配置，不是 YAML 条件语法。

### 离线检查规则

在仓库根目录执行下面命令。它只加载规则并用给定 facts 试算，不连接 PG、QQ、Gateway 或 Jev，
也不读取运行配置和密钥。

```bash
apps/argus/.venv/bin/python - <<'PY'
from pathlib import Path

from apps.argus.src.common.services.rule_service import (
    candidate_semantic_rules,
    evaluate_mechanical,
    load_rule_set,
)
from apps.argus.src.surfaces.qq.services.fact_service import QQFactService

surface = QQFactService()
rules = load_rule_set(Path("apps/argus/rules/qq.yaml"), known_facts=set(surface.known_facts))
if rules.source != surface.source:
    raise ValueError("Rule source must match QQ surface")
print(f"规则加载成功：version={rules.version}, mechanical={len(rules.mechanical)}, semantic={len(rules.semantic)}")

facts = {
    "source": "qq",
    "chat_id": "qq:123:group:456",
    "scope_kind": "group",
    "item_count": 1,
    "mentions_bot": True,
    "is_self_only": False,
    "text": "@123 帮忙看看报错",
    "sender_ids": ("777",),
    "sender_names": ("示例用户",),
    "permission_level": 2,
}
matched = evaluate_mechanical(rules, facts)
print("机械命中：", None if matched is None else (matched.id, matched.decision))
if matched is None:
    print("语义候选：", [rule.id for rule in candidate_semantic_rules(rules, facts)])
PY
```

修改示例 facts，分别验证应该叫醒和不应该叫醒的场景。上例不演示 datetime 条件；如果自定义
规则使用了时间事实，应在试算时传入带时区 datetime，而不是字符串。

已有规则测试也可直接运行，不需要启动测试数据库：

```bash
apps/argus/.venv/bin/python -m pytest apps/argus/test/common/services/test_rule_service.py -q
```

这些测试验证规则引擎，不会自动替你验证所有自定义业务规则；需要针对自定义规则增加对应场景。

### 生效方式

1. 修改 `rules/qq.yaml`，建议递增 version。
2. 离线加载和试算；需要时补自定义规则测试。
3. 等当前请求处理结束后，按应用生命周期停止并启动 Argus：

   ```bash
   ./bin/icarus stop argus
   ./bin/icarus start argus
   ```

**当前没有热重载，QQ 启用时每次启动加载一次。** QQ 关闭时不会加载 QQ 规则。
重启前尚未处理的记录不会恢复，因此修改规则时应留意当前工作和积压情况。旧规则已忽略的消息
不会因为改规则自动重新判断，也没有历史回放功能。

## 8. 当前不支持的语法

不支持正则、Python 表达式、任意函数、`gt` / `lt`、相对时间、逐消息变量绑定、模型置信度阈值、
多规则结果合并和运行中热更新。不能通过增加一个 YAML 键启用这些能力。

YAML 使用空格缩进；字符串 ID、可能被识别为布尔值或日期的文本建议加引号。语法或字段错误
应先修正再重启，不能假设加载器会静默跳过错误规则。
