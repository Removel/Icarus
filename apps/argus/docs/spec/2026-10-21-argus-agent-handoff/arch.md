# Argus 真实 Agent 交接架构

## 状态

2026-10-22 实现，尚未正式部署；[design](design.md) 规定设计，[plan](plan.md) 为实施步骤。
独立审阅问题已修复，提交前Argus171项通过，含真实三应用TCP/local模型两分支，以及Jev
instructions请求兼容测试。后续本机真实模型/QQ链路和Jev判断已由持久记录核对。

## 事实、迁移与水位

保留 QQ 验签/规范化、revision、规则/Jev、去抖和预算。Database 采用 schema2；schema advisory
锁内显式执行 v1→v2 迁移，wake_requests 增 handoff JSONB，observed_items 增 exposed_wake_id，
旧记录mode=mock且status/outcome不变，不新增业务表/seq。空库DDL与迁移添加相同列/索引。

ObservationRepository 在 scope锁→message锁 后分配 observed_at=max(clock_now,scope_max+1us)，
不信 parser 锁前时间或随机UUID单调；sent_at仍平台时间。duplicate不推进水位。handoff写入和
普通判断/提交路径采用scope先行锁，减少版本变动/报告的竞争。跨scope不全局串行。

## 控制面与调度

execution.mode 默认mock，gateway模式显式要求两个独立环境token；真实GatewayAgentClient通过
/argus/rpc调用wake/status/cancel/release，wire不含workspace。WakePayload保持应用内部mock兼容
形状，schemas/wake.py在边界转换固定v1 JSON并计算同三应用约定的指纹，不import另一应用源码。

RealWakeService独立于原WakeService模拟结果校验，每次scheduler推进仅有限控制RPC。真实活跃
查询跨boot且不只看status；resolved后仍可能占用，直到远端release_complete。scope/capacity_busy
保留原wake退避，未知不假失败/解锁，旧boot未知不自动重发成新业务。原60秒只用于mock。

## 数据面、角色与报告

HandoffRepository在原两表持久binding、initial水位、grant最近指针、接受/拒绝report_log和封口。
bind核对固定scope/origin_boot/冻结指纹；初始集合从冻结payload派生，不由工具扩张。role以
binding.origin_boot判代际：旧binding可完成原boot合法工作，新boot消息只context；编辑/撤回
单独版本，不被旧报告隐式完成。

HandoffService提供bind/deliver/read/history/wait/report/finalize：read按Unicode字符分块，页
按方向scope水位分页且有文本/扫描预算；wait先查库→登记通知→再查，取消清waiter，DB为事实源。
敏感/权限ObservationAccessPolicy对完整正文和输出文本元数据先检查再截断，ignored历史不能绕过；
JudgementService复用检测。仅实际返回安全引用登记exposed，不把incidental写wake归属或改new。

report整批事务复查送达/版本/角色/策略；成功才原子关联incidental并processed，部分成功不终止
run。确定拒绝持久回执但不增加accepted cursor；相同report同规范内容幂等，异内容冲突。finalize
仅未报initial标failed/status_reason=unresolved，仅读incidental仍new/无owner，已报部分不回滚。
所有真实封口含零报告都resolved，业务逐项结果和exit_kind另存，不声称全部已解决。

## 路由、资源所有权和部署

本机app保持公开GET/QQ上报，gateway模式挂内部Bearer路由；create_service_app只挂内部prefix，
不含原文GET或OneBot。main可显式同进程双uvicorn listener，共享一次owner lifespan/boot/services/
scheduler；普通host仍loopback，服务host只可信LAN/VPN，必须ACL，缺token拒绝。不做TLS/公网。
内部请求严格schema、1MiB限制、binding/path交叉校验，安全错误不回显secret/原文。

## 验证与剩余边界

- mock基线141项通过；提交前全套171项通过，含真实隔离PG与真实三应用TCP/local provider
  集成。没有用mock固定resolved替代真实插件路径。
- 覆盖迁移保留、单调水位、partial/rejected/finalize、跨boot/context、长文尾部过滤、正文续读、
  通知/取消、scope/capacity busy及B仅读/显式报告两分支。
- 正式数据库迁移和LAN/VPN部署/ACL验证未执行。后台按用户简化决议直接继承Agent的MCP配置，
  可调用QQ工具；Argus自身仍不发平台消息。Jev运行开关为应用配置，当前调用显式带instructions。
- 不清库、不自动降schema、不切mock冒充旧真实执行完成；未知状态保持安全占用，需可用状态/
  取消/记账通路收尾。旧boot待办不恢复不等于允许和旧执行并行。
