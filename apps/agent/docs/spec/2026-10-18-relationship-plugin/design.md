# relationship 插件设计

| 项目 | 内容 |
|---|---|
| 文档版本 | v1.0 |
| 日期 | 2026-10-18 |
| 状态 | 已实施；验证记录见 plan.md，当前架构见 arch.md |
| 目标应用 | `apps/agent` |
| 后端服务 | `apps/rela-mem` |
| 插件 ID | `relationship` |
| 交互方式 | Agent Tool，不自动召回、不注册 Blackboard Region |

> 本文保留目标与工具契约，并纳入 2026-10-18 用户确认的审查修订；当前已验证源码架构见 `arch.md`。

## 1. 目标与边界

把 rela-mem 接入 Agent，作为人物身份、档案和关系的工具能力。

它负责：

- 按名字或精确外部身份查找人物；
- 读取人物上下文（档案、圈子、关系）；
- 写入稳定档案事实；
- 删除档案事实；
- 写入或失效人物关系。

它不负责：

- 事件、经历和对话叙事；这些仍由 MemoryPlugin / Mem0 负责；
- 每轮对话前的自动召回；
- 启动门闩、Blackboard Region 或动态 System Prompt 修改；
- 人物改名、归档、圈子成员管理等低频管理操作；
- Argus 接入、WebUI、跨服务事务。

### 1.1 与 MemoryPlugin 的分工

| 内容 | 写入方 |
|---|---|
| “我上周去摄影了” | `memory_remember` |
| “我喜欢摄影” | `relationship_remember` |
| “姐姐说猫罐头从 50 降到 10” | `memory_remember` |
| “姐姐的猫喜欢鸡肉味猫罐头” | `relationship_remember` |
| “张三是李四的表哥” | `relationship_relate` |

分类由主 Agent 决定。RelationshipPlugin 内部不运行 LLM、不做二次分类、不监听每轮输入。

### 1.2 第一版范围

工具：

```text
relationship_find
relationship_context
relationship_remember
relationship_forget
relationship_relate
```

不包含：

- `relationship_circle`（圈子创建、成员加入/移除）；
- 人物改名、归档、硬删除；
- 档案历史读取；
- 关系历史。

## 2. 总体架构

```text
ReActAgent
   │ tool calls
   ▼
RelationshipPlugin
├── RelationshipBackend（Protocol）
├── RelaMemHttpAdapter（httpx）
└── 5 个固定 Tool
        │ REST
        ▼
    rela-mem :8767
```

依赖方向：

```text
agent_app.plugins.relationship → icarus.core.tools / icarus.runtime
agent_app.plugins.relationship → rela-mem HTTP
```

插件不导入 rela-mem 的 Python 模块，不把 HTTP DTO 泄漏到上层。Adapter 负责路径、参数、认证和错误映射。

### 2.1 文件布局

```text
apps/agent/src/agent_app/plugins/relationship/
├── manifest.json
├── factory.py
├── plugin.py
├── config.py
├── backend.py
├── rela_mem_http_adapter.py
├── models.py
├── tools.py
└── prompts.py

apps/agent/test/agent_app/plugins/relationship/
├── test_adapter.py
├── test_factory.py
├── test_plugin.py
├── test_tools.py
└── test_prompt_contribution.py
```

测试镜像源码目录。插件内组件是普通对象，不注册子 Plugin。

## 3. 注册与配置

### 3.1 Manifest

```json
{
  "schema_version": 2,
  "plugin_id": "relationship",
  "plugin_version": "0.1.0",
  "entrypoint": "agent_app.plugins.relationship.factory:create_plugin",
  "python_requires": ["httpx>=0.27,<1"],
  "required_capabilities": [],
  "provided_capabilities": [],
  "provided_tools": [
    "relationship_find",
    "relationship_context",
    "relationship_remember",
    "relationship_forget",
    "relationship_relate"
  ],
  "published_events": [],
  "consumed_events": [],
  "state_scopes": [],
  "tier": "core",
  "required_config": []
}
```

