# Tool Call Quality Architecture｜工具调用质量架构

> 状态：六组代码及全部审查发现已修复；提交前重验1274项测试及typecheck/package/compile/diff通过。详情见[plan.md](plan.md)。未执行真实模型smoke；按用户授权提交并准备推送/PR，未合并。
> [design.md](design.md)记录目标与决策；本文按当前源码/功能测试描述实际实现，不把分发请求当执行退出证据。

## 1. 所有权与依赖

| 位置（`apps/agent/` 下） | 当前职责 |
|---|---|
| `src/icarus/core/tools/settings.py` / `execution_policy.py` | 默认、绝对上限、窗口与显式申请的有效预算 |
| `src/icarus/core/tools/tool_executor.py` | 执行/分配预算、预览前保存、最终预算断言、按可信搜索实例收紧加载metadata |
| `src/icarus/core/tools/result_budget.py` | 合法JSON预览、失败诊断优先级、明确省略标记、当前mapping恢复标量保留 |
| `src/icarus/core/tools/tool_search.py` | 精确scope/name发现、结构化定义、无状态完整性过滤 |
| `src/icarus/core/tools/tool_execute.py` | 既有门控/Schema校验/单次解包，加内部dispatch请求记录 |
| `src/icarus/core/agents/types.py` | 完成事件的安全目标访问属性；不更换原ToolCall |
| `src/agent_app/plugins/mcp/` | MCP内容转换、现有脱敏、有限诊断、最终错误包装和Schema缺省 |
| `src/agent_app/plugins/builtin_tools/` | 字符分片Read、普通Bash非零空stderr诊断、工具使用提示 |

没有新增Plugin、DTO、依赖、后台执行追踪或数据库迁移。core不反向依赖插件；MCP Catalog保持独立，Agent循环仍只持有Run状态。

## 2. 预算与结果处理顺序

当前类型及仓库/打包默认为default=25000、max=25000、batch=100000；max/batch也限制配置绝对上限。min512、max_calls8、timeout120/600s、结果文件16MiB、preview90%和head50%保持原值。显式低预算保留，旧完整4000/16000配置可用；只写低max却省略default的配置按既有范围校验报错，不静默重写。

有效单次仍取请求/默认、max和窗口8%的较小值，组预算受窗口15%限制。128000窗口默认10240/19200，20000窗口1600/3000；25k并不是所有窗口下的实际结果量。配置来源仍择一，不把仓库settings与打包资源自动合并。

```text
Tool执行与结果Hook
 → 按组分配实际预算
 → 超预算 或 >1000token失败：尝试保存预算前结果
 → render_tool_result（成功一般预览；完整保存的失败优先短预览）
 → 最终预算断言
 → 搜索定义完整性/来源过滤（只改metadata）
 → Agent收集加载名单并生成Tool Result历史
```

只有文件complete=True才主动将大失败缩至min(1000,实际预算)。预览保最终诊断、exit_code和stdout；只有完全重复的stderr去重。共享省略标记指向完整/不完整文件，图片保持原样；图片信封放不进1000则回实际预算，仍装不下由执行器明确拒绝超预算结果。

无store、无task_id、写失败、文件超字节限或原捕获不完整，不强制短预览、不谎称完整保存；保留实际预算与真实metadata。文件保存的是转换/Hook后的预算前结果，不含内部metadata，不是未脱敏远端原报文。小失败不额外保存。

## 3. 分页恢复与Read/Bash

省略标记mapping/list含`truncated:true`、文本含`truncated`，保留省略key避让业务字段。mapping当前层对有限恢复标量优先保留完整值：offset/limit/has_more/next_offset/page/page_size/total/next_page/text_offset/max_chars/truncated/next_text_offset/next_cursor，剩余预算用于正文。不按具体插件身份解析业务、不递归搜寻协议。

信封本身超预算时整体退成省略标记，不能裁出半截cursor；失败短预览也复用这条原子恢复策略，与字段插入顺序无关。通用预览不指导原调用重放：工具可能已产生副作用，裁剪或失败不代表回滚。

Read保留UTF-8、offset/limit行页，在当前行页加text_offset（默认0，非负整数）/max_chars（默认2000，1–20000），拒绝bool。返回原行字段加字符参数、truncated、next_text_offset。先读完字符片段再按next_offset翻行页；has_more=false仍可能字符truncated=true。等于页长返回空片段、超过报错。

