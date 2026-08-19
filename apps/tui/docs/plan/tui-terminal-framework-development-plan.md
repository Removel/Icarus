# TUI Terminal Framework Development Plan｜TUI 终端框架开发计划

> 历史计划：本文记录已经完成的第一阶段 `prompt_toolkit + Rich` 终端框架。当前 Textual
> 全屏 TUI 的实施依据是 `apps/tui/docs/plan/textual-tui-development-plan.md`。

## 目标

基于 `apps/tui/docs/arch/tui-terminal-framework-design.md`，把当前标准库串行 REPL
升级为可安装、保留终端原生 scrollback 的 Icarus 终端应用：

```text
global icarus command
→ Rich welcome
→ prompt_toolkit multiline input
→ AgentRuntimeService
→ live OutputEventSubscription
→ Rich streaming Markdown and tool status
```

本计划只实现终端框架第一阶段。它不实现 Agent 运行期间输入、TUI 本地消息 FIFO、队列
展示、任务取消和运行时状态机。

## 实施状态

- 全局 `icarus` console script 与 wheel package data：已完成；
- `prompt_toolkit` 多行输入和按键契约：已完成；
- Rich 欢迎页、工具状态与流式 Markdown：已完成；
- 串行 REPL 拆分、长期实时订阅和 Workspace 透传：已完成；
- TUI 测试：`27 passed`；
- Agent 全量回归：`294 passed`；
- 隔离 wheel 安装与仓库外 `icarus --help`：已通过；
- 真实模型 Markdown 与 `read` 工具 PTY 冒烟：已通过；
- TUI 本地队列和任务取消：按范围保留到后续状态机阶段。

## 文档归属与配套边界

本需求的产品入口和主要修改都属于 `apps/tui`，所以设计和计划保存在 TUI 应用目录。

全局安装需要根目录打包元数据；安装后的默认 Agent 配置可能需要一个最小配套调整。
这些是 TUI 启动能力的依赖，不把本需求升级为根 `spec/` 中的不可拆分跨应用设计：

- TUI 行为、模块和测试由本计划定义；
- 根目录 `pyproject.toml` 只提供 Monorepo 的安装入口和依赖清单；
- `apps/agent` 若调整配置资源读取，保持通用 `get_config()` 契约，并在 Agent 自己的测试中
  验证；
- 不修改 Agent Core、Plugin Runtime 或 Event 数据流。

## 实施前基线

开始实现前的现状：

- `apps/tui/main.py` 同时承担 CLI、标准输入和串行事件循环；
- `apps/tui/renderer.py` 使用 `TextIO` 直接输出纯文本；
- TUI 已使用 `AgentRuntimeService.subscribe_events()` 创建独立实时订阅；
- 当前订阅纯实时、无回放，因此必须在提交前创建；
- `apps/agent/requirements.txt` 尚未包含 `prompt_toolkit` 和 Rich；
- 根目录尚无 `pyproject.toml` 和 `icarus` console script；
- `apps/agent/settings.json` 仍由源码相对路径读取；
- 当前工作环境未安装 `prompt_toolkit` 和 Rich。

旧的 `apps/tui/docs/plan/repl-tui-development-plan.md` 描述已经落地的标准库 MVP。
本计划是后续 TUI 框架升级的实施依据，不回写旧计划来伪装其历史决策。

## 实施原则

- 先固定安装和资源读取，再迁移交互层；
- `apps/tui` 只依赖 `AgentRuntimeService` 的公开接口；
- 输入、对话编排和渲染分文件维护；
- 保留普通 screen 和原生 scrollback，不使用 alternate screen；
- 第一阶段仍然严格串行：当前任务收到 `InputFinishedEvent` 后才读取下一条输入；
- `Enter` 提交，`Ctrl+J` 是跨终端稳定的换行键；
- `Shift+Enter` 在终端能提供独立序列时支持，不能在相同回车字节上伪造区分；
- 原始多行 Prompt 保留内容和缩进，只有空输入与退出判断使用 trim 视图；
- Renderer 不展示隐藏 reasoning，不完整展开 ToolResult；
- 不顺带实现本地队列、取消、历史恢复或无调用方的通用 UI 抽象。

