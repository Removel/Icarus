# QQ 观测闭环现状

> 历史架构记录：下文旧 connector/handler/service 和 SQLite 链路已被替换。
> 当前架构见 [第一阶段源码架构](../2026-10-21-argus-positioning/arch.md)。本目录 design/plan 保留历史目标。

## 接入

QQConnector负责HTTP验签、账号核对和体积限制，交QQHandler.handle解析；handler直接调用QQService.receive。service转换为entity/records.py表记录，调用DAO收存并固定接收顺序/首次强信号，成功提交后返回空204。来源相同事实一致则重复204，冲突409；已知非聊天事件忽略，错误消息422。

不使用入站队列。后台BatchScheduler/BatchRuntime保留批次收集、等待检查、ready队列和恢复；HTTP请求不等待模型。旧queue/worker实现及对应包装测试已移除。

## 判断与持久化

service使用通用图编译/运行能力：human判断→证据边→code/model判断→relation/action。图底座不依赖业务组件，策略与证据由service绑定，实体/表记录均在entity，DAO只做行查询和事务写入。

JevClient.ask负责typed响应、超时重试，不做QQ业务判断。IcarusClient.ensure_session封装session解析/创建，submit/steer给出类型化accepted/rejected/unknown/failed回执。service在发送前保存操作身份与准确参数，响应未知不自动重发，明确追加拒绝原子回waiting。

## 验证边界

合成OneBot消息、临时SQLite、可控时钟和假Jev/Gateway覆盖接收、去重、批次、判断、等待、恢复与投递安全。没有启动真实外部服务、读取密钥或操作真实库；submission_unknown仍需人工核实，不能用相似历史猜测成功。
