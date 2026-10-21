# rela-mem 实施计划

| 项目 | 内容 |
|---|---|
| 日期 | 2026-10-18 |
| 状态 | 后端已实现；48 项测试、独立审阅修复、最终镜像与持久化验证已完成 |
| 依据 | [design.md](design.md) |
| 分支 | `feat/rela-mem`（已从 `feature` 切出） |
| 目标 | 先交付可独立验收的完整后端服务，不接 Agent/Argus |

> 本文件是实施顺序和验收清单，不是当前源码说明。用户已确认补充行为并明确授权开始编码；保留已有阶段划分，实际完成与偏差见 §7。

## 0. 前置约束

- 设计与实现不一致时，以最新评审结论和测试为准，并同步更新 `design.md`。
- 不引入迁移框架（无 Alembic），初始化脚本必须幂等。
- 不新增业务表；PostgreSQL 只有 `profile_entries` 一张业务表。
- 不新增注册表；profile key 只校验格式，relation kind 只校验格式。
- 本期不改 Agent、Gateway、Argus，也不把业务代码搬入 `packages/`。允许为本应用增加根 `Makefile` 测试快捷入口，但统一重构所有服务的部署/配置不属于本期。
- 当前文档复核依据：本地 `feature` 与 `feat/rela-mem` 均为 `f717798`；2026-10-18 查询远端 `origin/feature` 为 `3b62310`，已确认它是当前分支的祖先。本地已有 7 个未推送提交，不为同步基线自动 push/rebase。
- 请求依赖方向固定为 `api → domain/services → repositories`；`main.py` 负责组装。服务层不导入 FastAPI，仓储不导入服务层，不增加通用 CRUD 框架或无调用方的共享包。
- 配置身份和连接端点来自可挂载 JSON（`RELA_MEM_CONFIG` 指定路径），Secret 从环境读取；`removel` / 鱼梦只是当前设备的配置示例。
- 每个阶段实现和测试一起组织；如用户另行授权提交，文档提交单独拆开。
- 不自动 commit / push；按用户要求执行。

## 1. 目标代码布局

```text
apps/rela-mem/
├── docs/spec/2026-10-18-rela-mem/
│   ├── design.md
│   └── plan.md
├── src/
│   ├── main.py                 # FastAPI app + lifespan
│   ├── config.py               # settings + env
│   ├── errors.py
│   ├── api/
│   │   ├── dependencies.py     # 认证与服务依赖注入
│   │   ├── health.py
│   │   ├── persons.py
│   │   ├── aliases.py
│   │   ├── profile.py
│   │   ├── circles.py
│   │   ├── members.py
│   │   ├── relations.py
│   │   └── graph.py
│   ├── domain/
│   │   ├── enums.py
│   │   ├── models.py
│   │   └── services/
│   │       ├── person_service.py
│   │       ├── profile_service.py
│   │       ├── circle_service.py
│   │       ├── member_service.py
│   │       ├── relation_service.py
│   │       └── graph_service.py
│   ├── repositories/
│   │   ├── graph_client.py
│   │   ├── database.py              # PG engine/session 与单表定义
│   │   ├── initialization.py        # 幂等建表/建索引和约束检查
│   │   ├── person_repo.py
│   │   ├── alias_repo.py
│   │   ├── circle_repo.py
│   │   ├── member_repo.py
│   │   ├── relation_repo.py
│   │   ├── profile_repo.py
│   │   └── cypher/
│   ├── schemas/
│   │   ├── common.py
│   │   ├── person.py
│   │   ├── profile.py
│   │   ├── circle.py
│   │   ├── member.py
│   │   └── relation.py
│   └── utils/
│       ├── ids.py
│       ├── normalize.py
│       └── time.py
├── test/
│   ├── conftest.py
│   ├── test_main.py
│   ├── test_config.py
│   ├── api/
│   ├── schemas/
│   ├── domain/services/
│   ├── repositories/
│   ├── utils/
│   ├── scripts/
│   └── integration/
├── scripts/
│   ├── install.sh
│   ├── test.sh
│   ├── init_graph.py
│   ├── init_postgres.py
│   ├── seed_demo.py
│   └── smoke_rest.py
├── Dockerfile
├── docker-compose.yaml
├── pyproject.toml
├── settings.json
└── README.md                       # Phase 5 再写，不提前声明未实现能力
```

测试目录镜像源码目录。例如：

`src` 直接作为 import root，不再套一层包名：

```text
src/api/persons.py
  ↔ test/api/test_persons.py

src/domain/services/relation_service.py
  ↔ test/domain/services/test_relation_service.py
```

启动：`uvicorn main:app --app-dir src`；导入：`from api.persons import ...`、`from config import ...`。pytest 配置 `pythonpath = ["src"]`、`testpaths = ["test"]`。

