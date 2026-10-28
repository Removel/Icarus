# rela-mem

独立人物关系与档案服务。Mem0 记录事件/经历；rela-mem 保存人物身份、圈子、关系和档案。分类由调用方负责，本服务不运行 LLM，不导入 Agent/Gateway/Argus。

## 统一启动

从仓库根使用 `./bin/icarus`；短命令 `icarus` 需要先安装命令链接。根控制面需要 Python 3.11+，
本地容器需要 Docker Compose；独立本机开发环境需要 Python 3.12。

复制 `settings.example.json` 到自己的部署配置，修改 `identity.user` 与 `identity.agent`。
示例身份不是任何实际用户的部署默认值；已有特殊身份冲突会拒绝启动，不能靠改配置重绑身份。
在仓库根 `.env` 或进程环境填写（不要提交真实值）：

```dotenv
ICARUS_DATA_DIR=/absolute/path/to/icarus-data
RELA_MEM_API_TOKEN=<nonempty-token>
RELA_MEM_FALKORDB_PASSWORD=<nonempty-password>
RELA_MEM_PG_PASSWORD=<nonempty-password>
# 可选；不设置时挂载 settings.example.json，不要设置为空的路径覆盖。
# RELA_MEM_SETTINGS_FILE=/absolute/path/to/settings.json
```

```bash
./bin/icarus config validate
./bin/icarus config show rela-mem
./bin/icarus install rela-mem       # 构建应用镜像，拉取 required 本地数据库镜像；不启动
./bin/icarus start rela-mem         # 自动先启动 required postgres/falkordb，再等待应用就绪
./bin/icarus status rela-mem
./bin/icarus logs rela-mem --no-follow
./bin/icarus stop rela-mem          # 只停应用，不停共享数据库
```

`config` 命令不需要 Docker/凭据，不解析 Secret 值。实际输出包含 `configuration valid (10 components)`
（默认注册表）和带来源路径的有效元数据；运行状态按环境报告，不能把配置校验当作数据库连通验收。
默认本地端口是应用 `8767`、FalkorDB `16379`、PostgreSQL `15432`，均绑定 loopback。
统一入口通过注册表注入监听与连接字段；不要再用旧 `RELA_MEM_DB_BIND_ADDRESS` 管理拆出的数据库。

已有远程数据库在 ignored `icarus.local.toml` 中设置 `components.postgres` / `components.falkordb`
的 `mode = "remote"`、`host` 和 `port`。可复制根 `icarus.local.toml.example`；它默认是 remote 隧道示例，
全本地部署请删除这两个覆盖表。remote 只探测，不创建、安装或停止远程数据库。
PostgreSQL 角色/数据库及权限必须预先存在；应用可初始化自己的 schema/表，但不创建远端登录角色或数据库。
密码仍来自环境。原生进程可用宿主机隧道；容器必须使用容器可达地址，不能把容器内 `localhost` 当作宿主机。
完整工作流见 [infra 运维说明](../../infra/README.md)。

**迁移部署方式前，必须手动停止旧三合一 Compose 项目；新旧容器不能同时挂载相同数据库目录。**
没有自动挂载碰撞保护，不做自动数据迁移。独立数据库保留原路径：

```text
$ICARUS_DATA_DIR/services/rela-mem/falkordb
$ICARUS_DATA_DIR/services/rela-mem/postgres
```

根 `infra/` 分别管理数据库；`apps/rela-mem/docker-compose.yaml` **只含应用服务**，加入共享外部网络。
统一入口会创建需要的网络并注入 `postgres:5432`、`falkordb:6379`；身份等业务 JSON 不被改写。
镜像默认本地构建为 `rela-mem:dev`，可用 `RELA_MEM_IMAGE` 选择独立标签，不自动推送。
`stop` / Compose `down` 不删除上述 bind mount 数据；裸 `icarus stop` 会停止默认 required 闭包中的本地数据库。

### 手动 app-only Compose

