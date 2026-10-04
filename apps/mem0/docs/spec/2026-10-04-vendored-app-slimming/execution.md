# Mem0 第一批执行记录

- 日期：2026-10-04；隔离 worktree，未提交。
- [设计](arch.md) · [计划](plan.md)

## 已实施

上游外围裁剪、五个完整开发 skill bundle 迁移、迁移脚本测试夹具保全、app-owned
dev/test 入口、LICENSE 打包输入和维护文档更新。SDK 业务、鉴权/数据库迁移、dashboard、
运维脚本和原测试套件保留。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 原迁移脚本回归 | 新夹具缺失 RED 16 → 保全后 GREEN 16；脚本哈希未变 |
| 新脚本回归 | 原 RED 8 → GREEN 8；审查新增环境归属/pip 配置隔离 RED 2 → GREEN；最终 10 passed |
| 默认测试入口 | `make test-mem0`：82 passed |
| 完整原套件 | 前后均 37 个可选依赖 collection errors、9 skipped；不是全套通过 |
| 新测试 Ruff | 通过，未改全仓格式 |
| Wheel | 实际构建成功，包含 OSS notices JSON 与 LICENSE |
| 核心资源 | 与 OpenKB 一起核对 490 份受保护核心/UI/运维/迁移资源，内容相同 |
| 实际 dev installer | `bash apps/mem0/scripts/install.sh --dev` 成功 |
| API/dashboard 镜像 | 两者均已实际构建；另以 `--no-cache` 完整重建成功，无缓存 Mem0 镜像 `pip check` 通过 |
| 真实隔离 Mem0/PostgreSQL | docs/OpenAPI、401、中文记忆 create/list/search、scope 隔离、update/history/delete 全通过 |
| 数据持久化 | 临时容器重建后 PostgreSQL 记忆与 SQLite 历史可恢复；实际 Alembic 到 `006` |
| Dashboard | 无缓存镜像健康页和 setup HTML 实际服务通过 |
| Docker 构建数据保护 | 合成的嵌套 DB/向量数据/`.env` 经真实 COPY 不进入镜像，SDK/许可仍存在 |
| ShellCheck | 新 install/test 脚本检查通过；`CDPATH=''` 明确空赋值，行为测试仍 10 passed |

## 裁决与待验证

- 纯文档源码字符串/absence-only change-detector 测试改为一次性布局/哈希/链接验收；
  永久回归测试关注运行脚本与真实行为，不改变测试覆盖的 SDK/服务逻辑。
- 迁移 helper/skill 原 LICENSE 保留；本地旧路径修正不自动运行集成/平台迁移。
- 增加 app Dockerignore 防止新增 .venv/Secret/cache 被当成构建输入。
- 原 Docker 环境阻塞已解决：用户授权启动已安装的 Docker Desktop，不改主机设置。验证使用独立 `icarus-slimming-smoke` 项目、无宿主端口、internal 网络和临时数据卷；embedding 为本地 fixture，未调用真实模型。临时容器/网络已停止移除，测试卷与镜像保留，不删除用户数据。
- 全量可选环境未齐备，保留所有原测试与非零退出；不得通过删/skip 测试把结果变绿。
- 第二批 UI 清理、PR6 合入适配和生产数据操作未执行。没有 Git 提交或推送。