## 2. 阶段划分

### Phase 0：脚手架、配置、存储初始化

交付：

- `pyproject.toml`、`Dockerfile`、`docker-compose.yaml`（`rela-mem` + `falkordb:v4.22.1` + `pgvector/pgvector:pg17`；应用端口 `8767`，数据库暴露到宿主机 `16379` / `15432`）；
- `config.py`：读取 `settings.json` 和根 `.env`，严格校验；
- FastAPI `main.py` + lifespan，建立 FalkorDB async client 和 SQLAlchemy async engine；
- `GET /healthz`：两个存储都可用返回 200，否则 503；
- `scripts/init_graph.py`：先建 EXACT 索引，再建唯一约束，幂等；
- `scripts/init_postgres.py`：创建 `rela_mem` schema、`profile_entries` 表和索引，幂等；
- 启动顺序：初始化脚本成功后再启动 uvicorn；
- 测试：配置校验、healthz、初始化幂等、FalkorDB 约束存在性。

验收：

```bash
docker compose up -d
curl -s localhost:8767/healthz
# 重复执行初始化脚本不报错
```

### Phase 1：Person / Alias / identity.resolve

交付：

- `Person`、`Alias` 的图模型与仓储；
- `POST/GET/PATCH /v1/persons`；
- `POST/DELETE /v1/persons/{id}/aliases`；
- `GET /v1/identity/resolve`；
- `user` / `agent` 启动 bootstrap（配置 ID + display_name，kind 校验）；
- 别名规范化（`qq`/`phone` 数字，`wechat` 去空白，`name`/未知小写）；
- `Person.kind` 枚举 `user | agent | human | virtual`；
- 分页游标。

关键测试：

- 创建人物 + 别名；重复别名返回 409；
- `identity.resolve` 命中/未命中（200 + `person: null`）；
- QQ 号前后空格、非数字字符归一化后命中；
- 不能通过 API 创建 `kind=user|agent`；
- bootstrap 已存在但 kind 不匹配、用户与智能体 ID 相同、同种特殊身份被改配为另一 ID 时启动失败；不能创建第二个特殊身份；
- 删除 Alias 后其规范化值可以重新绑定，初始 Alias 冲突时创建 Person 原子回滚；
- Person、Alias.alias_id 和精确 Alias.identity_key 唯一；name 别名不设 identity_key，允许重名。

### Phase 2：Profile

交付：

- `scripts/init_postgres.py` 的 `profile_entries` 表和索引（Phase 0 已建，Phase 2 补测试）；
- `GET /v1/persons/{id}/profile`：当前值、`q` 搜索、`keys` 精确过滤、分页；
- `GET /v1/persons/{id}/profile/keys`；
- `PUT /v1/persons/{id}/profile/{attr_key}`：追加 `active` 行；
- `DELETE /v1/persons/{id}/profile/{attr_key}`：追加 `deleted` 墓碑；
- `GET /v1/persons/{id}/profile/{attr_key}/history`；
- `attr_key` / JSON 大小 / `source` / `recorded_at` 校验；
- `source_ref` 仅作追溯、允许重复；写请求不做服务端请求去重
- DELETE 通过 query 接收 `source` 和可选 `source_ref`，时间由服务端生成

关键测试：

- 最新一条 `active` 成为当前值；
- 墓碑后当前值消失，不会回退到更旧的 `active`；
- 墓碑后再写入 `active` 可以恢复；
- `q` 同时匹配 key 和 value，大小写不敏感；
- `q` 只作用于单个人物；
- `source_ref` 可重复，仅保留追溯；DELETE 的 query 来源参数写入历史行；
- 初始化配置使用非默认测试 schema 时，不得创建/查询/清理默认 `rela_mem` schema；约束 `PENDING` 需等待，`FAILED` 或超时应启动失败。
- 合法 JSON null 可作为 `active` 值，不能与 `status=deleted` 墓碑混淆；非法 key、超大 JSON、无时区/未来的时间处理必须由契约测试明确，不能依赖驱动的偶然行为。

### Phase 3：Circle / Membership

交付：

- `POST/GET/PATCH /v1/circles`；
- `PUT/DELETE /v1/circles/{id}/members/{person_id}`；
- `GET /v1/circles/{id}/members`；
- `GET /v1/persons/{id}/circles`；
- `membership_key` 幂等；
- `role` / `identity_label` / `alias` 自由字符串；
- `status=active|left`。

关键测试：

- 同一 `(person, circle)` 重复加入只更新，不重复建边；
- 移除成员后 `status=left`，默认成员列表不返回；
- `virtual` 主体可以加入圈子；
- 圈子 `status=archived` 后默认列表不返回，`include_archived=true` 可查；
- 人物圈子列表返回圈内昵称和身份。

