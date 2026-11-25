# 管理记忆列表完整分页实施计划

## 目标与证据

日期：2026-11-06。状态：已实施；聚焦回归通过，完整保留套件存在已验证的基线失败。沿用既有 memory-browser 功能目录。

统一开发基线及关联任务见 [实施入口](../../../../webui/docs/spec/2026-11-06-chat-reliability/plan.md)。

server/main.py 的 GET /memories/page 先取 ALL_MEMORIES_LIMIT + 1 条，再截取前 1000 条交给 memory_page 筛选、排序、分页。PGVector.list 的 SQL 只有 LIMIT，没有 ORDER BY。因此新记录和筛选命中的记录可能根本不进入管理快照；只给这 1000 条重新排序不能修复完整性。

当前真实环境只有 2 条记录，truncated=false。本缺陷不是当前历史记忆缺失的已确认原因；历史数据核对见 [数据审计计划](../../../../../docs/spec/2026-10-29-local-environment-reset/plan-memory-data-audit.md)。

## 文件与接口

- server/main.py：GET /memories/page 路由及管理列表调用。
- server/memory_page.py：既有序列化、筛选及排序语义，避免路由与存储各自解释条件。
- mem0/vector_stores/pgvector.py：在存储边界实现有明确管理调用者的计数、筛选、排序、分页读取，不把 SQL 写进 Agent 或 WebUI。
- tests/test_memory_page.py，以及新增或扩展管理路由/pgvector 回归。
- MODIFICATIONS.md：记录对维护中分支的存储和管理 API 修改，保留第三方许可。
- 实施前读取 mem0/AGENTS.md、tests/AGENTS.md；本计划不要求对 Mem0 整树同步上游。

## Task M1：完整数据集上的管理分页

- [x] 用超过 1000 条的隔离夹具复现：最新一条、仅在旧截断范围之外命中的查询、第二个用户/作用域都能被查询到。
- [x] 将筛选和稳定排序放在截断之前，在数据库侧对完整结果计数并分页；按 updated_at、created_at 回退及 id 建立确定顺序，覆盖无效/缺失日期。
- [x] 页码超界沿用最后一页行为；状态、分类、用户、作用域和文本搜索含义与当前管理页面一致，不在存储实现中降低转义或参数绑定要求。
- [x] 保留响应 results、page、page_size、total、counts、categories、users、scopes、truncated 的兼容结构。
- [x] total 是所有筛选条件命中数；counts 和筛选选项来自完整管理数据集，沿用当前全库统计语义；支持完整分页的路径返回 truncated=false。
- [x] 总数、分面和当前页在一个一致的读取视图中产生，避免并发插入导致页码和总数互相矛盾；评估实际 SQL 和事务成本。
- [x] 不采用无上限拉取所有向量/正文到 Python 的方案；只读取当前页所需正文，必要分面与统计在数据库侧完成。
- [x] 新能力只服务已有管理路由。SDK 通用 list(top_k) 和检索接口保持已有语义，不为上层便利扩大抽象层或重定义所有 provider。
- [x] 核对可配置 vector store 的支持边界；若某 provider 不支持完整管理分页，要明确返回可诊断错误，不能继续将截断快照标记为完整。

## Task M2：验证容量与兼容性

- [x] 覆盖正/倒序、时间相同、缺失时间、Unicode/特殊字符查询、过期边界、空库、末页和筛选选项。
- [x] 管理鉴权保持管理员要求；非管理员访问仍被拒绝，原 GET /memories 与 search/get/history 不改变。
- [x] 通过实际隔离 PostgreSQL/pgvector 检查 SQL 行为；路由使用可控后端，不能只测试 Python 列表排序或 SQL 文本包含某个词。
- [x] 更新测试入口，确保新增测试被 make test-mem0 的契约门槛覆盖；额外集成测试明确记录独立运行方式和环境要求。
- [x] 对 Mem0 Python 同步/异步的已有调用保持行为一致；不要为不存在的调用者添加重复接口。

## 验收命令与限制

~~~bash
# 先在 Mem0 自有环境执行新建/修改的最小测试文件。
apps/mem0/.venv/bin/python -m pytest apps/mem0/tests/test_memory_page.py -q
make test-mem0
# 涉及 SDK 存储行为时，执行相关 pgvector 测试和完整保留套件。
make test-mem0-full
apps/mem0/.venv/bin/python -m compileall -q apps/mem0/server apps/mem0/mem0/vector_stores apps/mem0/tests
git diff --check
~~~

