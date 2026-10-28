# 提示词插件｜当前实现

> 2026-10-17：本文按当前源码与测试编写。设计见 [plan.md](plan.md)，验证见 [plan-validation.md](plan-validation.md)。

## 职责与结构

`agent_app.plugins.prompt` 是第12个内置插件，tier=base，默认 required，无工具和持久化 state。
`agent_app.contracts.prompt` 声明静态贡献 Protocol、PromptSection、两类业务 Event 与 prompt.read 事件能力，
通过 manifest contracts 扩展目录加载；core/runtime 不导入这些应用类型。

- base_prompt.py：Python常量 DEFAULT_SYSTEM_PROMPT，保留通用身份/授权/事实/验证/表达约束；插件专属记忆等指导已下放至各插件prompts.py。
- sources.py：仅根目录 AGENTS.md→CLAUDE.md 回退，有界 UTF-8 读取，拒绝越界路径、非普通文件。
- composer.py：稳定排序和渲染，复用 conservative_token_count，optional整节丢弃，required/base/external不可丢、不截断。
- plugin.py/state.py：内存静态快照、任务贡献聚合与受管deadline，查询/更新handler。
- factory.py/config.py：设施与用户配置分离；总预算12288、单节8192、timeout5秒、required_contributors列表。

## 装配与运行

应用 SessionRuntime 的 before_ready 在所有插件 start/restore/on_session_start 与失败级联后，
从有效 PromptContributor 采样静态 sections；从 manifest 发现 input.user_input→prompt.contribution 的生产者，
以及 prompt.update 生产者；传入复制后的静态数据和允许来源，不把 sibling 对象交给 Prompt。

静态按 base/workspace/session/persona/external 渲染；动态按 mode/contributor/runtime，同层来源+section_id排序。
六段稳定业务指导属于 session 静态层，只在初始化/恢复时进入 System，不进入每任务 User Prompt。
Prompt 消费 UserInput 是为真正动态来源保留的路径；没有动态来源时仍会发一次空 completed，保证 Blackboard readiness 推进。
缺失来源在input时间+timeout后完成；接收时记录绝对cutoff，入队与提前缓存贡献都检查接收时间/事件时间，不能靠零延时timer竞态延长截止。optional跳过，required使task失败。贡献先到可缓存；重复/迟到准备结果不转成run-time更新。
等待放在runtime托管后台任务，prompt.read不被普通Event等待阻塞。

Blackboard required_context_sources包含prompt（必须等待），fatal_context_sources也包含prompt（失败终止）。
上下文ready前查询prompt.read；普通task复用system，compaction成功后同task await refresh再发布context.ready。
刷新失败本task不启动Agent，后续task重试。动态块只有一个、publisher=prompt，保留原Blackboard serializer/readiness。

prompt.update无task用于暂存persona；refresh才改变system。有task时必须已成功准备且TaskChannel为RUNNING，才发TaskContextInputEvent。
以应用解析的task.control提供者身份及request_event_id/task_id/run_id配对结果；accepted后才确认预算消耗。
准备期JSON块与运行期上下文分开计费，运行期复用TaskChannel的format_runtime_context，按每条独立batch保守预留包裹开销，
不把多次运行提醒误算成单个准备块；拒绝确认释放预留。InputFinished/stop清理任务。

## 创建时私有指令

Gateway `session.create.instructions` 只在创建时接受；Gateway与Agent应用入口共用实际配置预算校验，超限拒绝。
SessionStore同事务创建session和现表conversation_updates的sequence=0私有行，update_type=session.instructions。
该行不构造公共RuntimeUpdate，不改变last_sequence/public activity；公共读与完整性统计只看正数。

SessionRegistry在每次single-flight load内调用get_session_instructions，原文注入Prompt配置；坏记录不能降级为空。
lazy create、unload/reload、进程重启均使用同一私有持久化记录。用户历史/通知/状态/标题不展示该记录。
这不是加密或保证模型绝不复述system；协议侧不自动泄漏正文。

## 其他插件接入

静态：实现 `static_prompt_sections() -> tuple[PromptSection,...]`；当前允许 `session` 与 `persona` 层。`session` 用于稳定的业务/工具使用指导，`persona` 用于身份与表达；应用在初始化结束后自动采样，只构建一次并放入 System Prompt。