- 不加入 `runtime.required_plugin_ids`。
- 不发布/消费 Event，不注册 Capability，不持有跨 Session 状态。
- 服务不可用时只让相关 Tool 失败，不阻塞 SessionRuntime。
- Factory 不做启动健康检查。

### 3.2 配置

`runtime.plugin_config.relationship`：

```json
{
  "endpoint": "http://127.0.0.1:8767",
  "timeout_seconds": 10
}
```

- `endpoint` 默认 `http://127.0.0.1:8767`；
- `timeout_seconds` 默认 10，范围 1–60；
- 严格校验，拒绝未知字段；
- Token 只来自环境变量 `RELA_MEM_API_TOKEN`，不进入 JSON、日志或 ToolResult；
- 非 loopback endpoint 且 Token 为空时 Factory 失败；
- 非 loopback 必须 HTTPS，拒绝 URL 内嵌凭据；loopback 可以 HTTP；
- loopback 也必须有 Token 才能实际调用 rela-mem；缺少 Token 时允许插件启动但工具返回安全认证错误。

默认加入 `apps/agent/src/agent_app/resources/default_settings.json` 的 `plugin_config.relationship`，但不加入 `required_plugin_ids`。

## 4. 工具契约

### 4.1 `relationship_find`

```json
{
  "query": "张三"
}
```

参数只有一个：

| 参数 | 类型 | 规则 |
|---|---|---|
| `query` | string | 1–256 字符 |

`query` 的两种形式：

```text
"张三"           -> 按名字/别名模糊搜索
"qq:123456"      -> 精确 QQ
"wechat:wxid"    -> 精确微信
"phone:138..."   -> 精确手机号
```

代码逻辑：

1. 匹配 `^[a-z][a-z0-9_]{0,31}:.+$` 且 namespace 不是 `name` → 调 `/v1/identity/resolve`；
2. 否则 → 调 `/v1/persons?query=...&limit=10`；
3. 精确未命中返回空列表，不报错；
4. 结果附带有限的 `aliases`，用于重名消歧。

返回：

```json
{
  "items": [
    {
      "person_id": "p_01J...",
      "display_name": "张三",
      "kind": "human",
      "status": "active",
      "aliases": [
        {"namespace": "qq", "value": "123456", "display_value": "小张"}
      ]
    }
  ],
  "ambiguous": false
}
```

`items` 多于一个时 `ambiguous=true`。工具不替用户选择。

### 4.2 `relationship_context`

```json
{
  "person_id": "p_01J...",
  "query": "猫罐头"
}
```

| 参数 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `person_id` | string | 是 | 已知人物 ID |
| `query` | string | 否 | 档案关键词；不传则返回当前档案 |

代码固定组合调用：

```text
GET /v1/persons/{person_id}
GET /v1/persons/{person_id}/profile?q=<query>&limit=20
GET /v1/persons/{person_id}/circles?limit=20
GET /v1/relations?person_id=<id>&include_incoming=true&limit=40
```

返回一个有界上下文：

```json
{
  "person": {"person_id": "p_...", "display_name": "张三", "aliases": []},
  "profile": {"items": [], "truncated": false},
  "circles": {"items": [], "truncated": false},
  "relations": {"items": [], "truncated": false}
}
```

- 不暴露 `include_profile`、`include_circles`、`include_relations`、`include_incoming`、`limit` 等参数；
- 默认包含 incoming 关系；
- 各段有固定上限，以服务 `has_more` 标记 `truncated`；所有返回路径 aliases 最多 10 个；
- context 总输出最多 24000 字符，超限省略完整项并标记截断；身份/姓名证据读取响应最多 1 MiB，无法取得完整证据时不自动选择；
- 任一段失败时 Tool 失败，不返回半真半假的数据；
- 不调用已删除的 `/persons/{id}/context` 端点。

### 4.3 `relationship_remember`

