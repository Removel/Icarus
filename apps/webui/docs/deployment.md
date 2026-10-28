# 单用户部署与运维

WebUI 的 Node 入口提供静态资源、同源代理和单用户 Basic 认证。后端仍独立部署，浏览器入口认证不构成后端多租户隔离。不要将 Mem0、OpenKB 或 Gateway 直接公开到互联网。

## 本机运行

在 `apps/webui` 中使用 Node.js 22.12+、pnpm 10.32.1：

```sh
pnpm install --frozen-lockfile
pnpm build
```

将 `.env.example` 复制为 `.env.local`，填写后端地址及凭据，然后启动：

```sh
node --env-file=.env.local server/index.mjs
```

默认打开 `http://127.0.0.1:8080`。`pnpm start` 直接读取进程环境；生产入口不会隐式加载仓库根 `.env`。开发模式 `pnpm dev` 才从仓库根目录读取 `ICARUS_` 配置，并监听 `127.0.0.1:5173`。Vite preview 仅用于构建预览，不提供生产认证与代理。

## 配置

| 变量 | 默认值或用途 |
| --- | --- |
| `ICARUS_WEBUI_HOST` | `127.0.0.1`；非回环地址必须设置入口账号密码 |
| `ICARUS_WEBUI_PORT` | `8080` |
| `ICARUS_WEBUI_ORIGIN` | 浏览器实际访问的完整 origin，例如 `https://icarus.example.com`；用于校验写入和 WebSocket，不含路径 |
| `ICARUS_WEBUI_USER` / `ICARUS_WEBUI_PASSWORD` | 配对设置的入口 Basic 账号密码；用户名不可含冒号 |
| `ICARUS_MEM0_ENDPOINT` | `http://127.0.0.1:8888` |
| `ICARUS_MEM0_API_KEY` | Mem0 管理 API Key；全列表访问要求管理员权限 |
| `ICARUS_OPENKB_ENDPOINT` | `http://127.0.0.1:7566` |
| `ICARUS_OPENKB_API_TOKEN` | OpenKB Bearer token |
| `ICARUS_GATEWAY_ENDPOINT` | `http://127.0.0.1:8765`；填写 HTTP(S) origin，代理负责升级连接 |

后端地址不支持路径前缀或内嵌用户名密码。服务凭据只存在于 Node 进程，入口的 Authorization、Cookie 和客户端伪造的服务凭据不会转发到后端。健康检查 `GET /health` 无需认证，只表示 WebUI 进程存活，不代表后端可用。

非本机部署应由 HTTPS 反向代理终止 TLS，保留浏览器的 `Host`、`Authorization`、`Origin` 以及 WebSocket 的 `Upgrade`、`Connection` 请求头。将 `ICARUS_WEBUI_ORIGIN` 配置为外部 HTTPS origin，并将 WebSocket 空闲超时设置为适合长对话的值。入口校验 Host，不信任 `X-Forwarded-*` 来决定允许来源。所有非 GET/HEAD API 请求及 WebSocket 都需匹配配置的 Origin；命令行验证写入时同样需要发送该请求头。

## 容器

构建上下文为 `apps/webui`：

```sh
docker build -t icarus-webui:local .
docker run --rm --env-file .env.local -e ICARUS_WEBUI_HOST=0.0.0.0 -p 127.0.0.1:8080:8080 icarus-webui:local
```

容器以非 root 用户运行，必须配置入口账号密码。容器内的 `127.0.0.1` 指向容器自身；后端地址需要使用同一 Docker 网络中的服务名，或 Docker Desktop 的 `host.docker.internal`。`.env` 不复制进镜像。镜像仅包含构建产物与 Node 入口，不包含应用源码、设计文档和后端数据。

## 验收

```sh
node --env-file=.env.local server/check-services.mjs
```

该命令只读取真实 Mem0 列表、OpenKB 库列表、Gateway runtime 状态，不打印业务数据或凭据，不调用模型。失败以非零状态退出。它不能替代以下隔离验收：使用专用测试用户、空测试知识库及临时 Agent 工作区，验证记忆创建、修正、历史、暂停恢复、删除和刷新持久化；验证知识导入与阅读；提交一条简短 Agent 消息，核对文本、工具状态、取消与重连。模型冒烟需已配置的有效凭据，结果与模拟响应回归分开记录。不要以生产资料作为测试数据。

