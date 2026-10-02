# Argus 模块命名约定

> 状态：已完成现有模块改名并验证；后续决策图开发复用此规范。配套 [plan.md](plan.md)。

## 规则

- Python 模块/目录、方法和变量使用 snake_case；类使用 PascalCase；常量使用 UPPER_SNAKE_CASE。
- 软件或平台特定实现保留名称与职责：`qq_connector.py` / `QQConnector`，`qq_handler.py` / `QQHandler`，`qq_service.py` / `QQService`，`jev_client.py` / `JevClient`。
- `QQ` 为缩写，类名中仍大写；Jev/Icarus 按名字写为 `JevClient/IcarusClient`。
- 测试模块使用 `test_` 前缀和对应 snake_case 名称；以业务场景组织的现有测试不强制合并成一个文件。
- 通用类型可以在同一职责模块内组织，不强制一个类一个文件。
- 保留 `connector/handler/service/strategy/dao/entity/clients/utils` 现有目录；当前另有职责明确的 `evidence` 与 `decision_graph` 包，不为单复数整理而改层次。
- 应用内源码使用相对导入；对象/表记录归entity，事实与prompt归evidence，判断轨迹归strategy。2026-10-02整理后的实际调用链与图能力见[当前架构](../2026-10-01-evidence-and-pacing/arch.md)，本目录plan保留最初命名整理记录。

## 范围

先将当前 8 个 PascalCase 源模块、5 个 PascalCase 测试模块改名并同步所有 import、动态模块引用及当前文档路径。详细映射见实施计划。不改变类名、方法、SQLAlchemy 映射、配置键、数据库持久化标签或 HTTP/RPC 合约；不保留旧路径转发壳，因此仓库外导入旧路径的脚本也需调用方更新。

命名整理与决策图实现必须分别验证。前者只允许路径/import 差异；后者新增 `strategy/base_policy.py` / `BasePolicy` 与 `decision_graph/graph.py`、`runner.py` 等，使用同一规范。本次不创建还没有功能实现的空 BasePolicy 或图引擎。

## 安全与验收

保留本机运行配置、未提交文档修改和无关备份；不读密钥，不重启服务。用隔离配置和临时数据目录运行 Argus 测试，防止本机 QQ enabled=true 使测试连接真实服务。对照测试收集集合、新旧模块功能、编译与 diff 检查；代码重命名后再根据实际路径更新当前架构文档。未经另行要求不提交或推送。