```json
{
  "display_name": "张三",
  "identities": ["qq:123456"],
  "profile": {
    "hobbies": ["摄影"],
    "cat_food_preference": "喜欢鸡肉味猫罐头"
  },
  "confirm_create": false
}
```

| 参数 | 类型 | 必填 | 规则 |
|---|---|---|---|
| `person_id` | string | 否 | 已有 ID；与 `display_name` 互斥 |
| `display_name` | string | 否 | 查找或创建人物时使用 |
| `identities` | string[] | 否 | `namespace:value`，最多 10 个 |
| `profile` | object | 否 | key → JSON value，最多 50 个 key |
| `confirm_create` | boolean | 否 | 只在名字无匹配、用户确认后为 true |

至少提供 `person_id`、`display_name`、`identities` 之一。

查找与创建规则：

1. `person_id` 存在：
   - 读取人物；不存在则 Tool 失败；
   - 不重命名、不改 kind。
2. 没有 `person_id`，但有 `identities`：
   - 逐个调用精确解析；
   - 所有命中指向同一个人 → 使用；
   - 命中不同人物 → `confirmation_required`；
   - 全部未命中 → 要求 `display_name` 并先搜索姓名候选；无候选才自动创建；
   - 有候选或搜索未穷尽 → `confirmation_required`；用户选择已有者后传 `person_id` 补身份，明确确认新人后可传 `confirm_create=true` 创建（2026-10-18 评审确认）；
   - 创建必须在同一次 POST 中提交所有初始 aliases，不先创建空人物再加身份。
3. 只有 `display_name`：
   - 按名字搜索；
   - 最多查询 10 个候选，`has_more=true` 时不能证明唯一，返回待确认；
   - 逐个读取完整候选详情，以 `strip().lower()` 比较 display_name 和 namespace=name 的 alias.value；仅精确同名且唯一时直接使用；
   - 只有子串/非姓名别名命中、多精确同名或证据不完整 → `confirmation_required`（2026-10-18 评审确认）；
   - 无匹配且 `confirm_create=false` → `confirmation_required`，`would_create=true`；
   - 无匹配且 `confirm_create=true` → 创建。

写入：

- `source` 固定 `agent`；
- `source_ref` 自动生成 `agent:<task_id>:<run_id>`；执行上下文没有 ID 时使用 `agent:<uuid>`；
- `recorded_at` 由服务端生成；
- `profile` 每个 key 调用 `PUT /v1/persons/{id}/profile/{key}`；
- 所有 `identities`（即使传了 `person_id`）先精确解析并验证归属，包含归档身份；不匹配时不写档案；
- `identities` 调用 `POST /v1/persons/{id}/aliases`；409 只有重新解析确认归属同一 active person_id 时才忽略，跨人物/归档/无法确认则停止；
- 预先验证完整批参数，先绑定身份再写档案；部分写入后失败明确报告可能部分完成，不宣称原子性或“没有写入”；
- 不写入 `kind=virtual`；宠物等事实按约定挂到主人人物下。

返回：

```json
{
  "status": "ok",
  "person": {"person_id": "p_...", "display_name": "张三"},
  "created": false,
  "written": ["hobbies", "cat_food_preference"]
}
```

### 4.4 `relationship_forget`

```json
{
  "person_id": "p_01J...",
  "keys": ["cat_food_preference"]
}
```

- `person_id` 必填；
- `keys` 非空，最多 50；
- 每个 key 调 `DELETE /v1/persons/{id}/profile/{key}?source=agent&source_ref=agent:<...>`；
- 只删档案，不删人物、关系或历史；
- 返回 `{"status":"ok","deleted":[...]}`。

### 4.5 `relationship_relate`

```json
{
  "from_person_id": "p_zs",
  "to_person_id": "p_ls",
  "kind": "kin",
  "forward_detail": "表哥",
  "reverse_detail": "表弟",
  "scope_circle_id": "c_family"
}
```

