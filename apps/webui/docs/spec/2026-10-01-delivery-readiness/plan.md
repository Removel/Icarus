# WebUI 交付补齐计划

2026-10-01。依据当前代码、71 项浏览器回归及交付审查制定。本文先于实现落盘；完成状态以实际验证记录为准。

2026-10-02 更新：PR 准备已补齐分支约定、提交清单、跨应用 CI、真实服务与容器验收、完整镜像构建、
文档和合成数据截图。最新结果见 [pre-pr.md](pre-pr.md)，Git 提交、推送及远端 CI 尚未执行。

## 交付目标

交付可独立启动的单用户 Icarus 工作台，包含记忆管理、知识管理和 Agent 文本对话。沿用 Mem0 HTTP、OpenKB HTTP 与 Gateway JSON-RPC/WebSocket 协议；业务适配留在各 workspace 包。前端部署入口承担静态文件、同源代理和访问认证，不承担 Agent 业务。

已有工作区改动作为基线保留，不创建分支、不提交、不暂存。新增文件由交付清单列出，最终提交由用户决定。

## 顺序与验收

1. **工程规范**：添加 Prettier、ESLint、明确的检查命令与 WebUI CI；格式化应用代码，保留功能行为。格式、lint、类型检查均须通过。
2. **Mem0**：默认使用真实服务；列表显式包含过期数据，详情及历史按服务 ID 读取；创建使用 `infer: false`；更新保留其他 metadata；写入成功后刷新服务数据，失败保留草稿；批量逐项报告失败。保留现有暂停/恢复语义并明确恢复清除期限。测试覆盖请求契约、刷新持久化、服务失败及批量部分失败。
3. **Agent 对话**：独立 chat workspace 包，经 Gateway 建立会话、订阅更新、加载历史、提交文本、显示流式文本和工具状态、取消任务；断线恢复历史，避免自动重复提交。未知更新类型安全忽略。验证协议错误、流式合并、历史重载、取消与断线。
4. **生产运行**：提供可测试的 Node 静态服务及 HTTP/WebSocket 代理。令牌仅留服务端；默认回环监听，非回环部署要求入口认证；拒绝跨源写入和 WebSocket 连接。提供环境变量样例、容器配置、健康检查和启动命令。
5. **交付文档及验收**：按实现更新 README、API 映射、当前架构、部署/升级/回滚/排障说明和验收记录。专项测试 → WebUI 全套测试 → lint/format/typecheck/build/diff；增加隔离的真实后端 HTTP 集成验证，有凭据时运行最小模型冒烟，不修改既有业务资料。

## 明确边界

这是单用户部署，不新增多租户权限、用户注册或计费。接口尚未提供的服务器分页、知识版本恢复、段落级引用和多模态上传不纳入本次；列表上限与搜索范围必须向用户说明。文档不得将模拟响应测试写成真实模型验收。

## 执行记录

- 基线：71 项浏览器测试通过（Edge），类型检查、构建、diff 检查通过；真实 OpenKB 的库/清单/图谱返回 200，但库为空。
- 工程规范：已加入 ESLint、Prettier 命令、应用格式化和 `.github/workflows/webui.yml`。本地 lint 和类型检查通过；远程 CI 尚未运行。
- Mem0：已接入真实 HTTP 契约，服务 ID、历史、原文创建、metadata 保留、失败草稿和逐项批量反馈已实现；记忆专项 28 项通过（Edge）。
- Gateway：已接入文本会话与流式更新，增加会话创建、历史分页、去重、取消、断线重连、显式重试、错误和窄屏回归；修复浏览器不允许使用 1002 关闭码导致错误恢复中断的问题，改用应用关闭码 4002。
- 生产入口：已实现静态目录约束、单用户 Basic 认证、同源 HTTP/WebSocket 代理、服务端凭据隔离、来源检查、健康探针、环境样例和 Dockerfile；4 项隔离代理测试通过。
- 文档：已更新 README、API 映射、当前架构、部署/升级/回滚/排障说明；增加只读真实服务检查命令 `pnpm check:services`。
- 并发回退复核：恢复被撤销的代理扩展协商、连接清理、测试及验收文档；统一 Mem0 对外端口 8888。源码及测试已重新核对，未对工作区做整体回退。
- 综合验证：87 项离线回归通过，4 项真实服务测试默认跳过；显式开启后，经 WebUI 容器运行的 4 项真实服务测试全部通过，覆盖真实存储、模型编译、Agent 回复、取消与重连。Docker 镜像构建、lint、format、typecheck、build、diff 通过，详见 [acceptance.md](acceptance.md)。
- 部署配置边界：根 `.env` 仍是模型预览配置；Gateway 本轮在进程环境使用已有有效密钥，知识编译在独立容器完成。长期部署需补齐模型凭据与 OpenKB 新建库模板。远端 CI 尚未运行。

## 本轮交付文件

- 工程：`.prettierrc.json`、`.prettierignore`、`eslint.config.mjs`、`package.json` 及根 `.github/workflows/webui.yml`。
- 记忆：`packages/memory-app/src/{mem0,types,useMemories,MemoryDetail}` 与页面集成，`test/mem0_service.py`、`test/packages/memory_app/test_service.py`。
- 对话：`packages/chat-app/`、Shell 导航与开发代理，`test/packages/chat_app/test_chat.py`。
- 生产：`server/index.mjs`、`server/check-services.mjs`、`.env.example`、`Dockerfile`、`.dockerignore`、`test/server/test_server.py`。
- 文档：`README.md`、`docs/api-map.md`、`docs/deployment.md`、本目录的 `arch.md` 和验收记录。

此前已存在的知识库、设计规范、OpenKB 等工作区改动仍保留，不纳入本轮新增实现的完成声明。

真实服务测试与运行说明：`test/integration/test_live.py`、`test/integration/README.md`。仅在显式开启开关后写入独立测试数据或调用模型。


## 验收反馈收尾（2026-10-01）

- [x] 记忆状态、分类、用户、Agent、作用范围、有效期和更新时间放到正文上方。
- [x] 历史每页 10 条；默认展示正文变化区段，可展开修改前后全文。服务仍一次返回全部历史，分页属于前端展示分页。
- [x] 对接 Mem0 新增的 `changes`：区分暂停、恢复、有效期、分类和正文修正；旧记录缺少属性快照时不反推操作类型。
- [x] 记忆规则使用面向用户的表述，说明原文保存、使用范围、暂停与恢复期限。
- [x] 质量检查改为报告列表、详情弹窗、Markdown 导出和确认删除；直接读取 OpenKB 服务端报告档案，每页 10 份，不限制保留数量。
- [x] 报告操作在执行期间防重复，完成后刷新服务端目录；失败可重试。
- [x] 完成记忆差异与分页、报告刷新/导出/删除等专项回归，以及 WebUI 全套和工程检查。
- [x] 真实 Gateway 调用 memory_remember 与 bash、历史恢复、取消验收；详见 execution.md。
- [x] 完成交接文档与最终检查，保留并列明未改动范围的全套失败。
- [ ] **待定，不纳入本轮实现**：原始资料到知识页面及文档关联之间的人工干预。需先确定干预点、可编辑对象、审批/撤销语义和并发编译规则；不能只加按钮而没有后端事务协议。

本轮 Agent 的 Windows Bash 修复属于 Agent 应用，见对应计划；Mem0 的审计字段属于 Mem0 应用。WebUI 不加入模型供应商分支，也不绕过 Gateway 执行命令。