动态：只用于真正随任务变化的内容。manifest声明消费input.user_input、发布prompt.contribution，handler返回或publish
PromptContributionEvent(task_id=event.task_id,status="completed",sections=(...))，失败也发终态。
若需运行中更新，另外声明发布prompt.update；无task为待生效persona静态，有task为当前动态提醒。
生产者不直接调用PromptPlugin、不import其实现；只依赖应用契约。required来源由Prompt接收方配置。

真实外部插件接入示例位于 `apps/gateway/test/test_prompt_e2e.py` 的 write_contributor：包括manifest、
独立工厂和handler；测试通过真实插件发现、总线、模型HTTP请求和Gateway重启证明可用。

最小生产者形态（位于自己的插件目录，不作为Prompt的子插件）：

```python
from agent_app.contracts.prompt import PromptContributionEvent, PromptSection
from icarus.core.contracts.input import UserInputEvent
from icarus.core.plugin import BasePlugin, handles

class ExamplePlugin(BasePlugin):
    def static_prompt_sections(self):
        return (PromptSection("guidance", "session", "稳定的使用指导"),)

    def static_persona_sections(self):
        return (PromptSection("persona", "persona", "人格描述"),)

    @handles(UserInputEvent)
    async def on_input(self, source_plugin_id, event):
        return (PromptContributionEvent(
            task_id=event.task_id,
            status="completed",
            sections=(PromptSection("task-guidance", "mode", "当前任务要求"),),
        ),)
```

稳定规则放 `session`，人格放 `persona`，真正随任务变化的内容才放 `mode/contributor/runtime`。
其manifest沿用schema2；只有当插件发布 prompt Event 时才需要 `contracts: agent_app.contracts.prompt`、
`consumed_events: ["input.user_input"]`、`published_events: ["prompt.contribution"]`。只做静态 session/persona 的插件
不需要声明 prompt Event，应用通过 `PromptContributor` Protocol 直接采样。

静态Protocol不是运行期查询接口，不能从另一个handler直接调用兄弟插件来拼提示词；动态来源必须发终态，
即使无内容也发completed+空sections。超时默认optional省略，required失败。

## 已接入的内置贡献者

六个业务插件通过 `static_prompt_sections()` 提供 `session` 层稳定指导，使用自己的prompts.py维护内容：

| 插件 | 当前贡献 | 不改变的业务路径 |
|---|---|---|
| memory | 长期记忆使用/自然表达/维护边界 | 召回正文仍走TaskContextInput+Region+ACK，不在Prompt再注入一份 |
| skill | 发现/先读SKILL.md规则、当前写入开关说明、最多8项轻量目录 | 生成/演化内部Agent及Job通知不变；不自动执行Skill |
| process | bash与后台任务分工、start不等于ready、跟进logs | 不自动启动/查询进程，不回灌状态事件 |
| knowledge | 聚焦查询、读原文、真实引用、写入边界 | 不做自动RAG、不查询/上传后端 |
| mcp | 先搜索/列目录、真实tool_ref/schema、配置不等于连接成功 | 无服务器时为空；指导生成不联网/不启动bridge |
| builtin-tools | 文件先读、分页、覆盖/行前插入、命令退出码 | ToolDefinition和参数约束不变 |

Skill目录单项JSON>256字符整项省略，总JSON<=1200字符，最多8项；元数据明确视为不可信数据，
正文按需read。现有scanner只读取最多16KiB YAML frontmatter，不读取后续正文。该目录在session初始化/
恢复时采样一次，不在每任务重扫；新session或运行时重启后自然更新。

基础System Prompt从1110降为472字符/保守tokens（3208→1374 UTF-8 bytes，约减57.5%）；六个session指导
约1339 tokens。它们现在只以一份进入System，不再每任务进入User Prompt并累积到历史。

## 限制

不做嵌套/workspace import/文件监听、KV失效探测、运行中替换外部指令、persona产品实现。
Hook既有参数改写权限未变，前缀稳定保证限于Prompt/Blackboard输出；不承诺服务商cache hit。
回退旧版本时必须保留sequence=0兼容reader，不能直接让旧完整性统计读新数据。