### Phase 4：Relation / Graph

交付：

- `POST /v1/relations`：upsert，返回 `created`；
- `GET /v1/relations`：`person_id`、`circle_id`、`kind`、`status`、`other_person_id`、`include_incoming`、分页；响应保留原始正反字段，`direction=one_way|two_way`，另用 `relative_direction=incoming|outgoing` 表示查询视角；
- `PATCH /v1/relations/{id}`；
- `DELETE /v1/relations/{id}`：`status=invalid`；
- 关系方向校验；
- `relation_key` 确定性生成和唯一约束；
- `GET /v1/graph/shortest-path`：无向遍历，`max_depth<=5`。

关键测试：

- 双向缺 `reverse_detail` → 400；
- 单向带 `reverse_detail` → 400；
- 将双向请求的端点交换并同时交换正反内容，再提交仍是同一条边，称谓不颠倒；
- 自由 `kind` 中出现 `|` 等字符时，不能使不同关系编码成相同键；
- 修改关系方向/kind 造成键冲突时返回 409，旧边完整保留；
- 创建 `from=张三, to=李四, forward_detail=表哥, reverse_detail=表弟`：
  - 张三是李四的表哥，李四是张三的表弟；
  - 查询李四返回张三 `detail=表哥`；
  - 查询张三返回李四 `detail=表弟`；
- 单向查询：查询目标侧返回 `forward_detail`；查询来源侧 `detail=null`、`detail_missing=true`；
- 重复 `relation_key` 幂等，不新增边；
- 同一 pair 不同圈子可并存；
- 同一 pair 双向 + 单向可并存；
- 关系失效后默认查询不返回；
- 最短路径返回节点和边；深度超限返回 400；
- 图约束重复 `CREATE` 被拒绝（集成测试）。

### Phase 5：种子、验收、文档

交付：

- `scripts/seed_demo.py`：我 / Icarus / 张三 / 李四 / 家庭圈 / 关系 / 档案；
- `scripts/smoke_rest.py`：按验收场景走一遍；
- `README.md`：安装、启动、配置、测试；
- 更新 `design.md` 中“尚未实施”的状态；
- 补 `arch.md`（只在代码落地后写，描述当前架构）。

验收：

- 设计文档第 10 节全部场景通过；
- `make test-rela-mem` 通过；
- `python -m compileall apps/rela-mem/src` 通过；
- `git diff --check` 通过；
- 重启 Compose 后数据仍在。

### Phase 6：Agent 候选别名

为 Agent 的 `relationship_find` 提供重名消歧信息：

- 修改 `GET /v1/persons?query=`，每个结果附带最多 10 个 aliases；
- 只返回 `alias_id`、`namespace`、`value`、`display_value`；
- 不改变分页、过滤、权限和空结果语义；
- 增加集成测试：两个同名人物返回各自别名；分页不重复；未命中返回空列表。

该阶段是 agent relationship 插件的前置依赖，实施时与插件任务分开提交。

2026-10-19 实施验证：已完成安全字段投影和每人最多10条别名；`test_search_candidates_include_bounded_aliases` 先因缺失 aliases 失败，再通过。真实隔离数据库中 persons 测试4项、rela-mem 全量49项通过；覆盖同名候选各自别名、超10条、person_id分页和空结果。未修改其他服务能力，未提交/推送。

## 3. 测试与验证顺序

每阶段按以下顺序执行：

1. 该阶段最小的测试文件或目录；
2. `apps/rela-mem` 全量测试；
3. 受影响的其他应用测试（本期应无）；
4. `python -m compileall apps/rela-mem/src`；
5. `git diff --check`。

集成测试要求：

- 使用独立图 `rela_mem_test` 和独立 PostgreSQL 库/schema `rela_mem_test`；
- fixture 创建/清理测试数据，不碰真实 `rela_mem`；
- FalkorDB 约束测试必须在 `init_graph.py` 之后运行；
- 数据库与 API 都使用异步接口；CLI 脚本以同一接口启动事件循环，不复制同步实现。
- 测试不依赖外部模型或网络，只依赖隔离的本地 Docker Compose。不得停止按名称猜测出的外部容器、清理用户图/库或修改真实 `.env`。

### 自审新增的重点回归