## 实施顺序

```text
打包与配置资源
→ prompt_toolkit 输入组件
→ Rich Renderer
→ 串行 REPL 拆分与集成
→ console script / Workspace 验证
→ 文档与全量回归
```

## 阶段一：建立可安装入口和稳定配置资源

### 任务一：增加根目录 Python 打包配置

**新增文件**

- `pyproject.toml`

**更新文件**

- `apps/agent/requirements.txt`

**开发内容**

- 使用一个明确的 PEP 517 build backend；
- 发现并安装 `apps` namespace 下的 Agent 和 TUI Python 模块；
- 声明现有 Agent 运行时依赖；
- 新增并固定兼容范围：`prompt_toolkit`、`rich`；
- 注册 console script：

```toml
[project.scripts]
icarus = "apps.tui.src.main:main"
```

- 把 `apps/agent/settings.json` 声明为 package data；
- 保留 `apps/agent/requirements.txt` 作为当前开发环境安装入口，其运行时依赖版本范围与
  `pyproject.toml` 一致；
- 不创建指向本地仓库绝对路径的 Shell alias 或包装脚本。

**验证**

- 构建元数据可以被 Python 解析；
- editable install 后命令环境中存在 `icarus`；
- 安装包中包含 Agent、TUI 和 `settings.json`；
- `icarus --help` 不要求当前目录是 Icarus 仓库。

### 任务二：把默认 settings 改为包资源读取

**更新文件**

- `apps/agent/src/model_config/config_loader.py`
- `apps/agent/test/model_config/test_config_loader.py`

**开发内容**

- 使用 `importlib.resources.files("apps.agent").joinpath("settings.json")` 或等价
  标准库方式读取默认 JSON；
- 使用资源 API 打开文本，不把 `Traversable` 强制转换成真实文件系统路径；
- 继续让环境变量覆盖 API Key 和 `ICARUS_DATA_DIR`；
- 保持 `get_config() -> ConfigModel` 和现有字段语义不变；
- 为测试提供最小的资源读取注入点，避免继续 monkeypatch 整个 `Path` 类型；
- 不在该任务中新增用户级 config 文件、CLI 配置参数或配置优先级系统。

**验证**

- 源码运行可以读取默认配置；
- 安装环境可以读取 package data；
- API Key 和数据目录仍由环境变量覆盖；
- 配置内容无效时继续由 `ConfigModel` 给出明确校验错误。

### 阶段一检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/model_config -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

完成条件：从非源码目录导入已安装包时，默认配置资源仍然可读，且 `icarus` 入口可解析。

## 阶段二：实现独立输入组件

### 任务三：定义输入边界和按键契约

**新增文件**

- `apps/tui/src/input.py`
- `apps/tui/test/test_input.py`

**开发内容**

- 建立一个职责单一的输入类，例如 `PromptInput`；
- 内部持有 `prompt_toolkit.PromptSession`；
- 对 REPL 暴露异步接口，例如：

```python
async def read(self) -> str:
    ...
```

- 启用多行缓冲和 bracketed paste；
- `Enter` 接受当前缓冲；
- `Ctrl+J` 插入换行；
- 对终端可识别的独立 `Shift+Enter` 序列注册换行绑定；
- 左右键按字符移动，上下键在当前多行文本内移动；
- `Ctrl+C` 放弃当前缓冲，并让下一次读取返回全新空缓冲；
- 空缓冲上的 `Ctrl+D` 产生 EOF；
- 不启用跨轮 Prompt 历史，避免与 Blackboard 业务 History 或未来 TUI 消息队列混淆；
- 允许测试注入 pipe input 和 dummy output；
- 不导入 `AgentRuntimeService` 或 Renderer。

**终端兼容实现约束**

- 先用小型探针验证目标 `prompt_toolkit` 版本对增强键盘协议和原始序列的支持；
- 如果目标库不能可靠识别 Shift+Enter，只保留真实可验证的最佳努力绑定；
- 禁止把普通 Enter 的 `\r` 同时绑定为换行和提交；
- 欢迎页始终把 `Ctrl+J` 作为可靠 fallback。

