# 统一组件生命周期当前架构

- 日期：2026-10-19。
- 依据：根 `icarus.toml`、`scripts/icarus/`、`scripts/tests/` 和两个应用配置加载器/测试。
- 本文描述当前实现；目标与实施决定见 [design.md](design.md)，任务状态见 [plan.md](plan.md)，
  真实验证与已知基线见 [execution.md](execution.md)。运行示例见 [根 README](../../../README.md)
  和 [infra 运维说明](../../../infra/README.md)。

## 所有权与调用路径

根层是 Python 3.11+ 标准库控制面，不是共享应用环境或业务配置服务。
应用代码、Dockerfile、安装依赖、业务 JSON 与 app-only Compose 仍归 `apps/<app>/`；
独立中间件 Compose 归 `infra/<component>/`。应用只消费连接环境变量，不读取根 TOML、不 import 控制面。
没有远程部署、SSH、镜像推送、数据库迁移或凭据轮换能力。

```text
bin/icarus
  └─ scripts/icarus/main.py                  CLI / IcarusControl
       ├─ configuration.py → registry.py    纯配置加载、地址归一化、诊断
       └─ Lifecycle                         依赖预检、owner、就绪等待、失败汇总
            ├─ graph.py                     required 闭包、拓扑排序
            ├─ environment.py → injection.py  dotenv 合并、声明式子进程导出
            ├─ probes.py                    有界 HTTP / TCP 探测
            ├─ compose_runtime.py           项目/文件作用域、镜像、共享网络
            └─ process_runtime.py           Linux PID/监听身份、记录、日志、前台运行
```

没有旧硬编码组件表的回退路径。`main.py` 解析命令、构建适配器并委托 `Lifecycle`；不分支处理
具体数据库协议。Mem0/OpenKB 的 `compose_command` 保留应用自己的配置验证包装器；根层传递
`COMPOSE_PROJECT_NAME` / `COMPOSE_FILE`，动作仍是包装器首参数。

## 注册表与只读配置入口

`registry.py` 的 frozen dataclass 为 `Probe`、`Connection`、`Dependency`、`Component`、`Registry`。
`load_registry` 读取 base → 旁侧 `<stem>.local.toml` → 本次 overrides：表递归合并，标量/列表替换。
默认 base 为根 `icarus.toml`；`--config PATH` 选择其他 base，且不再叠加默认机器覆盖文件。
相对配置路径和注册表运维路径都以仓库根为基准。

严格校验 version=1、未知字段、类型、组件/owner 引用、环境变量名、无凭据 URL、相对路径越界及
已存在 symlink 逃逸。`env` 的凭据型变量名拒绝字面值，Secret 用 `secret_env` 引用变量名。
命令是 argv 数组，不是根层 shell 拼接。命令声明和 Compose 文件是否可执行/存在仍有运行时检查，
不能把 schema 校验等同于完整安装预检。

`configuration.load_configuration` 从 HTTP(S)/WS(S) endpoint 归一化 host/port，拒绝矛盾来源，
随后验证全部 enabled 组件的 required 执行图。optional 边不进入闭包/循环判定，remote 内部图不展开。
CLI 地址覆盖同时调整 URL/listener，不写回文件。

`config validate` / `config show [component]` 在创建 runtime 前返回。前者校验有效图，后者序列化
元数据和来源文件路径；不读 dotenv/Secret、不调用 Docker、不启动服务。当前没有图形配置 UI。

## 当前组件与依赖

| 组件 | 运行归属 | required 依赖 | optional 依赖 |
| --- | --- | --- | --- |
| gateway | 原生 process，根 workdir | agent | — |
| agent | embedded，owner=gateway | mem0、openkb | rela-mem |
| tui | foreground，调用者 Workspace | gateway | — |
| webui | 原生 process，workdir=apps/webui | — | gateway、mem0、openkb |
| mem0 | 应用 Compose，3 个容器（API/PG/Dashboard） | mem0-postgres | — |
| mem0-postgres | bundled，owner=mem0 | — | — |
| openkb | 应用 Compose，1 个容器 | — | — |
| rela-mem | app-only Compose，1 个容器 | postgres、falkordb | — |
| postgres / falkordb | 独立 infra Compose，各 1 个容器 | — | — |

