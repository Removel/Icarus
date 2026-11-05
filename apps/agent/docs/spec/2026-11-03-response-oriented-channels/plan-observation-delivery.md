# ArgusPlugin 观察交付恢复计划

> 实施用`superpowers:executing-plans`，先RED再GREEN。不改ReActAgent，不重启服务。本轮是用户要求的“先保存当前代码，再文档，再开发”。

**Goal:** 让ArgusPlugin负责当前窗口正文和历史的动态贡献，Bridge只建立任务/绑定并提交简短主题；不再将完整观察包作为直接用户请求。

**Architecture:** 复用PromptContributionEvent、BlackboardContextReadyEvent及普通输入链；稳定系统引导不变，动态观察位于插件上下文，最终仍由Blackboard合入User Prompt。确认权限来自最终实际模型视图，不来自简短触发prompt。

**Tech Stack:** 现有dataclass/Pydantic、Event、SessionStore、pytest，无新依赖。

**Spec:** [Argus窗口设计与计划](../../../../argus/docs/spec/2026-11-03-response-oriented-channels/plan-message-windows.md)、feat/argus ArgusPlugin动态贡献。原有关联/接收机制见[plan-design.md](plan-design.md)。

## Global Constraints

- 只有稳定原则进入System Prompt；完整当前窗口不是可选附带材料，不按是否值得单独回应删句。
- 确认范围仍是冻结items，窗口正文与历史不能被工具误确认；全部原文保留身份/顺序。
- 数据准备失败时不运行空任务；不直接操纵兄弟插件，使用事件和应用注入设施。
- 保持普通权限、同步/异步一致、持久别名、回执对账及旧绑定原prompt。
- 不新增运行中领取/等待工具、notify端点或持续轮询；第二阶段单列。

## Review Focus

1. 只有短启动prompt可见也被误判为正文已送达：Task2。
2. contribution超时/失败可绕过必需来源：Task2。
3. 历史/当前非工作消息被commit确认：Task3。
4. 旧绑定重试被新渲染改变指纹：Task1/2。
5. 群聊目标与当前消息/引用关系丢失：Task1/3。

## Task1：区分任务启动和观察载荷

**Files:** `apps/agent/src/agent_app/argus_protocol.py`、`application/services/argus_bridge.py`、`plugins/argus/presentation.py`。

**Interfaces:** WakeRequest新增默认空`window_items: list[Observation]`/`history_items: list[Observation]`，全体受现有512KiB总预算约束；items仍为冻结工作。新绑定`view_version=3`、`prompt`为简短主体任务与业务主题；`observation_prompt`为插件将贡献的完整语义视图。semantic_text继续干净主题，不要求Memory认识Argus。

模型视图messages是完整当前窗口（包括work成员）；work_refs单独列本轮需要确认的引用；history单独列以前的对话。不是把某条解释句作为可以不交付的附件。当前窗口默认由items补足，不能在window_items中缺工作项、重复或跨未授权来源。

- [ ] 写DTO边界、缺窗口兼容、完整当前窗口、历史单列、目标保持测试，先RED。
- [ ] Bridge分配所有显示消息的稳定别名，保存原始请求与两个视图，不在安全重试时重新渲染。
- [ ] 简短启动prompt必须有语义主题，非空，但不再重复完整观察JSON；旧view版本仍走旧路。

## Task2：接回ArgusPlugin动态贡献

**Files:** `plugins/argus/plugin.py`、`manifest.json`；`application/controllers/session_api.py`的受保护会话配置；必要的`plugins/argus/config.py`；新增`test_prompt_delivery.py`。

**Interfaces:** `on_input(UserInputEvent)`按task_id读取注入Bridge中的精确binding，发布`PromptContributionEvent`的contributor层观察section；`on_context_ready(BlackboardContextReadyEvent)`验证最终输入正文，写task-scoped可见证明；`on_finished(InputFinishedEvent)`清理任务内证明。只接受真实输入/Blackboard来源，不接受伪造其他事件。

Argus会话在应用配置中将argus加入prompt.required_contributors，正常会话不变。manifest声明消费input/context_ready/finished、发布prompt.contribution；不创建子插件，不在通用Runtime按Argus分支。

- [ ] 实际provider输入测试证明：完整正文在plugin_context，user_request仅简短任务；稳定系统不含正文。
- [ ] 缺绑定/贡献失败/超时/正文被截断都不能开始模型或确认未见内容；正常上下文和Memory照常准备。
- [ ] 消息可见证明只记在该task及原binding；切换Session/任务不能继承旧证明。贡献事件本身不等于实际可见。

## Task3：工具与确认检查迁移

**Files:** `plugins/argus/tools.py`、`guide.py`、`prompts.py`；扩展presentation/commit/cancel/reference测试。

- [ ] v3不再用binding短prompt命中判delivered；根据该任务observation_prompt在最终模型消息中的完整出现检查，代码自动发送agent.delivered，只包含items。
- [ ] resolve仍只允许冻结工作项，window非工作正文/history只能作为语义材料；已展示不等于工作已处理。
- [ ] 指南说明先理解完整窗口，再决定是否回应；不用每个气泡各发一条，也不强制所有消息回复。
- [ ] 保留reply_target、scope授权、未知发送不重试和原子commit。旧版本工具和取消测试不回退。

## Task4：联合验证与更新记录

**Files:** `apps/gateway/test/test_argus_tools_e2e.py`、`test_argus_semantic_context_e2e.py`、`test_argus_mcp_reply_e2e.py`由Gateway计划拥有。

- [ ] 原文6条一次进入实际模型视图，Memory query保持业务主题；只有一次平台发送再commit。
- [ ] 第二窗口可复用Session，但不是第一窗口新增观察直接改本轮冻结范围。
- [ ] 旧轮次/重启/丢回执/取消保持；不通过删真实Session或变更生产轮次避测试。
- [ ] 最小插件→`make test-agent`→`make test-gateway`→`make typecheck-agent`→compile/diff；真实模型与服务加载另授权。

## 第二阶段边界

运行中新观察由available/read/wait和受控领取后续不可变批次处理，不能偷偷扩当前items。可以复用现有PromptUpdateEvent通知，但该阶段需要持久批次与终态停止语义；本次不实现，也不再引入旧任务永久查询。
