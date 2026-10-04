# WebUI 交付架构

2026-10-01，依据当前 `apps/shell`、`packages`、`server` 及对应测试整理。

Shell 使用 hash 路由组合记忆、知识和对话 workspace 包。记忆和知识模块保持挂载以保留筛选、阅读与编辑状态；对话包首次进入时懒加载。共用 UI 包包含视觉组件、导航、未保存草稿保护和 Gateway 传输客户端；记忆配置读取与对话复用该客户端。各业务模块拥有自己的服务适配，不互相导入业务实现。

记忆通过 `mem0.ts` 调用同源 `/api/mem0`，`useMemories` 管理可取消的列表、详情、历史读取。页面使用服务 ID，写入由服务确认后回读或刷新；服务错误保留编辑草稿，批量操作逐条统计结果。新增表单独立为 `MemoryCreate`，通过 Gateway `memory.get_context` 获取配置中的身份，工作区范围复用服务端 `SessionIdentity`，不在浏览器重算路径散列；原文创建使用 `infer: false`，更新保留 metadata，暂停写过去的 expiration_date，恢复写 null。记忆列表最多加载 1000 条后在本地筛选排序。属性位于正文之前；历史按每页 10 条展示，读取仍为全量。Mem0 `changes` 提供分类和有效期前后值，旧数据保持通用更新标签；正文使用共同前后缀裁剪得到变化区段，全文按需展开。

知识包的 `openkb.ts` 对接 `/api/v1`。列表与图谱分别请求，图谱失败不阻断资料阅读；正文按需读取。知识页保存、资料导入、重编译、移除和检查继续调用 OpenKB。示例模式仅在开发环境按需导入，生产构建不包含示例数据与入口。Markdown 渲染、React Flow 布局均由前端完成。`KnowledgeQuality` 从 OpenKB `list.reports` 读取服务端档案，每页展示 10 份；通过 page 接口读取全文并在弹窗中展示或导出，通过 report/delete 确认删除。检查完成重新读取目录；浏览器不另存报告副本。报告是检查时的快照，不随库内容变化更新。

共用包的 `Gateway` 通过 `/rpc` WebSocket 管理 JSON-RPC 请求 ID、超时和关闭时的未完成请求；`useChatSession` 调用会话创建、列表、订阅、历史、提交和取消。先订阅，再按游标读取历史，同时缓存实时更新；历史与通知按 sequence 去重，完整消息替代同一步的 delta，迟到 delta 不覆盖完整消息。`updates.ts` 只解释公共 RuntimeUpdate，不依赖模型供应商协议。未知事件忽略。断线重新连接并读取历史，不自动重发输入；用户显式重试复用 submission_id。`ChatApp` 负责会话侧栏与输入布局，`ChatTranscript` 负责消息展示与跟随滚动；读取历史时停止自动跟随，并提供回到最新消息按钮。

`server/index.mjs` 使用 Node 原生 HTTP/HTTPS 实现静态资源、HTTP 流式转发和 WebSocket 隧道。它把 Mem0 路由前缀去掉，保留 OpenKB `/api/v1` 与 Gateway `/rpc`。后端令牌只从进程环境读取，在服务端加入；客户端入口凭据与 Cookie 被移除。非回环监听必须启用 Basic 认证，写入与 WebSocket 校验配置的 Origin。静态路径解析后验证真实路径属于构建目录，防止越界或符号链接逃逸。`/health` 是进程存活探针，`check-services.mjs` 另行检查真实后端读取接口。

测试与源码层次对应：`test/packages/memory_app` 使用有状态 HTTP 响应验证服务契约及刷新；`test/packages/chat_app` 使用可控 WebSocket 验证公共协议、流式合并、分页、取消、错误与重连；`test/server` 启动真实 Node 入口和隔离 HTTP/WebSocket 对端验证代理与访问控制。这些测试不声称验证真实模型或真实后端存储。生产 Dockerfile 构建静态产物后仅复制产物与 Node 入口，CI 运行应用回归、lint、format、类型、构建和 diff 检查。

知识库子导航常驻外壳；知识库选择与名称搜索框限制宽度。质量检查操作位于选择行下方，报告标题、空状态与报告列表独立展示；离开质量页时关闭报告弹窗的可见性。