| 参数 | 类型 | 规则 |
|---|---|---|
| `relation_id` | string | 有值时执行失效，其他写入字段必须为空 |
| `from_person_id` | string | upsert 必填 |
| `to_person_id` | string | upsert 必填 |
| `kind` | string | upsert 必填，自由字符串 |
| `forward_detail` | string | upsert 必填，from 是 to 的什么 |
| `reverse_detail` | string | 可选；有值表示 two_way，无值表示 one_way |
| `scope_circle_id` | string | 可选 |

代码处理：

- `relation_id` 存在 → `DELETE /v1/relations/{id}`；
- 否则 `POST /v1/relations`；
- `direction = "two_way" if reverse_detail else "one_way"`；
- upsert 的 `source` 固定 `agent`，`source_ref` 自动生成；
- 失效 DELETE 当前仅更新状态/时间，不接收或记录此次 `source_ref`，返回 `{"status":"ok","invalidated":"r_..."}`；
- 不暴露 `source`、`source_ref`、`direction`、`operation`。

返回：

```json
{
  "status": "ok",
  "relation": {"relation_id": "r_...", "direction": "two_way"},
  "created": true
}
```

## 5. 确认与错误契约

### 5.1 需要用户确认

歧义或待创建时返回 Tool 成功、业务状态为待确认：

```json
{
  "status": "confirmation_required",
  "reason": "ambiguous_person",
  "candidates": [
    {"person_id": "p_1", "display_name": "张三", "aliases": []},
    {"person_id": "p_2", "display_name": "张三", "aliases": []}
  ],
  "would_create": false
}
```

- `success=True`；
- 不写任何数据；
- 模型必须向用户展示候选或询问是否创建；
- `would_create=true` 时，用户确认后再次调用 `relationship_remember`，并传 `confirm_create=true`。

### 5.2 工具失败

- 参数错误、人物不存在、服务不可用、HTTP 非 2xx → `ToolExecutionResult(False, error=...)`；
- 错误文本以 `relationship ... failed:` 开头；
- 不包含 Token、请求头、完整 HTTP body 或远端堆栈；
- 不自动重试写请求；
- 服务不可用不影响其他 Tool 和 Session。

## 6. 静态 Prompt

插件实现 `PromptContributor.static_prompt_sections()`，使用 `session` 层。内容：

```text
人物档案与事件记忆的区分：
- 有时间点、发生过一次、属于经历 -> memory_remember
- 稳定人物属性、偏好、身份 -> relationship_remember；关系 -> relationship_relate；圈子成员管理本版不支持
- 一句话里两者都有 -> 分别调用两个工具
- 不确定是事件还是档案 -> 先问用户，或只查询不写入

人物查询：
- 用户提到具体人物时，先 relationship_find；需要档案/关系/圈子时再 relationship_context。
- 用户只给名字且出现多个候选时，必须让用户选择，不能猜测。
- 精确身份使用 qq:/wechat:/phone: 前缀。

人物写入：
- 只有精确身份无匹配且没有姓名候选时才自动建档；有候选先让用户选择已有者或确认新人。
- 只有名字且无匹配时，必须让用户确认后再传 confirm_create=true。
- 不记录临时状态、猜测、凭据或用户明确要求不要记住的内容。
- 删除档案只有用户明确要求时才调用 relationship_forget。

关系：
- a -> b 表示 a 是 b 的 xxx。
- 双向关系必须同时给出正向和反向称谓；单向只给正向。
- 关系失效只有用户明确要求时才调用 relationship_relate 的 relation_id 形式。
```

不修改稳定 System Prompt 的其他层，不注册动态 Prompt 贡献。

## 7. 依赖：rela-mem 搜索候选别名

`relationship_find` 需要展示候选别名。当前 `GET /v1/persons?query=` 只返回 Person 属性。需要小改 rela-mem：

- 每个搜索结果附带最多 10 个 `aliases`；
- 字段：`alias_id`、`namespace`、`value`、`display_value`；
- 不改变分页、过滤和权限；
- 不返回 `normalized_value`/`identity_key` 等内部字段；
- 增加集成测试：同名候选返回自己的别名，分页仍按 `person_id`。