2026-11-06 调研时 Mem0 开发 .venv 不存在；安装时使用应用自有环境，缺依赖或收集失败不能算测试通过。不得使用运行中 mem0_app 作为千条测试数据库。是否创建测试数据库/容器及其生命周期须写清隔离名称和清理范围，不停止用户正在运行的服务。

回滚仅针对管理查询实现；本计划默认不修改持久化 schema，也不重新生成 embedding。若实际需要索引或 schema 变更，先补充迁移与回滚步骤，不自动重建记忆表。


## 实施记录与新增持久化缺陷

2026-11-06：`PGVector.browse_memories` 通过单条 SQL 的 CTE 在完整集合上过滤、计数、分面及分页，仅向 Python 返回当前页正文。日期采用合法性检查、updated_at/created_at 回退与 UUID 稳定排序；依赖 PostgreSQL 16+ 的 `pg_input_is_valid`，当前受管环境为 PostgreSQL 17。非 PGVector 管理分页明确返回 501，管理员校验保留；通用 SDK list/search 没有扩大接口。

隔离回归还复现了另一个问题：推断写入的批量 insert 失败后，逐条 insert 也失败，却仍记录 ADD 历史并返回成功 ID。同步与异步路径现均抛出明确持久化错误，不再为失败批次生成虚假成功；报错说明可能已有部分写入，不能盲目重试。成功 fallback 仍正常工作。此缺陷可以解释“说保存了但没有记录”，不能据此断言现有 7 条历史缺口的原因。

## 实际验证

- 因网络不可用无法完整安装开发依赖，使用已有 `icarus-mem0:dev` 镜像及应用自有离线 pytest 依赖；源码只读挂载，测试显式启用 pytest-mock/asyncio 插件，不继承业务凭据。
- 聚焦契约、路由、存储、同步/异步写入共 **190 passed**，退出 0。先观察写入失败回归红灯，再修复至通过。
- 真实 SQL 测试使用临时容器 `icarus-memory-browser-test-20261106`、tmpfs、独立网络及数据库 `icarus_memory_browser_test`；构造 1005 条记录并对 10 组查询条件与参考实现比对，含空库。测试入口要求显式 `MEM0_TEST_POSTGRES_DSN`，未配置则跳过真实 SQL 测试。容器已停止并自动删除，用户 Mem0/PostgreSQL 服务未动。
- 完整保留套件：修改后 **40 failed / 1102 passed / 39 skipped / 177 errors**；临时恢复原始 Mem0 源码、排除本轮新增文件的基线为 **40 failed / 1095 passed / 37 skipped / 177 errors**。失败节点集合逐一比对相同，涉及未安装的可选 vendor 依赖和既有鉴权测试环境；不能宣称完整套件通过，也未修复无关问题。比对后已恢复全部修复代码。
- `scripts/test.sh` 默认契约门槛已纳入新增 route、写入和 SQL 测试入口；本次实际验证使用上述 Docker 等价 pytest 命令，未把 `make test-mem0(-full)` 记为通过。
- 没有 schema 迁移、embedding 重建或业务数据恢复。编译与最终 diff 检查见统一实施入口。


聚焦验收可按下列方式复现（先启动上述隔离 PG 容器，结束后停止同名容器；不使用业务数据库）：

```bash
docker run --rm --network container:icarus-memory-browser-test-20261106 \
  -v /mnt/f/project/Icarus/apps/mem0:/work:ro -w /work \
  -e PYTHONPATH=/work:/work/server:/work/.venv/test-deps \
  -e PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 -e MEM0_TELEMETRY=false \
  -e MEM0_TEST_POSTGRES_DSN=postgresql://postgres@127.0.0.1/icarus_memory_browser_test \
  --entrypoint python icarus-mem0:dev -m pytest \
  tests/memory/test_main.py tests/memory/test_storage.py tests/memory/test_write_acknowledgement.py \
  tests/test_dev_scripts.py tests/test_database_connection.py tests/test_memory_page.py \
  tests/test_memory_browser_route.py tests/vector_stores/test_pgvector.py \
  tests/vector_stores/test_pgvector_browser.py \
  -q -p no:cacheprovider -p pytest_mock -p pytest_asyncio.plugin
```

这里的 test-deps 为应用忽略目录中的本机离线 pytest 依赖，不作为生产依赖或跟踪产物；标准开发环境安装完成后直接使用应用私有环境即可。完整套件对照日志留在本机忽略目录，未将日志、数据库或运行凭据纳入改动。
