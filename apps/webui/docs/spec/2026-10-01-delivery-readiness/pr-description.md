# feat(webui): connect memory, knowledge and chat workspaces

提供连接 Mem0、OpenKB 和 Agent Gateway 的单用户 WebUI。用户可以管理记忆及其作用范围和历史、
导入并阅读知识资料、浏览文档关联与质量报告，并在同一工作台中进行支持工具状态、取消和历史恢复的文本对话。
页面刷新后从服务读取持久化数据，编辑和离开页面时保护未保存草稿。

生产入口提供 Node 静态服务、Basic 认证及同源 HTTP/WebSocket 代理，服务凭据留在服务端。
同时补齐后端契约、Windows Bash 进程清理、CI、Docker 构建、部署与回滚说明。

## 配套变化

- Agent/Gateway：`memory.get_context` 复用配置身份与 Session 工作区规则；召回默认截止时间改为 5 秒。
- Mem0：同步/异步历史记录分类与有效期变化，兼容已有 SQLite 历史数据。
- OpenKB：报告删除、资料稳定标识与来源字段、新建知识库的部署模型模板。
- Windows Bash：定位 Git Bash，收束超时、取消及后台子进程。

## 验证

- WebUI：100 passed、7 skipped；单独开启后，7 项真实服务与模型测试全部通过。
- 新 WebUI Docker 镜像：7 项真实服务测试全部通过，包含认证入口及 HTTP/WebSocket 转发。
- Agent：Linux 全量 709 passed；Windows 受影响契约与 Gateway 合计 112 passed。
- Gateway：全量 16 passed；Mem0 更新和历史 76 passed；OpenKB 受影响接口在 Linux 下 217 passed。
- lint、format、typecheck、build、compile、diff、OpenKB 锁文件检查、actionlint 通过。
- WebUI、Mem0、OpenKB 均从仓库 Dockerfile 完整构建，未用旧业务镜像替代源码。

远端 CI 待 PR 推送后执行。完整环境、命令、构建来源和界面截图见 [验证记录](pre-pr.md)。

## 升级与边界

WebUI 与后端契约需要配套升级；更新前备份 Mem0 history.db 和 OpenKB 数据。旧 Mem0 回滚可能移除
新增审计列，需保留升级前及升级后的数据库副本。运行步骤见 [部署说明](../../deployment.md)。

不宣称全项目全绿：OpenKB Linux 全量仍有基线已有的模块行数检查失败；Windows 全量存在平台差异；
Mem0 全库缺少可选供应商依赖，memory 目录的一项既有异步 notice 测试在基线与当前版本均失败。
相关专项和 Linux Agent 全量通过，未为了门禁改写这些旧测试。

本次为单用户工作台；服务端全量分页、知识版本恢复和知识加工中间过程的人工干预不在本次范围。