仅在自行管理数据库/网络时绕过根控制面。除上面的三个 Secret、配置挂载和数据目录外，必须显式提供下面表中
**全部八个连接变量**；此 Compose 文件不自动启动数据库，也不会从 JSON 填补缺少的 Compose 插值。
先准备 `${ICARUS_NETWORK:-icarus}` 外部网络（或复用统一入口已创建的网络），再从仓库根执行：

```bash
docker compose --env-file .env -f apps/rela-mem/docker-compose.yaml up -d --build --wait
```

本地共享网络使用 `postgres:5432` / `falkordb:6379`；远端则填容器可达的 host/port。
不要将未加 TLS 的数据库暴露到公网。应用健康检查为 `/healthz`，业务接口仍需 Token。

## 本机开发

```bash
bash apps/rela-mem/scripts/install.sh
```

设置 `RELA_MEM_CONFIG` 指向自己的 JSON。`settings.example.json` 的数据库默认值为
`127.0.0.1:16379` / `127.0.0.1:15432`，适合本机连接默认暴露端口；独立进程的监听地址使用 JSON
`server.host/port`（示例监听 `0.0.0.0`，本机开发可改为 `127.0.0.1`）。
`RELA_MEM_BIND_ADDRESS` / `RELA_MEM_PORT` 是 Compose 的发布端口设置，不是 standalone loader 的监听覆盖。
`RELA_MEM_SETTINGS_FILE` 仅是 Compose 挂载源路径；应用内部使用 `RELA_MEM_CONFIG`。
应用在仓库内开发时加载根 `.env`（`override=False`）；容器只读取注入环境和挂载配置。
应用不读取根 TOML，也不导入控制器模块；原生**受管**进程的身份/停止实现当前限 Linux `/proc`。

连接环境变量优先于 JSON；未设置的字段保留文件值，再使用原有模型默认值。支持：

| 环境变量 | JSON 字段 |
| --- | --- |
| `RELA_MEM_PG_HOST` / `RELA_MEM_PG_PORT` | `postgres.host` / `postgres.port` |
| `RELA_MEM_PG_DATABASE` / `RELA_MEM_PG_USER` | `postgres.database` / `postgres.user` |
| `RELA_MEM_PG_SCHEMA` | `postgres.schema` |
| `RELA_MEM_FALKORDB_HOST` / `RELA_MEM_FALKORDB_PORT` | `falkordb.host` / `falkordb.port` |
| `RELA_MEM_FALKORDB_GRAPH` | `falkordb.graph` |

显式设置为空的连接变量会校验失败，不回退到文件；端口由现有 Pydantic 模型转换为整数并检查 `1..65535`，错误显示不包含输入值。Python 调用 `load_settings(path, environ=mapping)` 时只读取该映射，不合并进程环境，也不加载 `.env`。身份、图后端实现与其他业务配置仍由应用自身管理。

Agent 独立支持 `ICARUS_MEM0_ENDPOINT`、`ICARUS_OPENKB_ENDPOINT`、`ICARUS_RELATIONSHIP_ENDPOINT`，仅覆盖对应插件的 `endpoint`，不会改写业务字段或将插件加入启用列表；非 loopback 的知识/关系服务仍须遵循既有 HTTPS 与鉴权规则。

```bash
apps/rela-mem/.venv/bin/python apps/rela-mem/scripts/manage.py check-config --config /absolute/path/to/settings.json
apps/rela-mem/.venv/bin/python apps/rela-mem/scripts/manage.py serve --config /absolute/path/to/settings.json
# 或设置 RELA_MEM_CONFIG 后在 apps/rela-mem 下运行
.venv/bin/uvicorn main:app --app-dir src --host 127.0.0.1 --port 8767
```

`src` 直接是 import root，测试镜像相同层级，没有 `src/rela_mem` 包层。每个应用使用独立环境以避免 `config` 等通用模块名相互冲突。