七个 app 为 default_stack；middleware 自身不是默认根，通过 required 闭包加入。Argus 未登记。
Gateway/Agent、Mem0/private PG 是生命周期 owner 关系，不是反向 required 边。

`graph.topological_order` / `required_closure` 返回稳定、去重、依赖优先顺序，`reverse_order` 反转停止顺序。
embedded 节点的依赖在 owner 启动前预检，不等待尚未启动的 owner。bundled 节点归一到 owner，
本 owner 自带的 bundled 服务不是启动前置，而在 owner 就绪后探测。remote 组件或 remote owner
是不透明边界，不遍历其本地内部依赖，不安装本地内部软件、不注入 unused launch secrets。

## 环境与应用配置边界

`environment.merged_environment` 合并选定 dotenv 和进程，进程优先；`ICARUS_ENV_FILE` 替换而非追加
根 `.env`，相对路径以根解析，显式不存在的文件报错。绝对 `ICARUS_DATA_DIR` 在 runtime 创建时校验，
支持该路径中的 `${NAME}` 展开。各 standalone 应用仍保留自己的 dotenv 发现规则。

`build_child_environment` 是无 I/O 纯转换：

- `env` 导出公开静态值，`secret_env` 从合并后的 base 环境按 source 名解析；缺失 Secret 报变量名。
- host/port/endpoint 通过组件显式 `*_env` 导出；依赖边导出 endpoint 或数据库连接字段。
- 数据库密码的 `Connection.password_env` 只是绑定声明，不从 provider 或其他派生值推导密码。
- 同一变量的声明导出必须一致；与继承值不同则用派生值覆盖并发 names-only warning。
- local Compose consumer 与 local target **都声明 shared_network 且 network 相同**时，连接使用 target
  的 `network_host/network_port`；否则使用 host/port。embedded consumer 使用实际 owner 的运行形态。

`Lifecycle.child_environment` 合并 runtime 与其 hosted 组件的声明导出，冲突拒绝启动。这使 Agent 的
三个插件端点进入 Gateway，而不是创建额外 Agent 进程。Agent loader 仅覆盖
`runtime.plugin_config.{memory,knowledge,relationship}.endpoint`；不启用插件、不改模型/用户/知识库字段。
rela-mem loader 仅覆盖八个 PG/Falkor 连接字段；显式空值校验失败，端口经原有 Pydantic 校验。
显式 `environ` mapping 隔离进程/dotenv；未提供时沿用根 `.env` 的 `override=False`。
两者都不回写业务 JSON，既有 HTTPS/鉴权规则不变。

PostgreSQL 首次 bootstrap 使用 **provider 自己**的 `components.postgres.env`：
`ICARUS_POSTGRES_USER` / `ICARUS_POSTGRES_DATABASE`，以及引用的 `RELA_MEM_PG_PASSWORD`。
控制面不反向扫描第一个 consumer，不推断变量名；consumer 边另行声明其登录角色/数据库/schema。
已有数据库不会因环境改变而新建角色、迁移或旋转密码；remote 数据库须事先配置好访问权限。

## 生命周期与失败语义

`Lifecycle` 提供 install/start/stop/status/logs、child_environment、owner、run_foreground，
状态结果是 `StatusRow(component, mode, state, detail)`。

- `install`：本地原生/embedded 调应用安装脚本；Compose 按 `image_action=build|pull` 执行。
  `--dev` 只调 app-owned 开发安装命令，不构建镜像。既不创建根 `.env`，也不自动安装全局命令链接。
- 默认 start：default_stack 的 required 闭包，前台最后运行；required 不可达阻断依赖分支，optional
  只警告，其他分支继续，失败汇总为非零。显式后台组件同样满足 required 闭包。
- `tui` / 显式 `start tui` 只运行前台入口、检查依赖，不启动依赖。`endpoint_args` 是 foreground-only
  的 flag→已声明依赖映射；HTTP→WS、HTTPS→WSS，并只补一次 `/rpc`。用户显式 endpoint argv 优先，
  仅绕过该默认目标检查，前台保留调用者 cwd。
- 已 running/external/reachable 的服务复用并提示配置变化需重启；未知占用端口拒绝接管。
  新启动才校验 launch Secret，已有就绪 external 不需要 unused Secret。