## 升级和回滚

记录当前镜像标签或保留上一版 `apps/shell/dist` 和 `server`，固定锁文件安装并运行检查后构建新版本。完成后端备份后，在测试入口验证配置与关键流程，再切换服务并检查 `/health` 和真实服务检查命令。回滚时恢复上一版镜像或成对恢复静态产物与 Node 入口，保留外部环境配置。WebUI 不执行后端数据迁移；已发生的业务写入需遵循相应后端的恢复流程。

## 排障

| 现象 | 检查 |
| --- | --- |
| 启动拒绝非回环监听 | 配对设置入口用户与密码 |
| 页面 401 | 核对入口 Basic 凭据；浏览器可能缓存旧凭据 |
| 写入或对话 403 | 核对 `ICARUS_WEBUI_ORIGIN` 与地址栏 origin、反向代理是否保留 Host 和 Origin |
| API 502 | 检查后端进程、容器网络地址和端口；运行真实服务检查 |
| Mem0 401/403 | 检查服务管理 Key；全列表需要管理员权限 |
| 对话断线 | 检查 Gateway `/health`、WebSocket 转发和代理超时；客户端会恢复历史，不自动重发输入 |
| 发送结果未知 | 核对历史后再重试；重试复用 submission ID，幂等范围是同一 Gateway 进程，进程重启后不要盲目重试 |
| 记忆搜索缺少条目 | 列表最多读取 1000 条，筛选和排序仅覆盖已加载条目 |

HTTP 后端请求空闲超时为 5 分钟；长时间无输出的知识编译可能先在代理超时，遇到不确定结果应先查询服务状态，避免重复导入。入口使用 Basic 认证，浏览器负责保存凭据，没有应用内注销、多账号管理或权限分级。


## 真实服务验收与配置核对

自动化验收见 [test/integration/README.md](../test/integration/README.md)。默认 `pnpm test` 跳过真实服务测试；显式启用后创建唯一测试数据，结束时删除所建记忆和知识库。Gateway 使用临时工作区，会话历史保留用于核对。

Mem0 数据库 `config_overrides` 优先于容器默认值。即使环境写着 fastembed，也应通过受保护的 `GET /configure` 核对实际 embedder；误指向占位接口时，列表可读但新增、编辑和召回会失败。修正已有实例前需核对向量维数、模型和现有数据的兼容性。

OpenKB 的管理默认库模型与新建库模板是两个配置来源。Icarus 容器启动时将 `OPENKB_ICARUS_MODEL` 同步到服务器工作目录的 `config.yaml`，保留模板的其他配置，使 REST 新建库使用部署指定的模型。已有知识库的模型不会被启动脚本覆盖，需要通过 OpenKB 库配置接口修正。模型名称须与服务实际提供的名称一致；只改全局模型不能覆盖库内已有模型，HTTP 健康检查也不验证模型调用。

报告删除需要更新 OpenKB 的 `/api/v1/report/delete`；新记忆属性历史需要更新 Mem0 的 changes 存储。升级 Mem0 前备份 history.db，旧版降级可能丢弃新增审计列。记忆对话写入会使用 infer=true 调用 Mem0 的提取模型，管理页原文写入通过不能代替这条链路的验证。Windows Bash 需要 Git for Windows，Gateway 启动环境应包含 Git 的 PATH。


本次手动记忆修复需同时更新并重启 Agent Gateway。新增表单依赖 `memory.get_context`；旧 Gateway 返回方法不存在时，页面保留草稿并禁止保存，不回退到 Demo 身份。已保存的 `workspace:icarus` 不是实际工作区标识，旧的 `default` 用户也未必匹配当前配置；通过详情重新添加到正确归属后再处理旧记录。

若记忆已保存却未被对话引用，先核对所属用户、Agent、工作区、有效期，再查 Memory Region 的 `error`。自动召回包括服务查询和上下文确认，默认最多 5 秒（可配 1–30000 毫秒）；`timeout` 表示本次未及时取得内容，不表示记录不存在。真实验收命令可用 `WEBUI_LIVE_TESTS=1 WEBUI_LIVE_MODEL=1 pnpm test test/integration/test_live.py -k real_manual_memory`，验证浏览器新增、持久化、检索和新会话真实模型回答。