初始化在应用 lifespan 中完成；独立脚本也可重复执行：

```bash
apps/rela-mem/.venv/bin/python apps/rela-mem/scripts/init_graph.py
apps/rela-mem/.venv/bin/python apps/rela-mem/scripts/init_postgres.py
```

不做自动数据迁移、不清理已有数据。特殊身份冲突或图约束验证失败会拒绝启动。

## REST 使用

除 `/healthz` 外，所有 API（包括 `/openapi.json`）均需要 `Authorization: Bearer <token>`。默认不公开 Swagger 页面。

```bash
curl http://127.0.0.1:8767/healthz
curl -H "Authorization: Bearer $RELA_MEM_API_TOKEN" http://127.0.0.1:8767/openapi.json
```

端点覆盖人物、别名、精确身份解析、档案/历史/关键词搜索、圈子/成员、关系与最短路径。详细字段由 OpenAPI 提供；背景设计见 [design](docs/spec/2026-10-18-rela-mem/design.md)。

- 姓名 `name` 别名允许重名，用 `/v1/persons?query=小张` 返回候选。
- `/v1/identity/resolve?namespace=qq&value=123456` 只查精确身份，没找到返回 `200 {"person":null}`。
- 档案 key 不限制语义，只限制小写 ASCII、下划线与点分格式。`GET /v1/persons/{id}/profile?q=猫罐头` 搜当前有效档案的 key 和 JSON 文本。
- 写档案只追加，同一 key 按 `recorded_at DESC,id DESC` 取最新；回填时间须有时区且不能在未来。
- 删除档案用 `DELETE /v1/persons/{id}/profile/{key}?source=agent&source_ref=...`；追加 `deleted` 历史，不物理擦除。合法 JSON null 与删除不同。
- `source_ref` 是来源引用，不是幂等键。写超时不能盲目重试；重复请求可能增加历史或覆盖期间的新值。
- `a → b` 表示“a 是 b 的 xxx”。双向关系必须填 `forward_detail` 与 `reverse_detail`，内容更新也必须同时提交两者；服务规范化双向端点但不改变语义。
- 关系查询保留存储 `direction=one_way|two_way`，另用 `relative_direction` 和 `detail` 提供查询视角。
- 最短路径按无向连接遍历 active 关系，不推导业务关系；最多 5 跳且有 5000 节点/邻接行预算，超额明确报错。
- 人物/圈子归档不删除历史。单用户、静态 Bearer Token，不实现 RBAC、多租户、PII 加密、硬删除/合并。

## 测试

```bash
make test-rela-mem                 # 无数据库的单元/HTTP 契约/脚本测试
make test-rela-mem-integration     # 含真实数据库测试
```

集成测试需要**独立**的本机实例，不得指向用户数据库：

- FalkorDB：`127.0.0.1:26379`，密码 `rela-test-graph-password`；
- PostgreSQL：`127.0.0.1:25432`，用户 `rela_mem`、数据库 `rela_mem_test`、密码 `rela-test-pg-password`。
- 可用 `RELA_TEST_GRAPH_PORT` / `RELA_TEST_PG_PORT` 调整测试端口；每个测试创建随机测试图和 schema，结束清理自己的对象。

只读真实 HTTP 冒烟：

```bash
apps/rela-mem/.venv/bin/python apps/rela-mem/scripts/smoke_rest.py --url http://127.0.0.1:8767
```

明确向测试服务写种子（记录会保留，脚本不删除数据）：

```bash
apps/rela-mem/.venv/bin/python apps/rela-mem/scripts/seed_demo.py --url http://127.0.0.1:8767 --allow-write
```

Agent relationship 插件已通过 HTTP 接入；根统一生命周期也已登记 rela-mem。
本应用仍不导入 Agent，不提供 Argus/WebUI 集成，不负责镜像发布。
关系插件与存储的共同边界见 [跨应用约定](../../spec/2026-10-19-agent-relationship-integration.md)。
