# Agent 的 Argus 后台执行架构

## 状态与验证范围

2026-10-22 实现；[design](design.md) 为设计约定，[plan](plan.md) 保留实施步骤，尚未正式部署。
当前已实现下面组件，不再把旧mock基线当本阶段结果。独立审阅发现7个Important，已逐项
RED→GREEN修复；后续最小MCP接入和静态工具说明也已落地。提交前全套Agent1152/Argus171/
Gateway43通过，类型检查270源文件通过，wheel安装/15manifest验证通过。用户本机真实QQ回复
与Jev判断已由持久记录核对，不等于正式部署或全面生产验收。

## 持久身份与公开隔离

`application/repositories/session_entities.py` 的 sessions 增加 scope_key/properties，SessionStore
启动显式检查/ADD COLUMN；部分唯一 scope 索引与 JSON wake_id 唯一索引支持排他和持久幂等。
reserve_scoped 在 BEGIN IMMEDIATE 中检查已有指纹、scope owner 和总后台名额，未满才创建会话。
同 scope 跨 workspace/boot 唯一，默认总额8；stopped/pending_sync 未释放仍计数。

`icarus/core/session.py` 的 SessionContext 是深冻结通用值。SessionApi 的公开 get/submit/history/
steer/cancel/unload/delete/title/discard 等路径拒绝后台，SQL/内存列表均隐藏；同步 task status 用
加载的不可变 context 过滤，公开 subscribe guard 用 workspace_key/session_id 查持久 kind，未知拒绝。
内部预留任务使用可信专用入口，不给公共 submit 新增 task_id 或 trusted 参数。

SessionRegistry从存储加载后台context；按2026-10-22用户简化决议，SessionRuntime保留配置的
mcpServers，后台与interactive共用既有MCP发现/执行机制，不新增动作白名单、群范围校验或发送
记账层。scope只限定Argus读/报告与执行占用，不限制通用MCP目标。Argus工厂在非后台拒绝激活，
沿用optional factory disable机制；后台将插件和prompt contributor标required，Runtime不识别业务Event。

## 启动与执行控制

ScopedExecutionController 持有 reserve→bind→bound→launching→原输入队列的门控和持久记录。
重复 wake 同指纹返回同身份；调用方取消等待不取消已开始的交接。launching 后不凭异常重入队；
提交可能已接受时保持 execution_unknown，不伪造 start_failed 停止。默认总期限900秒，计入等待。

应用 ArgusHandoffClient 通过 HTTP bind/finalize/report 重试，安全错误与 header Bearer 不泄漏秘密。
application/models/scoped_execution.py 拥有 ExecutionView，argus_protocol.py 为应用内纯 HTTP/
wake 边界，插件不反向 import controllers/repository。Gateway 只调用公开 scoped facade。

## 完整停止与工具生命周期

SessionRuntime.drain_execution shield 共享等待任务，先 PluginManager 全图 drain 输入/上下文/
inbox/run，再等会话 ToolExecutionTracker。后者在 core/tools 中只跟踪真实 future：同步线程 wrapper
单独登记并 shield，异步 grace 超时仍保留未完成任务；snapshot 共享 tracker。原取消调用仍可快速
返回，不代表工具已停。UpdatePipeline.flush 等应用记录完成后，控制器才写 execution_stopped。
宿主额外检查entry.persistence_failed与对应task已持久task.finished，checkpoint改写的failed是权威
退出原因；queue.join或SessionRuntime原始内存终态本身不够证明持久停止。

同 data_dir 只支持单宿主，重启将旧进程内执行标 interrupted，恢复的是记账而非模型。不存在内存
task 本身不是运行中停止证据；外部服务副作用/显式后台进程不被声称因取消已回滚。

## 普通 Argus 插件和记账

MCP插件另在初始化的System session层列出已配置服务器名，并提供可信配置
`runtime.plugin_config.mcp.server_instructions`的对应固定说明。此步骤不连接服务器，不把远端
动态工具目录、消息原文或进度注入System。普通/后台共用，后续User Prompt不重复；当前本机
SnowLuma说明直接给出get_action(name)、search_actions(query)、invoke_action(action,params)，
不是在通用代码里识别具体QQ协议。

`plugins/argus/` 是一级插件：初始化 static_prompt_sections 给 System session 层一份固定说明，
同 task 动态贡献仅给 User Prompt 引用/摘录/scope/游标。先组装并 /deliver 实际初始 ID，再发布
required contribution；失败经原prompt失败通路收束，模型不启动。固定说明允许模型使用配置的MCP
工具（包括QQ）；最终assistant正文不自动发平台，发送须经真实工具调用，报告仍与发送结果分离。

四工具 read/history/wait/report 使用固定绑定和现有 RuntimeLoopRunner，同步/异步共享后端。
ToolExecutor 按签名注入可信 tool_call_id，不放入模型 schema。backend 仅暂存工具页，插件消费
AgentToolCompletedEvent，完整最终预算后 output 与原页一致才确认 exposed/游标；截断或预览
不确认，允许重复读取而不跳项。读 incidental 不等于认领；report 仅本 task 已 exposed work。

报告先追加 conversation_updates 的 argus_report，再按顺序发送；成功/确定拒绝追加对应内部回执，
未知前项先重试，不越过它发送新报告。相同成功报告复用回执，避免重复记账。raw 内部日志和公共
RuntimeUpdate 投影分离，保留底层序号连续性检查，不把内部行展示到普通历史/UI。

控制器收束后同步outbox、finalize、事务清scope/释放名额，再可信卸载后台runtime；卸载失败不
重新占槽或重跑模型。绑定瞬时失败由宿主原身份重试，加载窗口的取消在会话登记后补送。
同步失败保留pending_sync并重试，
不重跑模型。已接受/拒绝水位从持久回执恢复；释放记录按 wake 可查，按 scope 只查当前 owner。

## 已观察的验证

- 原Agent基线1093项通过；提交前全套1152项通过，包含静态工具说明及System跨task稳定性测试。
- typecheck270源文件通过；wheel安装验证与15个manifest惰性发现通过。
- Argus拥有的真实TCP集成启动Gateway子进程、真实AgentRuntime/插件/provider adapter，模型为
  本地HTTP fixture；已验证read→wait→report，以及B仅读留new/顺手报告结清两条分支。
- 针对thread/async取消、outbox拒绝、RPC等待取消、旧库迁移、公开guard、后台继承MCP等有聚焦测试；
  生产数据库和网络ACL未执行部署验证。后续本机真实模型/QQ测试已确认发送与封口，但暴露出
  长结果读取绕路、部分initial漏报、语义介入尺度和图片输入边界；问题另行记录，未伪装为已修复。