**测试用例**

- 输入普通文字后 Enter 返回一次完整字符串；
- Ctrl+J 产生单个换行，随后 Enter 一次提交完整多行字符串；
- 受支持的 Shift+Enter 序列行为与 Ctrl+J 一致；
- 左右键能在行内插入内容；
- 上下键能在多行缓冲中移动并修改对应行；
- bracketed paste 的多行文本只提交一次；
- Ctrl+C 丢弃旧缓冲，下一次输入没有残留；
- Ctrl+D 在空缓冲上退出。

### 阶段二检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_input.py -q
git diff --check
```

完成条件：输入组件在不启动 Agent Runtime 的情况下，能够独立验证全部最低按键契约。

## 阶段三：实现 Rich 输出和流式 Markdown

### 任务四：重构 Renderer 接口

**更新文件**

- `apps/tui/src/renderer.py`
- `apps/tui/test/test_renderer.py`

**开发内容**

- 让 `ReplRenderer` 依赖可注入的 Rich `Console`，不再直接拼接 ANSI；
- 提供明确方法边界：

```python
show_welcome(workspace_path)
show_user_message(prompt)
render_event(event)
finish_turn()
close()
```

- `show_welcome()` 输出产品名、绝对 Workspace、Enter / Shift+Enter / Ctrl+J 和
  Ctrl+D 提示；
- `show_user_message()` 保留多行输入和代码缩进；
- 普通测试 Console 固定宽度并关闭颜色，确保断言稳定；
- 保留中文 JSON 参数的 `ensure_ascii=False` 语义；
- 工具结果默认只显示工具名、紧凑参数、成功或失败摘要。

**验证**

- 欢迎页只包含必要信息，不清屏；
- Workspace 不受安装目录影响；
- 多行用户消息结构可读；
- 工具开始、成功、失败和 AgentError 可读；
- 大 ToolResult 不出现在默认输出。

### 任务五：实现流式 Markdown 段状态

**更新文件**

- `apps/tui/src/renderer.py`
- `apps/tui/test/test_renderer.py`

**开发内容**

- 在 Renderer 内只维护 UI 必需的“当前 Markdown 段”状态；
- 连续 `AgentTextDeltaEvent` 追加到同一个原始缓冲；
- 使用 Rich `Live` 在普通 screen 刷新 `Markdown(buffer)`；
- `transient=False`，固化后把最终段保留在 scrollback；
- 给刷新增加合理节流，避免每个高频 token 都做昂贵全量终端重绘；
- 工具开始、工具完成、错误和 InputFinished 前先固化当前段；
- 工具事件后的文字创建新 Markdown 段；
- `finish_turn()` 和 `close()` 幂等，确保异常退出也恢复终端状态；
- 不显示 reasoning delta，也不根据工具事件自行生成模型叙述。

**测试用例**

- 多个 Delta 的最终纯文本只出现一次；
- 粗体、列表和代码块跨 Delta 后最终结构正确；
- 不完整 Markdown 中间态不会导致 Renderer 崩溃；
- 文本、工具、文本形成两个已固化 Markdown 段；
- InputFinished 固化最后一段；
- AgentError 在新行展示；
- 未知 Event 不打断已有段；
- `finish_turn()` 和 `close()` 重复调用安全。

### 阶段三检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_renderer.py -q
git diff --check
```

完成条件：Renderer 的最终输出顺序和内容可稳定测试，并保留在普通终端历史。

## 阶段四：拆分并迁移串行 REPL

### 任务六：抽出串行对话循环

**新增文件**

- `apps/tui/src/repl.py`

**更新文件**

- `apps/tui/src/main.py`
- `apps/tui/test/test_repl.py`

**开发内容**

- 将 `run_repl()` 从 `main.py` 移入 `repl.py`；
- 通过构造参数注入输入组件和 Renderer，测试不依赖真实终端；
- 保持生命周期顺序：

```text
service.start()
→ service.subscribe_events()
→ renderer.show_welcome()
→ input.read()
→ renderer.show_user_message()
→ service.submit()
→ subscription.next_event() until matching InputFinishedEvent
```