字符按Python Unicode码点计，不是UTF-8字节或字素簇。仍先读行页再切片，不保证超长单行内存有界；文件续读期间被修改无快照一致性。预算裁剪保留的游标不证明正文已展示，Read说明要求原offset/text_offset缩小max_chars重读。

Bash仅普通非零退出且stderr空白时补`exit_code=<code>, no stderr`。超时、取消、spawn失败、采集超限等原诊断/metadata优先，进程组清理不变，不新增后台会话能力。

## 4. MCP表示与诊断

Converter只把完整object/list文本转`{type:"text",data:...}`，不提升为structured_content；与原structured JSON语义相同才去重，先比较后脱敏，布尔不与数值混同，数组顺序有效。标量/补充文本/资源/图片保留，字符串内JSON仍保持字符串并接受既有Redactor处理。

错误文本在转换/去重前收集并脱敏，因此JSON错误不丢顶层原因。success只依据is_error，不解释retcode/code/receipt。无文本的structured错误保留证据且允许通用error。

Manager在调用前校验，诊断最多8条、展开4层、每条240字符，目标标识128字符；描述required/类型/enum约束类别和anyOf/oneOf分支，不回显实际值/enum值，未知动态键用占位，抑制异常上下文。Plugin最终operation也先脱敏/限长，实际执行标识和参数不变。诊断异常正文在最终Plugin出口另限2200字符；manager缺失目标错误也先将tool_ref脱敏/限128，避免外层安全operation后又拼回完整引用。该限制针对诊断输出，不声称整个Schema验证计算资源有界。

list的Schema缺省等于实例default_page_size；search Schema与实际缺省均min(5,max_page_size)。保持外部ref禁止、取消透传、失败不自动重放及既有重连。没有为MCP增加Run发现授权。

## 5. 分发身份与公共事件

目标执行器返回后，tool_execute将`dispatched_tool={id,name,arguments}`合入结果metadata，保留其余结果字段；arguments深拷贝去顶层_execution。前置门控/Schema失败不记录，执行器直接抛异常也不伪造完成记录。

这是**进入执行器的分发请求**：不证明业务工具实际调用、成功、退出或副作用；参数不冒充before Hook改写后的实参。完成事件target_tool_call仅在外层为tool_execute、记录类型合法、name/id非空、id匹配、arguments为dict时解包，深拷贝返回；否则回退原调用。

原ToolCall、调用ID、消息历史和公共started/completed仍保留外层身份。metadata不经as_dict进入模型消息；runtime-update现有白名单投影不新增内部参数。Hook仍单次观察目标，不重复入口。

目标访问属性在读取分发记录前先检查外层metadata为Mapping；手工构造metadata=None/list/string时也回退原ToolCall，不抛异常、不修改原metadata。普通非分发调用直接返回原ToolCall。

## 6. 发现与加载完整性

ToolSearch在Run创建前已有固定query/name Schema，scope按精确plugin_id，name可选精确筛选；不传name仍整组。命中返回scope、tools数组（name/description/input_schema）与usage；定义不含_execution。未命中/空query返回目录，不增加授权。

搜索metadata先给候选tools_loaded。ToolExecutor最后一次渲染后，只对注册的ToolSearchTool调用无状态finalize_loaded_tools：候选对应的最终可见条目与Catalog完整定义序列化一致才保留，严格区分JSON类型；完整条目可部分加载，残缺条目不加载。非搜索及分发目标的tools_loaded被移除，其余metadata保留。Agent继续与可执行全集求交、保序去重，跨Run重置，不改循环中的定义数组。

单项极大定义可无法加载；精确name或合理增加预算可恢复，读结果文件本身不授权。保证终点是最终Tool Result/Agent历史，不涵盖下游LLM before Hook主动改写、网络送达或模型真实接收。不收紧LLM Hook权限；MCP仍按自身Catalog验证/执行。

## 7. 验证与已知边界

功能测试覆盖预算入口、MCP JSON/错误包装、字符Read、Bash、失败保存/降级/图片、分发属性、伪造授权、裁剪后的精确恢复与四条Agent路径。真实Session web-search测试已适配结构化搜索结果，不更改web业务实现。

提交前重验Agent全量1274 passed（74.29s）；typecheck273源码文件、wheel资源/安装/发现、compile/diff通过。审查两项Important与原延后Minor均有回归覆盖，记录见plan；真实模型smoke未执行，无凭据或外部服务数据用于验证。未改Gateway/TUI协议，因此本轮未执行其套件。