- [x] `test_reversed_two_way_upsert_preserves_details`：交换端点并交换称谓后 upsert，仍只有一条正确边。
- [x] `test_profile_tombstone_does_not_resurrect_old_value`：先取最新行再按状态/关键词筛选，墓碑不回退；合法 JSON null 不误当删除。
- [x] `test_alias_conflict_rolls_back_person_creation`：初始别名被占用时，创建人物不留下孤立节点；删除别名后该值可重新绑定。
- [x] `test_profile_write_rejects_unknown_person_and_bad_inputs`：不能为不存在的人物追加档案；数据库故障不能被解释为“人物不存在”。
- [x] `test_startup_waits_for_operational_constraints`：初始化重复运行可通过，错误和未就绪约束不能被吞掉；连接池在启动失败和停机时关闭。
- [x] `test_paths_follow_active_edges_and_exclude_archived_people`：归档人物和失效关系不参与默认路径，历史数据不被物理删除。
- [x] 独立镜像/Compose 真实启动与 HTTP 冒烟（对应原计划 `test_container_uses_mounted_configuration_without_repository_imports`）：单独镜像只靠本应用包、挂载配置和环境变量运行，两个身份可自定义。

## 4. 提交边界

建议：

1. 文档提交：`docs(rela-mem): add base service design and plan`（仅 `design.md` + `plan.md`）。
2. Phase 0：脚手架 + 配置 + 初始化 + healthz + 测试。
3. Phase 1：Person/Alias/identity.resolve + bootstrap + 测试。
4. Phase 2：Profile + 测试。
5. Phase 3：Circle/Membership + 测试。
6. Phase 4：Relation/Graph + 测试。
7. Phase 5：seed + smoke + README + 验收修正。

不把无关重构、格式化或实验文件混入功能提交。

## 5. 授权边界

用户已明确授权在 `feat/rela-mem` 开始后端实现；源码和测试直接位于 `src` / `test`，不增加应用包名层。只交付独立后端和应用自身运行脚本，不接 Agent/Argus，不扩展统一生命周期。仍不自动提交、推送、发布镜像或修改真实用户数据库。

## 6. 已确认项

1. 关系方向采用边语义：`a → b` 表示 a 是 b 的 xxx；`forward_detail` 是 from 对 to 的称谓，`reverse_detail` 是 to 对 from 的称谓。
2. 特殊身份默认值：本机 `user=removel / 鱼梦`，`agent=icarus / Icarus`；均可在配置文件中自定义。
3. 应用端口 `8767`；FalkorDB / PostgreSQL 暴露到宿主机 `16379` / `15432`，可配置。
4. 保留 `GET /v1/persons/{id}/circles`。

## 7. 实施记录（2026-10-18）

- Phase 0–4 的核心 API 已实现，Phase 5 脚本/README/当前架构文档已添加；独立审阅修复后单元/契约/真实数据库测试共 48 项通过（最终运行约 9.6s）。
- 审阅发现的 5 项已修复并完成 RED→GREEN：关系并发失效不被陈旧 PATCH 恢复、POST 显式 null 清空 provenance、已有 PG 表结构不兼容时拒绝启动、配置校验不渲染 Secret 输入、畸形历史游标统一 400。
- `make test-rela-mem`（修复后）：20 passed，28 integration deselected；`make test-rela-mem-integration`：48 passed。
- 图/PG 集成测试仅使用本机专用测试容器，随机图和 schema。并发别名唯一、原子回滚、初始化幂等、墓碑不回退、历史分页、虚拟成员、双向反向提交、键冲突回滚、路径归档过滤均已运行。
- 本地 `rela-mem:dev` 镜像构建成功；独立三容器 Compose 启动健康检查、真实 HTTP 种子冒烟通过。重启三个容器后未重新写种子，已验证身份、档案和关系保留。审阅修复后的最终镜像再次构建并在原持久数据上启动，完成只读 HTTP 与数据保留检查。测试结束只停止本会话的隔离容器，测试 bind 数据保留，未清理真实用户数据。
- 单独 wheel 构建和内容检查通过：顶层 main/config/errors 与各层包均存在，没有 test 或多余 rela_mem 包层。第一次 `--no-build-isolation` 尝试因开发环境缺 setuptools 失败；使用 pyproject 声明的隔离构建后成功，不隐藏前一次失败。
- 编译、`pip check`、tracked diff 及全部 untracked 应用文件空白检查通过。
- 上游弃用告警未屏蔽：Starlette 对 anyio BlockingPortal 别名、FalkorDB 对 redis lib_name/lib_version 的调用。当前不影响通过结果。
- 保留合理布局简化：aliases 合并在 api/persons.py，成员在 api/circles.py，graph 路由在 api/relations.py；未创建没有调用方的 models.py 或空的额外模块。
- 最短路径采用 service 内有界无向 BFS + 批量邻接查询，不依赖版本受限的内置 shortestPath。5000 节点/邻接行预算到达时报 LIMIT_EXCEEDED，不返回不完整路径。
- 未运行其他应用的全量套件（未改其源码）；未连接远端服务器/真实用户库、未调用模型、未提交/推送/发布镜像。独立代码审查已完成；五项反馈已修复并通过回归，全量 48 passed。