- 输出订阅在第一次提交前创建，并贯穿整个 REPL Session；
- 只把当前 `accepted.task_id` 的 Event 交给 Renderer；
- 空输入不提交；
- 使用 trim 后视图识别 `exit` / `quit`，其余 Prompt 保留原始文本和多行缩进；
- EOF、exit 和 quit 正常结束；
- 输入阶段 Ctrl+C 由输入组件清空缓冲，REPL 继续读取；
- `finally` 中先关闭订阅，再停止 Service；
- 消费异常或渲染异常继续向上层传播，但清理不能跳过；
- 不增加本地消息队列，不在 Agent 运行时调用 `input.read()`。

**测试用例**

- Service 在欢迎页前启动，订阅在首次 submit 前创建；
- 欢迎页每个进程只展示一次；
- 多轮输入严格串行；
- 原始多行 Prompt 完整传给 `submit()`；
- unrelated correlation Event 不展示；
- 失败任务收到 InputFinished 后可以进入下一轮；
- 空输入、exit、quit 和 EOF 不提交；
- 读取、提交、消费和渲染任一步失败时都关闭订阅并停止 Service；
- TUI 不维护或回传业务 History。

### 任务七：精简 CLI 入口并固定 Workspace

**更新文件**

- `apps/tui/src/main.py`
- `apps/tui/test/test_cli.py`（新增）

**开发内容**

- `parse_args()` 继续支持 `--session-id`；
- `async_main()` 在进入任何异步或可能改变环境的逻辑前捕获
  `Path.cwd().resolve()`；
- 以该绝对路径创建 `AgentRuntimeService`；
- 创建 Rich Console、Renderer 和 PromptInput；
- `main()` 使用 `asyncio.run()`；
- 正常退出返回 `0`，启动或未捕获错误返回 `1`，顶层 KeyboardInterrupt 返回 `130`；
- 错误摘要写 stderr，不默认展开完整 traceback；
- `--help` 不能初始化 Agent Runtime 或要求环境变量。

**测试用例**

- 从临时目录调用 `async_main()` 时 Service 收到该目录的绝对路径；
- `--session-id` 正确透传；
- `--help` 成功且无 Service 副作用；
- 顶层错误退出码稳定；
- console script 指向 `apps.tui.src.main:main`。

### 阶段四检查点

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_repl.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_cli.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
git diff --check
```

完成条件：新输入和 Renderer 已接入现有 Service，且串行 Event 消费语义没有变化。

## 阶段五：安装和真实终端验收

### 任务八：进行隔离安装冒烟验证

**开发辅助**

- 使用临时虚拟环境或构建后的 wheel 验证，不依赖当前仓库被加入 `PYTHONPATH`；
- 测试临时目录必须是明确创建的目录，不改动用户全局 Python 环境。

**验证步骤**

1. 构建或安装当前项目；
2. 在 Icarus 仓库外的临时 Workspace 执行 `icarus --help`；
3. 配置最小环境后执行 `icarus`；
4. 确认欢迎页 Workspace 等于命令启动目录；
5. 确认安装包能读取默认 `settings.json`；
6. 输入一条多行 Prompt，确认只提交一次；
7. 确认退出后没有遗留 Live 区域或后台 Runtime。

安装或下载依赖需要网络时，按环境权限流程执行，不把依赖包提交到仓库。

### 任务九：进行真实模型和工具链路验证

仅在已有有效凭据且不暴露密钥时执行：

**纯文本 Markdown**

```text
请用一个二级标题和两项列表回复 TUI_MARKDOWN_OK
```

验证流式刷新和最终 Markdown。

**工具调用**

```text
读取当前 Workspace 的 README.md，并简要说明标题
```

验证工具开始、工具完成和工具后的新 Markdown 段。

**多轮**

连续两轮验证 Blackboard 仍然维护业务 History，TUI 每轮只提交当前 Prompt。

如果没有凭据，明确记录“真实模型冒烟未执行”，不能以单元测试结果冒充真实链路。

## 阶段六：同步用户文档和需求状态

### 任务十：更新启动与兼容说明

**更新文件**

- `README.md`
- `apps/tui/README.md`
- `docs/todo/tui.md`

**开发内容**

- 把推荐启动方式改为在 Workspace 中执行 `icarus`；
- 保留开发安装和测试命令；
- 解释当前目录就是 Workspace；
- 记录 Enter 提交、Shift+Enter 最佳努力支持、Ctrl+J fallback、Ctrl+D 退出；
- 明确当前仍为串行交互；
- 只在代码和测试完成后勾选 `TUI-01`、`TUI-02`、`TUI-04`、`TUI-07`；
- `TUI-03` 只有在输出和输入生命周期边界完成后勾选；
- 保持 `TUI-05` 和 `TUI-06` 未完成，并链接未来状态机设计；
- 把 TODO 文档中的设计链接更新为 `apps/tui/docs/arch/`，不再指向根 `spec/`。

## 完整验证顺序

### 最小 affected tests

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/model_config -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_input.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_renderer.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_repl.py -q
apps/agent/.venv/bin/python -m pytest apps/tui/test/test_cli.py -q
```