该改动属于 rela-mem 应用，实施顺序放在插件之前。

## 8. 测试策略

| 层 | 内容 |
|---|---|
| Adapter 契约 | MockTransport 锁定路径、query/body、认证头、错误映射 |
| Tool 单元 | 前缀解析、候选歧义、确认创建、档案写入/删除、关系方向推断 |
| Factory | 严格配置、Token 规则、启动不请求服务 |
| Prompt | 静态 section 注册与关键规则 |
| Plugin | start/stop 关闭 client，无事件订阅 |
| 集成 | 假 rela-mem HTTP 服务跑完整 Tool 调用；可选真实 rela-mem smoke |

关键回归：

- 只有名字无匹配时，`confirm_create=false` 不创建；确认后创建一次；
- 精确身份无匹配且无姓名候选时自动创建；有候选先确认；
- 多个同名候选返回 `confirmation_required`，不写数据；
- `relationship_remember` 不写 `source=user`；
- `relationship_relate` 有 `reverse_detail` 自动 two_way，无则 one_way；
- `relationship_forget` 只追加墓碑，不删人物；
- 服务离线时 Tool 失败但 Session 继续；
- 不订阅 `UserInputEvent`，不注册 Region，不参与启动门闩。

## 9. 非目标

- 自动召回或每轮预查询；
- Argus 直接接入；
- 圈子成员写操作；
- 人物合并、改名、归档；
- 关系历史；
- 档案历史；
- 跨服务事务；
- 新的共享 package；
- 修改 ReActAgent 或 Plugin Runtime。

## 10. 风险

| 风险 | 影响 | 处理 |
|---|---|---|
| 工具数量增加 | 模型选择变难 | 只加 5 个，描述明确“何时不用” |
| 隐式参数规则 | 模型误用 `confirm_create` | 工具描述和静态 Prompt 双重约束 |
| 自动建档 | 产生重复人物 | 仅精确身份自动创建；名字必须确认 |
| 服务离线 | 工具失败 | 不设 required plugin，不阻塞 Session |
| 上下文过大 | Prompt 膨胀 | 固定上限 + `truncated` |
| Memory/Relationship 混用 | 事件写进档案 | 静态 Prompt 规则和工具描述 |
| 跨服务写无事务 | 部分成功 | 每个 Tool 独立失败；不宣称原子 |
| 搜索结果缺别名 | 重名难选 | rela-mem 搜索返回有限 aliases |

## 11. 验收

1. Agent 能按 `"张三"` 找到候选，并在多个候选时询问用户。
2. Agent 能按 `"qq:123456"` 精确解析人物。
3. Agent 能为已有人物写档案，并返回写入的 key。
4. 只有名字且无匹配时，Agent 不自动创建；用户确认后创建一次。
5. 精确身份无匹配且无姓名候选时自动创建；有候选时先确认。
6. Agent 能读取人物档案、圈子和关系。
7. Agent 能写双向关系和单向关系，方向/称谓正确。
8. Agent 能失效关系、删除档案。
9. 服务离线时 Session 仍可运行，其他工具不受影响。
10. 没有任何自动召回、Blackboard Region 或启动门闩。

## 12. 已确认决策

1. 纯 Tool 插件，不做每轮自动查询。
2. 5 个工具：find/context/remember/forget/relate。
3. 参数尽量少，来源、时间、方向、默认值、组合查询由代码处理。
4. 精确身份前缀合并进 `relationship_find`。
5. 精确身份无匹配且无姓名候选可自动建档；只有名字无匹配或新身份遇已有姓名候选需用户确认。
6. 删除档案使用独立 `relationship_forget`。
7. 歧义返回 `success=True` + `confirmation_required`。
8. `source_ref` 由代码生成，不暴露给模型。
9. rela-mem 搜索返回有限 aliases。
10. 不加入 required_plugin_ids，不自动召回，不注册 Region。
