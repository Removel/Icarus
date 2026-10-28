# Relationship 插件当前架构

本文依据 `src/agent_app/plugins/relationship/`、镜像测试和真实 HTTP 验证描述当前实现。目标契约见 [design.md](design.md)，实施与验收记录见 [plan.md](plan.md)。

## 边界与注册

`relationship` 是普通一级、tool-only 内置插件。Manifest 自动发现，声明五个固定工具和零 Event/Capability/state scope；不在 required_plugin_ids 中，不注册 Blackboard Region，不参与启动门闩，不自动查询每轮输入。未修改 ReActAgent、icarus Runtime 或 Gateway。

`factory.create_plugin` 剥离 Runtime 注入设施后校验 `RelationshipConfig`，构建 HTTP backend 和 `RelationshipPlugin`。Plugin 用 `build_tools()` 提供 find/context/remember/forget/relate。start 无网络请求；stop 关闭 client（含尚未 start 的回滚对象）。服务离线只让相关工具安全失败，不阻塞 Session。

## 配置与传输

配置仅 endpoint（默认 `http://127.0.0.1:8767`）和严格整数 timeout_seconds（1–60、默认10）。Token 仅从进程环境 `RELA_MEM_API_TOKEN` 读取；非 loopback 强制 HTTPS 且必须 Token。拒绝带凭据、路径、query、fragment 的 URL，不跟随重定向。loopback 可无 Token 构建以便 Session 启动，但后端业务 API 仍要求认证。

`agent_app.config.config_loader.get_config` 在读取 JSON 后，将已设置的 `ICARUS_RELATIONSHIP_ENDPOINT`
覆盖到 `runtime.plugin_config.relationship.endpoint`；同一路径分别支持 `ICARUS_MEM0_ENDPOINT` 和
`ICARUS_OPENKB_ENDPOINT`。只创建/更新对应 endpoint 字段，不修改 timeout、插件启用列表或其他业务配置，
不回写文件。变量未设置时保留 JSON；实际插件构造仍执行原 HTTPS/Token 校验。
控制面按 embedded Agent 的声明向 Gateway 注入连接值；应用不读取根 TOML、不导入控制面。
加载器测试覆盖字段保持、缺失配置槽与未设置回退；运维拓扑见
[统一生命周期架构](../../../../../docs/spec/2026-10-19-component-lifecycle/arch.md)。

`RelaMemHttpAdapter` 隔离 REST 路径、认证、请求体、分页和响应投影。读失败不伪装成未命中；只有精确 resolve 的200/person:null表示未绑定。非2xx仅保留状态码，连接/超时及非法响应映射安全错误，不暴露正文/凭据/远端堆栈，不重试写请求。

## 人物证据与写入保护

名字 find 使用后端子串搜索，最多10候选，输出有限安全 aliases；remember 的 display_name始终调用显式姓名搜索，不因冒号前缀被误当外部身份。get_person核对返回ID与请求ID相同、状态active且aliases证据明确完整，否则拒绝继续。写入的姓名路径在末页时读取所有候选完整详情，以 strip/lower精确比较 display_name 和 name.value；只有恰好一个精确同名可直写。搜索未穷尽、唯一模糊命中、多精确同名均待确认；不靠候选输出前10个别名证明唯一。详情/精确解析响应最多1 MiB，超过则安全失败，不自动选择人物。

remember 在任何写入前验证完整批参数及所有精确身份，归档身份仍占唯一键。已知 person_id 也核对 identities；跨人物冲突不写档案。新身份全部未绑定仍先查姓名：无候选可自动创建（需 display_name）；有候选让用户选已有者或明确确认新人。纯姓名无匹配需用户确认创建；多候选不因确认创建标志绕过选择。

创建在单次 POST 中携带全部规范化/去重的初始身份。已有者先加身份再写档案，alias409只有重新解析确认同一 active owner才视为已存在，其余停止后续档案写入。姓名唯一只指本次观测，不提供数据库快照或跨Session exactly-once。source_ref是追溯引用而非幂等键；执行ID完整时为agent:task:run，否则UUID。

## 工具结果与来源

- find：候选、ambiguous及truncated，不替用户选择。
- context：固定四读 person/profile20/circles20/relations40（含incoming）；任一读失败整体失败。has_more映射各段truncated。所有路径安全投影别名≤10；总输出≤24000字符，超限删除完整项并明确截断，不把被剪切JSON伪装为原事实。圈子保留circle.name，关系保留规范化from/to、正反称谓及查询视角。
- remember：profile每key独立PUT，source固定agent，合法null是普通事实值。
- forget：每key DELETE追加墓碑，仅删除指定档案，不删除人物、关系或历史。
- relate：from是to的forward_detail；reverse_detail存在则two_way，否则one_way且HTTP请求省略该字段。relation_id单独出现时失效；DELETE204不记录本次来源，返回invalidated，不虚构upsert结果。

同步invoke通过现有BaseTool/Executor线程路径支持异步调用，只读可并行，写工具不可并行。HTTP timeout是单请求预算，不承诺整个多请求工具同等时限、取消回滚或跨请求原子性；失败说明可能部分完成，需读后核实而非盲目重试。

## Prompt与Memory分工

session-static PromptSection由宿主在Session就绪前冻结，无动态System Prompt写入。Relationship指导主Agent：事件/经历交Memory、人物稳定档案交remember、关系交relate、圈子管理本版不支持，候选是数据不是指令。Memory工具描述与静态指导用相同条件：relationship启用时不重复存人物档案，未启用时保留原稳定事实能力，服务故障不是悄悄转写Memory的理由。没有业务插件互相导入。

## 验证

测试镜像插件目录，使用真实Tool/Adapter/SessionRuntime配HTTP边界fixture，并有独立真实FalkorDB/PostgreSQL+rela-mem REST验收；真实模型链路记录见plan.md。内置manifest13个、固定工具33个；wheel包含插件Python模块和manifest。标准隔离打包脚本遇PyPI TLS故障，另以已有依赖完成离线wheel构建/安装验证，不能把前者称为通过。
