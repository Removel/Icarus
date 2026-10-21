# OpenKB 第一批执行记录

- 日期：2026-10-04；隔离 worktree，未提交。
- [设计](arch.md) · [计划](plan.md)

## 已实施

上游外围裁剪、导航 skill 迁根与许可/容器路径说明、锁定 dev/test 入口。
CLI/API/backend/UI、runtime marketplace、deck 三件套、lock/config/assets 和原测试均保留。

## 实际验证

| 检查 | 结果 |
| --- | --- |
| 新脚本回归 | 原 RED 8 → GREEN 8；默认真实 deck gate 遗漏已 RED→GREEN；新增私有构建数据规则检查当前通过 |
| 真实 bundled skill | 1 项通过，三份实际 SKILL.md 均能发现/读取，已进入默认 CI gate |
| 默认测试入口 | `make test-openkb`：325 passed；两条既有 coroutine 未 await 警告 |
| 完整原套件 | 基线 1254 passed/1 failed → 最终 1264 passed/同 1 failed |
| 既有失败 | `tests/test_file_size.py::test_no_module_exceeds_limit`；不扩大豁免 |
| runtime marketplace/skill/bundle 专项 | 35 passed |
| 新 Python 测试 Ruff | 通过 |
| 实际 dev installer / 锁检查 | 成功，`uv lock --check` 通过 |
| Wheel | 构建成功，三份 `openkb/_skills/*/SKILL.md` 均存在 |
| 隔离 API | 临时 HOME/config/KB 下 helper import、create_app 和 `/openapi.json` 200 验证通过 |
| 实际源码镜像 | 原 Dockerfile 全部阶段构建成功；Debian 下载 HTTP 500/EOF 原样重试后成功，未改源码或换旧业务镜像 |
| 安装包/runtime/UI | 从 `/tmp` import 实际 site-packages 包；三份 wheel 内 `_skills`、真实扫描、Workbench bundle、LICENSE 均存在；镜像 `pip check` 通过 |
| 真实隔离服务 | Workbench HTML、OpenAPI、401、managed KB/status/list/graph/page 读取、concept 编辑、路径保护/init 全通过 |
| 持久化与删除 | 重建临时容器后别名/config/两知识库及编辑页保留；dry-run 删除预览与实际临时 concept 删除通过 |
| Docker 构建数据保护 | 合成嵌套 KB/`.openkb`/kbs/output/`.env` 经真实 COPY 不进入镜像；frontend/skill/LICENSE 输入保留 |
| ShellCheck | 新 install/test 脚本检查通过；明确 `CDPATH=''` 后脚本行为回归仍通过 |

## 裁决与待验证

- 现有 uv.lock 与 `api` extra 的 `web` alias 元数据不一致，locked 安装在改代码前失败。
  仅修对应 requires-dist 元数据；无 package/version、锁 revision、解析依赖图变动。
  PR6 含同类修复，合入时需保留并收敛。
- 根分发 manifest 与临时 KB runtime marketplace 不混淆；后者和其测试仍留。
- 导航 skill consumer 从缺容器入口到正确 managed Compose 命令；不创建第二 KB，
  不把 `/data/kbs` 当主机 Read 路径。
- 纯 absence/源码 grep 门禁由一次性布局/来源/哈希/链接验收替代；增加 per-app
  Dockerignore 保护私有环境/Secret，不改 Docker build context。
- 原 Docker 环境阻塞已解决：在用户授权启动的 Docker Desktop 构建真实源码镜像，用独立无宿主端口/internal 网络测试项目验收，不访问已有知识库或远程模型。初始化与编辑使用合成页；`index.md` 编辑收到预期 400（生成页只读），改为合法 concept fixture 验证，不改后端契约。临时容器/网络已停止移除，测试卷和镜像保留。
- 第二批 UI 删除与 PR6 集成仍等待依赖；没有发布/提交/生产数据更改。
