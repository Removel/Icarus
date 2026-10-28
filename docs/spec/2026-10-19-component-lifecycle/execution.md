# 统一组件生命周期｜实施与验收记录

## 当前状态

已完成实现、逐任务审查、整体审查修复与真实端到端验收。本文件记录实际证据及仍存在的外部基线限制。代码尚未提交，未创建分支或推送。

- 代码基线：`377f3b2`，当前分支 `feat/rela-mem`。
- 目标 remote：`origin`。尝试同步 `origin/feature` 时出现 TLS 握手错误；本地已知 `origin/feature` 是当前 HEAD 的祖先。远端最新状态未确认，没有自动 merge/rebase。
- 新增控制面、注册表、生命周期 CLI、infra 和应用连接覆盖已实现并完成逐任务审查；真实生产/容器/mixed 验收已执行。整体审查的两个安全边界已在同一轮修复，最终规范/安全/质量复审通过，无未关闭阻断项。

- 状态/配置入口：`./bin/icarus config validate` 返回 10 个组件，`config show` 输出来源和有效元数据，未创建实际本机覆盖文件或改写业务配置。
- 飞书：[统一组件生命周期技术方案](https://tcnyvm7vds2s.feishu.cn/wiki/H7FxwOWXkijkSjkOPkpcX7eQn7e)，原 `icarus技术方案资料库/infra` 节点，最终 revision 11 已回查。

## 变更前基线

| 检查 | 真实结果 |
| --- | --- |
| 原控制面 unittest | 29 passed |
| 仓库基建 pytest | 15 passed |
| Agent | 971 passed |
| Gateway | 34 passed，1 条既有 Starlette/anyio deprecation warning |
| mem0 离线契约 | 86 passed |
| openkb 离线契约 | 335 passed，2 条既有 warning |
| rela-mem 默认测试 | 19 passed、29 integration deselected；1 项因本机 Docker 不可用而失败，另有 1 条既有 deprecation warning |
| TUI | 252 passed、14 failed（13 个快照和 `test_conversation_windowed_wheel_loads_older_history`），1 条既有 warning |

rela-mem 的既有失败为 `test/scripts/test_management.py::test_compose_configuration_requires_secrets_and_has_isolated_ports`：命令 `docker compose ... config` 命中 WSL integration stub，未执行有效的 Compose 配置解析。这个环境问题不能写成实现回归，也不能将对应验证标记通过。

## 已完成模块

### 注册表

- 代码：`scripts/icarus/registry.py`。
- 测试：`scripts/tests/test_registry.py`。
- 功能：版本 1 TOML、机器覆盖层、临时组件覆盖、严格类型/字段/路径/引用校验，Secret 变量引用与可读的脱敏错误。
- RED/GREEN：记录了真实缺失行为断言失败，以及后续 credential alias、NUL 字符、JSON 响应体匹配的回归失败，再实现修复。
- 最终聚焦测试：167 passed。
- 独立审查：修复两个问题后，规范与质量均通过。

## 真实端到端验收前提

### 运行时与应用配置模块

- Compose 适配器延迟检测 Docker，远程组件不触发本地容器操作；进程适配器实际验证了临时 HTTP 服务的启动、就绪、PID 身份、日志、停止与回收。
- 运行时与注册表聚焦测试初次 198 passed，根脚本 329 passed；审查修复后进程聚焦 24 passed，规范与质量复审均通过。
- 进程停止会捕获并校验同组后代，即使 leader 先退出也会收束已确认的残留进程；身份不确定时保留记录并拒绝替换，不宽泛 kill。
- foreground 状态能从新控制器实例读取已登记的 PID；临时地址可经过身份校验恢复，供后续 status/stop 使用。
- Agent 配置目录 26 passed；rela-mem 配置测试 27 passed。覆盖字段只限连接信息，显式 environ 映射保持隔离。独立审查通过。

### 依赖、探测与注入模块的中间证据

三个模块已完成 RED/GREEN 与独立审查。初次 80 项聚焦测试、291 项根脚本测试通过；随后修复两处审查发现的真实用例：HTTP 响应持续少量发送字节时超过 socket timeout，以及 embedded 消费方依赖同 owner 的 bundled 中间件被误判为循环。修复后的 graph/probes 共 56 项通过，注入模块此前 31 项通过；复审规范与质量均通过。HTTP 总时限包含子进程启动，超时会回收 worker 与 pipe，不使用全局 signal 或泄漏线程。

最终验收将分开报告：

1. 实际 `bin/icarus` 命令、真实子进程/PID、HTTP/TCP 探测、配置覆盖和停止行为。
2. 实际 rela-mem 应用与真实 PostgreSQL/FalkorDB 的隔离数据验证。
3. Compose 配置解析与真实容器启停。

### 环境变化与真实端到端结果

执行开始时 Docker 不可用（WSL stub、Windows client 无 daemon）；之后宿主环境恢复了 `/usr/bin/docker`、Docker Server 29.8.0 和 Compose v5.5.1。没有修改宿主机设置。早先尝试的无 daemon Windows Compose 校验路径保留为证据，新版五份 Compose 定义都已实际解析成功。

**真实生产应用 CLI：已通过。** 使用临时 TOML、临时数据目录和临时端口，经 `bin/icarus` 启动真实 Gateway 与 WebUI：Gateway `/health`、WebUI `/health` 和首页 HTML、status、重复 start、日志、显式 stop 均通过。停止 WebUI 后 Gateway 仍可访问，随后单独停止 Gateway，端口释放。无模型调用。

**真实容器与数据库链路：已通过。** 使用唯一测试项目/网络/镜像、随机生成凭据和临时数据目录，经统一 CLI 拉起 PostgreSQL、FalkorDB、rela-mem。验证了：

- `/healthz` ready、业务接口无 Token 返回 401。
- 两个人物创建，档案写入/读取，关系写入/查询。
- 档案删除产生墓碑，历史保留；重启后墓碑与关系仍在。
- 重复 start 不重复实例；显式 stop rela-mem 后两个数据库仍可达。
- 以本地原生 rela-mem 连接同一组隔离数据库（注册为 remote）读取相同历史，停止应用及执行 remote stop 都不停止数据库。

这组 mixed-mode 验证模拟“已有外部数据库”的生命周期语义，目标是本次创建的隔离数据库，不是用户已有设备。已有外部数据库仅做过 TCP 检查，未认证或写入。测试没有使用用户提供的密码。

### 验收发现的真实问题

1. WebUI 进程标识路径最初相对于仓库根填写，但实际校验相对于 `workdir`；已通过 `/proc` 定位，修正元数据并补真实回归，生产应用 CLI 重新通过。
2. 混合模式 harness 首次更换了特殊身份，应用正确拒绝；harness 改用同一身份，不改服务端保护逻辑。
3. 原生应用首次启动失败后，同配置的已退出 PID 记录会阻止重试；已补真实失败回归，允许精确元数据匹配且无未收束成员的陈旧记录沿既有安全路径替换。混合模式重新通过；不同配置与存活不确定记录仍拒绝自动接管。

### 测试资源清理

自动清理最初因动态删除范围不可验证被安全检查拦截。随后保留资源，读取具体容器 ID、项目标签和挂载路径，逐个显式删除本次测试容器、网络、唯一镜像。清理后 `docker ps` 为空且测试网络不存在。临时数据库数据目录保留，未删除用户数据卷。

### 已完成应用回归

- Agent：977 passed。
- Gateway：34 passed，1 条既有 warning。
- mem0：86 passed。
- openkb：335 passed，2 条既有 warning。
- rela-mem：41 passed、29 integration deselected，1 条既有 warning；默认契约中原先 Docker 环境失败已消除。
- 根控制面：362 passed（控制器最后独立重跑，38.86s）；配置 validate 成功，10 个组件；compileall、diff、local 文件 ignore 检查通过。
- WebUI launcher/proxy：15 passed。
- TUI：253 passed、13 个既有 snapshot failed，1 条 warning。与变更前 14 项失败集合比较无新增失败；原先偶发的 history 测试本次通过，不声称修复。


### 最终回归补充

- 整体集成审查发现的 endpoint-only CLI 覆盖与外部消费者的 embedded-owner 就绪判断均已补真实回归并修复，复审通过。
- 最终重跑曾出现一个探测测试的 timing flake：0.2s 总预算包含 spawn 启动，负载下请求尚未发出，实际连接断言失败。用测试期延迟稳定复现后，只调整测试为 0.8s 预算和超过 1.5s 的持续响应，保留原有额外 0.15s 时限、真实请求联系与资源回收断言；生产代码不变。最终根套件重新 356 passed，原失败记录保留。

- 整体审查最后两个发现均先复现五项真实失败再修复：leader 在 stop 前已退出且仍有同组监听后代时，effective/stop/spawn 保留记录并拒绝不安全接管；TCP 的 DNS 与全部地址尝试也放入完整操作 deadline。聚焦六项、受影响文件 65 项通过，最后根套件 362 项通过。一次集中修复后的单次 scoped 复审通过，未遗留阻断项。

## 完成清单

- [x] 依赖图、HTTP/TCP 探测、环境变量注入。
- [x] Compose/进程运行时、生命周期编排与 CLI 接线。
- [x] 根 `icarus.toml`、人类友好的配置校验与有效配置查看。
- [x] `infra/` 中间件定义与 rela-mem 拆分、应用配置覆盖。
- [x] 真实 E2E、受影响套件回归、compile/diff 检查；TUI 既有失败如实记录。
- [x] 仓库使用文档与原飞书 `infra` 技术方案已同步；飞书 revision 11 的完成状态、362 项计数、安全修复/TUI 说明与原 infra 目录归属均回查通过。