- local process 同时要求受管监听身份和 probe；Compose 要求作用域内足够 running 容器、无 starting/
  unhealthy 标记且 endpoint probe 成功。等待默认 180 秒；不是 `up -d` 返回即成功。
- 显式 stop 只操作 named runtime，bundled 归 owner，embedded Agent 提示操作 Gateway；裸 stop
  反转默认 required 闭包并 owner 去重。不删数据、卷或共享网络。remote install/stop 明确跳过，start 只探测。
- status 展示 mode/address、运行事实和依赖降级；Compose 检查错误显示 unknown，不伪报停止/健康。
  可达与应用/认证 ready 分开；status 输出本身不以每行 ready 决定退出码。remote/foreground 无独立本地日志。

HTTP/TCP probe 在独立 spawn worker 中约束完整操作的总时限，包含 DNS、全部地址尝试、HTTP 响应头和持续滴流，并清理 worker/pipe；
响应默认 ≤65536 bytes，不跟随重定向、不继承代理，只传声明的 Token。
可匹配状态和扁平 JSON 子集，默认接受 200–399（包括未跟随的重定向响应）；不能将它解读为通用认证证明。
TCP 只建立连接，不验证数据库协议或密码。应用自身校验真实存储可用性。

## 运行时身份与安全限制

Compose 延迟检测 `docker compose` / `docker-compose`，操作绑定项目名与绝对 Compose 文件路径，
状态按两种标签筛选。仅 local shared-network up 检查/创建网络；安装、日志、停止不创建网络。
被动操作使用只供插值的占位值，绝不用于应用 startup。应用包装器继续校验自己的模型/API 配置。

原生过程记录在 `$ICARUS_DATA_DIR/runtime/<component>.json`，日志在 `logs/<component>.log`；
foreground 每 PID 一条记录。身份同时包含 repo、marker argv token、workdir、PID start ticks、地址，
并通过 `/proc` 检查自身/同组 listener。external 不被信号终止；orphaned 仅在 cwd/marker/listener
等匹配当前仓库后才可识别并安全收束。停止捕获并重新校验组成员身份，leader 先退出也不宽泛 kill；
残留身份不确定时保留记录、拒绝替换，要求使用原启动配置。

新的 CLI 实例可恢复经过身份/监听验证的临时启动地址供 status/stop 使用；同配置 dead leader 且无
未完成 termination members **也无存活进程组成员**的旧记录可重试。若 leader 已提前退出却存在未捕获的同组后代，则保留记录并明确拒绝 stop/replacement，要求人工检查，不自动收养或信号终止未知后代。不同配置、尚未监听或身份不确定时不承诺自动恢复。
**当前原生受管进程要求 Linux 可读 `/proc`；不是 macOS/Windows 原生管理支持声明。**

**旧 rela-mem 三合一 Compose 必须由操作者先停止，新旧项目不得同时挂载保留数据库目录。**
没有自动 active-mount 碰撞检查。拆分仍使用 `services/rela-mem/postgres` 与 `services/rela-mem/falkordb`，
不迁移、不清空数据。具体 cutover 和 remote 网络注意事项见 infra README。

## 验证与证据边界

根 functional tests 固定 registry 严格校验、graph owner/remote 语义、真实 HTTP/TCP 探测与进程身份、
环境冲突、Compose argv/作用域；`test_lifecycle_cli.py` 使用实际 CLI/测试子进程验证就绪、重复 start、
外部实例保护、临时端口恢复、前台新实例 status/stop 和配置来源。loader 测试验证连接以外字段不变。
Compose 单元边界测试不替代容器运行；daemonless `compose config` 也不替代真实存储验收。

控制器已另行执行真实生产 Gateway/WebUI CLI，以及隔离 PG17/FalkorDB 4.22.1/rela-mem 容器链路：
健康、认证拒绝、两个人物、档案、关系、墓碑历史、重启持久化和显式 app stop 保留数据库。
混合模式用原生 rela-mem 连接同一组测试数据库（登记为 remote），验证 remote stop 留库。
这是隔离外部依赖语义验证，不是用户指定数据库的认证/部署验收，也不是性能 benchmark。
本次自建容器/网络/唯一镜像已显式清理，临时数据保留；完整输出与基线差异由 execution 统一记录。