### 应用回归

```bash
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/application \
  apps/agent/test/model_config -q
```

### Agent 全量与静态检查

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q \
  apps/agent/src apps/agent/test apps/tui
git diff --check
```

### 安装检查

在隔离环境中验证：

```text
build/install succeeds
→ icarus --help succeeds outside repository
→ packaged settings loads
→ captured Workspace is correct
```

## 推荐变更拆分

若后续用户明确要求提交，按逻辑层拆分：

1. packaging + Agent settings package resource + focused tests；
2. prompt_toolkit input + input tests；
3. Rich Renderer + renderer tests；
4. REPL / CLI integration + tests；
5. README、TODO 和验证记录。

实现与对应测试保持在同一次提交。未经用户明确要求，不创建提交、不 amend、不 rebase、
不 push。

## 风险和控制

### Shift+Enter 终端差异

风险：传统终端把 Shift+Enter 和 Enter 都发送为 `\r`。

控制：以 Ctrl+J 为最低保证；Shift+Enter 只对可识别序列启用；欢迎页和 README 如实说明。

### Rich Live 与 prompt_toolkit 输出协调

风险：未来 Agent 运行中开放输入后，Live 刷新可能破坏活跃输入控件。

控制：本阶段严格串行，Live 期间不启动输入读取；后续由状态机设计统一协调。

### 长 Markdown 段重复渲染

风险：每个 Delta 对完整 Markdown 重渲染，长回答可能产生性能问题。

控制：节流 UI 刷新但继续累积全部 Delta；最终固化前强制刷新，不丢事件。

### 安装后资源不可用

风险：源码相对路径在 wheel 或非仓库 cwd 下失效。

控制：使用 package data + `importlib.resources`，并在仓库外做隔离安装测试。

### 依赖双重维护

风险：`pyproject.toml` 与 `apps/agent/requirements.txt` 版本范围漂移。

控制：本阶段要求两处范围一致；后续依赖治理可独立统一，但不在本需求中引入新工具。

## 完成标准

- 安装后能从任意 Workspace 执行 `icarus`；
- 运行时 Workspace 等于命令启动目录；
- 欢迎页不清屏，并显示准确 Workspace 和按键提示；
- 输入支持多行、光标移动和多行粘贴；
- Enter 提交，Ctrl+J 换行，Shift+Enter 在受支持终端换行；
- 用户消息与当前编辑缓冲职责分离；
- Agent Markdown、工具状态和错误按 Event 顺序保留在 scrollback；
- 流式 Markdown 最终不重复、不丢失；
- TUI 只通过 `AgentRuntimeService` 提交和订阅；
- 每个 Session 只创建一个长期实时订阅，并在退出时关闭；
- 当前任务结束后才读取下一次输入；
- 没有实现或伪装实现 TUI 本地消息队列、运行中输入和任务取消；
- TUI、应用层、配置和 Agent 全量测试通过；
- 隔离安装检查、compileall 和 `git diff --check` 通过；
- README 与 TODO 与实际能力一致。
