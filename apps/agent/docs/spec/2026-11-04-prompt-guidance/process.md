# 提示词审阅过程记录

日期：2026-11-04，时区 Asia/Shanghai。主文档见 [review.md](review.md)。

本文件记录本轮实际操作、证据和限制；不记录隐式思考过程。审阅文件归 Agent 所有，不创建空的 arch/plan，也不把单应用审阅写入根跨应用 spec。

**阅读提示**：第 0–8 节是两轮审阅的历史记录；实施轮见第 9 节。目录已迁至 `apps/agent/docs/spec/2026-11-04-prompt-guidance/`，历史命令中的 `docs/reviews/` 路径不再是当前位置。

## 0. 复核轮（同日追加）

初稿完成后按用户要求“review 并完善审阅文档”，进行了第二轮逐条复核。该轮只读代码、只跑本地探针，不修改任何生产文件。

复核方法：不依赖初稿结论，对每条发现的证据行号、计数和因果链独立重算；把每个“我怀疑成立”的点写成一个最小探针，跑完再决定保留、修正还是撤回。

复核结果分三类：
- 采纳并加强：R11–R14（新增），R02、R04、R05、R09、R10、R13（补证据或改结论）。
- 修正：R05 从“英文 base 变长”扩展为“多数段都变长”；R10 从“27/15/23 三个数互相矛盾”修正为“34 声明 / 9 常驻 / 27 延迟面”。
- 撤回：R03 关于“字节读取上限与字符预算口径错位”的假设（探针否证）；R14 关于“模型被告知未注册常驻工具”的假设（探针否证）。

被否证的假设也记录在 review.md 第 1 节，避免后续重复排查。

## 1. 起点与工作区保护

- 起始目录：`F:\project\Icarus`。
- 起始分支：`feat/webui`。
- 起始提交：`4082fafc0774f9003ac60e81edd03684a687e078`。
- 起始工作区改动：` M .gitignore`，本轮未改动此文件。
- 已阅读根 `AGENTS.md` 与 `CONTRIBUTING.md`。没有调用子代理；用户没有要求并行代理工作。
- 初始用户目标是排查提示词引导/描述并提交审阅文档；追加要求把过程保存到本地，并核实分支及原仓库上游进展。本轮未修改生产提示词或运行时代码。

## 2. 参考文件获取

1. 尝试直接读取飞书链接，未得到正文；Playwright 初次导航跳到登录页。
2. 向用户说明读取缺口并继续代码排查；用户回复“已登录”。
3. 加载 computer-use 技能并尝试定位已登录窗口。native pipe 报 `系统找不到指定的文件 (os error 2)`；没有继续 Windows UI 操作，也没有自动处理登录或权限对话框。
4. 重新导航既有 Playwright 页面成功，标题为《Icarus 提示词精简改造计划.md》。点击“查看代码”，读取浏览器 DOM 中带 `data-line-no` 的 Markdown 行。
5. 文件源代码视图会虚拟化行。逐页滚动并按行号合并后得到完整的 1–373 行，回到顶部。没有只以首页预览代替全文。
6. 原文保存为 [reference-feishu.md](reference-feishu.md)，去除编辑器的零宽占位符，保持原行序。副本包含参考方案本身的错误；修正意见在主审阅文档中，不篡改原文。

技能文件：`C:/Users/郭尚勇/.codex/plugins/cache/openai-bundled/computer-use/26.1027.30824/skills/computer-use/SKILL.md`。后续网页读取使用已有 Playwright 能力，无需 Windows native pipe。

## 3. 上游、分支与审阅基线

已执行的 Git 操作包括：status、branch、rev-parse、remote、fetch、log、rev-list、diff、ls-remote、show、ls-tree、merge-base 和 archive。
`git fetch upstream` 是本轮唯一更新远端跟踪引用的操作；没有切分支、修改当前分支历史或合并工作树。

| 项目 | 实际结果 |
|---|---|
| origin | `https://github.com/xilele777/Icarus.git` |
| upstream | `https://github.com/Removel/Icarus.git` |
| upstream/feature | `c019e9a7b7f12e175e59c4928c1678aba448cc70` |
| 最近集成提交 | 2026-10-30 17:00:37 +08:00，Merge pull request #16 from Removel/feat/tui |
| 本地 HEAD 对 upstream/feature | `git rev-list --left-right --count HEAD...upstream/feature` 返回 `0 10` |
| upstream/feat/agent | `649c595931c8a597d623e1bd7b7cf4333dbfdd8d`，已被 feature 包含 |
| 上游默认生产分支 | `git ls-remote --symref upstream HEAD` 返回 `refs/heads/main` |
| 生产 main | `e8998d125bcd95e09ce772a435c2ca5956b05236`；本轮未将它替代 feature 作为开发基线 |

本地缺少的 10 个集成提交：

```text
c019e9a Merge pull request #16 from Removel/feat/tui
87ee462 fix(tui): keep navigation and streaming updates responsive
4dd500e fix(tui): prevent selection crashes on detached disclosure headers
ba0dd68 Merge pull request #14 from xilele777/feat/webui
4e0ab8b Merge pull request #15 from Removel/feat/agent
649c595 docs(agent): document tool quality design and verified architecture
10f9abc feat(agent): add resumable text reads and clear command failures
85ced43 feat(agent): improve MCP results and bounded diagnostics
a063c30 feat(agent): harden tool results and lazy execution contracts
32b2c0f feat(agent): raise tool result budgets consistently
```

额外发现本地 `feat/argus` 的 `3186c01` 有相近提示词改造计划。`git merge-base --is-ancestor 3186c01 upstream/feature` 返回 1；未将此分支的计划当作集成状态。

后续要切分支时需先明确处理当前工作区改动。审阅已可独立阅读，本轮没有把“建议使用 feat/agent”解释成创建分支或合并的授权。

## 4. 源码检查与证据组织

先用 `rg --files` 定位 prompts、instructions、persona 和相关文档，再沿实际调用链读取：

1. PromptSection/PromptContributor 合同，static/dynamic 层，Prompt.prepare/build/read/finalize/update。
2. SessionRuntime 在插件恢复后采样静态内容，并向 Blackboard 注入预算验证。
3. Blackboard 的 system 获取、动态数据拍平、user_request 包装与历史压缩。
4. builtin、process、memory、relationship、knowledge、skill、MCP、persona 的 guidance、工具 schema 与注入测试。
5. ToolSurface 的常驻/延迟分类，tool_search 的精确 scope/name 和完整定义加载，tool_execute 的 Run 内加载检查。
6. 上游新增的 read 双重分页、裁剪恢复及加载结果规则；与本地 HEAD 差异单独核对。
7. Gateway 的私有会话指令校验、持久化与重启端到端测试。
8. 相邻模型任务：标题、压缩、Skill 生成/演化、Persona judge；WebUI 仅作入口搜索，没有进行界面文案逐页审查。

最终把发现分为当前可复现行为、当前文案歧义和参考稿迁移风险。每项都给出源码/参考行、影响与承接建议，没有实际重写提示词。

## 5. 隔离验证环境

使用 `git archive upstream/feature` 导出 `AGENTS.md`、`CONTRIBUTING.md`、`apps/agent`、`apps/gateway`、`packages`，解压到：

```text
C:\Users\郭尚勇\AppData\Local\Temp\icarus-prompt-review-31b6822c9a0c4b7185ff03f6f7c651af
```

测试解释器使用现有 `apps/agent/.venv-win/Scripts/python.exe`，版本 Python 3.13.14。没有创建根 Python 环境，没有读取 `.env` 或输出凭据。

该解释器没有 pip；第一次 `python -m pip` 失败。第二次使用 `uv pip --no-build-isolation`，因环境没有 setuptools 构建失败。随后去掉 no-build-isolation，构建隔离只安装构建依赖，执行以下操作成功：

```powershell
$auditRoot = 'C:/Users/郭尚勇/AppData/Local/Temp/icarus-prompt-review-31b6822c9a0c4b7185ff03f6f7c651af'
$agentPython = 'F:/project/Icarus/apps/agent/.venv-win/Scripts/python.exe'
uv pip install --python $agentPython --no-deps --target "$auditRoot/apps/agent/.review-site" "$auditRoot/apps/agent"
```

这是把所审阅应用正常构建安装到临时 target，而非替换当前应用的 editable 安装。测试进程优先导入这个安装目录，运行目录是 archive 根。输出已核实 `agent_app.__file__` 与 `icarus.__file__` 都来自 `.review-site`，因此并非拿旧工作树的实现冒充上游测试。

## 6. 实际测试记录

第一批聚焦测试：

```text
apps/agent/test/agent_app/plugins/prompt
apps/agent/test/agent_app/plugins/persona/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/memory/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/relationship
apps/agent/test/agent_app/plugins/knowledge/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/skill/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/mcp/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/process/test_prompt_contribution.py
apps/agent/test/agent_app/plugins/builtin_tools/test_prompt_contribution.py
apps/agent/test/icarus/core/tools/test_tool_search.py
apps/agent/test/icarus/core/tools/test_tool_execute.py
apps/agent/test/icarus/core/tools/test_tool_surface.py
```

pytest 参数：`-q --tb=short`。实际结果：

```text
FAILED apps/agent/test/agent_app/plugins/prompt/test_workspace_sources.py::test_workspace_agents_precedes_claude_without_combining
AssertionError: assert '\r\n AGENTS \r\n' == '\n AGENTS \n'
1 failed, 136 passed in 41.89s
```

代码核实：测试写文件没有指定 `newline`；Windows 默认写 CRLF，loader 用二进制读取后直接 UTF-8 decode，保留 CRLF。该项是上游基线在此环境中的既有结果，未修复、未跳过，也没有称整套通过。

第二批补充测试：

```text
apps/gateway/test/test_session_instructions.py
apps/gateway/test/test_prompt_e2e.py
apps/agent/test/agent_app/contracts/test_prompt_contract.py
apps/agent/test/agent_app/plugins/blackboard/test_history_compactor.py
```

同样参数，实际结果：`27 passed in 15.54s`。Gateway 端到端测试使用生产插件/SQLite/本地 HTTP 模型 fixture 与真实本地 socket；没有访问远端模型。两批共 **163 passed、1 failed**。

本轮只有审阅与支持文件改动，不运行应用全套、WebUI/TUI 全套或收费外部模型 smoke。该限制不能被以上聚焦结果替代。

## 7. 可控测量与复现

运行命令：

```powershell
& $agentPython 'F:/project/Icarus/apps/agent/docs/reviews/2026-11-04-prompt-guidance/audit_probe.py' --source-root $auditRoot --package-dir "$auditRoot/apps/agent/.review-site" --output 'F:/project/Icarus/apps/agent/docs/reviews/2026-11-04-prompt-guidance/measurements.json'
```

探针不调用远端服务，不读个人运行期人格/记忆，临时文件用 TemporaryDirectory 管理。结果：

- 最新 base：600 预算单位；参考旧 base：472；参考英文 base：911。
- 合成静态样本：2976。样本使用默认人格、空技能目录、无工作区/外部指令、无 Relationship guidance/快照、JEV 关闭，节 ID 使用简化名称。它不是某个真实会话的最终完整请求，也不包含所有工具定义/历史。
- 3001 字符行页：首次 2000 字符且 truncated，正确续读还有 1001 字符。
- 200 单位受控组装预算中：workspace 可选段被淘汰，base/persona/external 保留。
- memory_read 参数含 include_history；memory_correct 不含，只有 content/ref。
- 未加载的 memory_recall 执行请求在派发前失败，并提示当前 Run 要先 tool_search。
- manifest 清单仅代表声明；启用插件、生成开关等改变实际工具集，未把声明数当成运行时数量。

复核轮追加的探针（同一脚本、同一安装，重跑后 measurements.json 已更新）：

- 参考稿全部代码块逐段计数，含 memory/relationship 两处**未标注语言**的围栏；这两处在早期版本被 ` ```python ` 正则漏掉，是本次 ` ```[a-z]* ` 修正后才纳入的。计数前先 `strip()`，与常量实际取值一致：memory 699→1163、relationship 796→909（此前按原始围栏计得的 700/1164、797/910 已作废）。
- DEFENSE/STRONG_DEFENSE/REANCHOR 按 `## contributor/persona/{section_id}\n{content}` 渲染后计数：当前 100/92/82（均在 200 内），参考稿 226/305/116。
- 删除 persona.md 的 boundaries 与 anti-goals：identity 233→118、invariants 122→41。
- 按参考稿整篇 persona.md（soul 扩写 + 两段删除）：identity 233→167、invariants 122→111。数值看起来只是小降，但 `invariants_keeps_boundaries=false`——预算/长度类断言放不过去。这一条是复核过程中发现“初稿用 122→41 说明风险”不够准确后补的：41 是“只删两段”的隔离值，不等于参考稿的实际结果。
- persona 段名写错（`## boundary`、`## ignored`）：同样落到 identity 118、invariants 41，且只有 warning。
- workspace 在 6144/6145 个 emoji 之间丢弃；该探针同时否证了“字节读取上限与字符预算口径错位”的假设。

`measurements.json` 的 `loaded_agent_package` 记录的是 `$auditRoot/apps/agent/.review-site/agent_app/__init__.py`。复核时曾用 `git archive` 在工作树内建过临时目录做只读比对，该目录已删除，交付文件不再引用它。

完整数据见 [measurements.json](measurements.json)，探针见 [audit_probe.py](audit_probe.py)。文件中的基线标识固定为本轮审阅提交，未来复用时应同时调整和核实所安装版本。

## 8. 交付检查

已生成主审阅、过程记录、参考副本、测量结果和复现脚本，最后检查结果：

- 隔离基线 Agent/Gateway 源码 `compileall`：通过。
- 复现脚本 AST 与 measurements.json 解析：通过。
- 参考副本：373 行；5 个交付文件的 UTF-8/行尾空白检查通过。
- Markdown 本地链接：11 处全部能解析到存在的文件。
- `git diff --check`：通过。该命令不覆盖未跟踪文件，新增交付文件已另做上述空白检查。

Git 跟踪文件的变更仅保留用户原有 `.gitignore`；本轮新增文件都位于 `apps/agent/docs/reviews/2026-11-04-prompt-guidance/`。未提交、未推送，当前分支仍为 `feat/webui`。

## 9. 第二版复核后的实施轮（2026-11-04）

### 复核与 Git

再次读取 AGENTS.md、CONTRIBUTING.md、第二版审阅与相关源码/测试。确认原稿修正方向成立；补正 R14：常驻 9 个而只注册 read/write 时是 7 个缺失/7 条 ERROR，不是 6 条。R11 保留“演化判据输入失去边界内容”的证据，把“更容易越界判过”收紧为未经真实模型评估的风险推断。

先 `git fetch upstream`，确认集成基线仍是 `c019e9a7b7f12e175e59c4928c1678aba448cc70`。用户明确回复“允许，创建并切换 feat/agent 后修复”后执行 `git switch -c feat/agent upstream/feature`。本地新分支从最新原仓库集成分支创建，包含该基线；没有 merge、rebase、commit 或 push。保留用户 `.gitignore` 与 `pelican-bicycle.html`。

将用户已移动到 `apps/agent/docs/2026-11-04-prompt-guidance/` 的文件整体迁到本目录，新增实质 [plan.md](plan.md)，未建立占位架构文档。更新已有 Prompt/Persona 的 arch.md 以匹配最终代码。原飞书副本与原 measurements.json 保留为审阅时证据。

### 实际修复与承接

- Base 明确“常驻直接调用、延迟先加载、每 Run 重载、加载不代替用户授权”；保留工作区、澄清、凭据、执行诚实、合理请求和人格权限边界。tool_search 点名 `_execution.max_output_tokens` 恢复路径，保存文件不构成加载。
- builtin/process/Skill guidance 缩为能力选择；read 双维分页细则留在工具，字段补单位与续读关系。write/insert 增加完整覆盖、换行与重试核实说明。process 的扁平 action 描述补齐参数依赖、副作用、游标和分页单位，执行验证不变。
- Memory guidance 允许如实说明来源与故障，区分无命中，保留自主管理和跨插件分工。scope、原话抽取、correct 前 read、停止/删除细则由对应工具承接；include_history 仍只属于 memory_read。
- Relationship 不再见人名就查询，普通祝福/翻译上下文够用即可完成；身份与方向细则保留在工具，补完整候选条件、创建确认和 profile 键/值格式。
- 删除 Persona 的重复 POLICY，其执行边界归 base；EVOLUTION_GUIDANCE 用 `persona-05-evolution` 独立贡献，仍受 `jev_enabled and not NullJudge` 控制。默认 persona.md 的 boundaries/anti-goals 与 JEV invariants 来源保持完整，新增内容断言与实际传入判据的检查，没有引入第二份边界配置。
- workspace 省略日志补原因/预算/读取字节；保持现有可选/不回退/不递归策略。Persona 缺失 invariant 段汇总告警，动态段预算丢弃记录 section/单位/上限。会话 before_ready 汇总缺失常驻工具；注册表既有过滤保留。日志不带正文、原始异常或凭据，不新增 UI/协议状态。
- 摘要任务补保留来源、授权范围和未验证状态的规则；外部数据标签保持。Knowledge/MCP guidance 原本较短且有必要的局部数据边界，未为追求零重复删除。
- audit_probe.py 不再 exec 参考文档代码，只通过 AST 提取字符串常量及串接；增加显式 baseline 标签和独立工具定义测量。它仍只使用临时 fixture，不调用模型/外部服务。

### 测量

计量仍为项目保守预算，不是实际模型 tokenizer。分别用同一新版探针测基线和当前工作树，输出 [measurements-before.json](measurements-before.json)、[measurements-after.json](measurements-after.json)。

| 静态文本 | 基线 | 修复后 |
|---|---:|---:|
| Base | 600 | 599 |
| builtin guidance | 285 | 99 |
| Memory guidance | 699 | 389 |
| Relationship guidance | 796 | 293 |
| Process guidance | 195 | 124 |
| Skill 空目录贡献（含能力开关） | 212 | 168 |
| Persona POLICY | 111 | 0 |
| Persona 演化指导（条件启用） | 168 | 121 |
| Knowledge / MCP | 184 / 199 | 184 / 199 |
| Persona identity | 233 | 233 |

原合成静态 fixture 从 2976 到 2225（−751，约 25.2%），其条件仍为默认人格、JEV 关闭、无工作区/外部指令/Relationship guidance/快照，使用简化 section id；不是完整线上请求。Relationship 单段另降 503。

工具定义下沉有代价：在固定 fixture 参数、空 tool_search 目录、JSON sort_keys/ensure_ascii=False 序列化下，所测 21 个定义合计从 11939 到 14654（+2715）。此数不含所有运行时目录/执行器包装，不能与静态节数简单相加后宣称线上节省。read 默认单次字符页限制独立于工具结果预算；续读协议不变。

DEFENSE/STRONG_DEFENSE/REANCHOR 沿用原短句，渲染为 100/92/82，均在默认 200 内；额外用 110 预算验证 defense 优先、极小预算验证告警，不放宽默认上限。

### 环境与测试

Windows Python 3.13.14，复用 Agent 私有 `.venv-win`，使用 uv 正常 editable 安装当前 Agent（不使用根 PYTHONPATH）。所有实施轮 pytest 命令设置 `PYTHONUTF8=1`，日志与 JUnit XML 保存在本目录。

最初尝试沿用旧的局部 archive 跑全套，得到 1175 passed / 63 failed / 36 skipped，但该 archive 缺少其它应用文件，不能作为全套比较基线，结果仅保存在 baseline-tests.log 说明尝试历史。

随后用 `git worktree add --detach` 在临时目录建立完整上游基线，无命名新分支：
`C:/Users/郭尚勇/AppData/Local/Temp/icarus-prompt-baseline-20261104`。
在其 `apps/agent/.venv-review` 安装对应 Agent editable，依赖路径指向现有 Agent 环境；实际导入路径已打印核实为该 worktree 的 src。两套 Python 版本、依赖相同，基线与当前源码各自正常安装；未复制 .env 或生产数据。

验证顺序为聚焦文件/目录、应用全套，再编译/diff：

- 最新聚焦测试：**306 passed / 3 failed**（focused-tests.log/xml）。3 个失败为 workspace 原始 CRLF 断言与 Persona frontmatter 在 CRLF 下未解析引起的两个名称断言。完整基线的同文件重跑为 20 passed / 4 failed（baseline-crlf-tests.log/xml）；修复后缺失段告警让原来“无告警”断言通过，并不表示 CRLF frontmatter 已修复。
- 新增会话缺失常驻工具、Persona 判据传参、加载恢复、摘要规则等补充检查：**55 passed**；此批与聚焦用例重叠，不重复相加。
- Agent 全套基线：**1188 passed / 50 failed / 36 skipped**（baseline-full-tests.log/xml）。
- Agent 全套修复后：**1208 passed / 48 failed / 36 skipped**（after-full-tests.log/xml），另追加一个日志隐私测试单跑通过，并包含在最新聚焦结果中。未为了这一个新增测试重新跑完整套。
- 两份全套 XML 按 classname + name 比较：**新增失败 0、共同失败 48、仅基线失败 2**。不能称全套通过；共同失败也不保证异常文本完全相同。失败涉及 Windows 目录 fd/权限、shell/编解码/CRLF，以及既有运行时用例的 drain 时序，未修改或添加 skip。
- Gateway 私有指令与 Prompt 端到端：**14 passed**（gateway-tests.log/xml）。一次旧文案子串断言先失败，已改为实际修正规则存在性断言；仍检验真实本地 socket、system 注入一次、动态 user context、重启和隐私边界。
- Gateway 应用全套：**50 passed**（gateway-full-tests.log/xml），通过 Agent 解释器运行；Gateway 未改生产代码，仅更新受文案影响的端到端断言。
- mypy 聚焦 `prompt/sources.py`、`persona/plugin.py`、`application/session_runtime.py`：3 个文件通过（typecheck.log）。Agent/Gateway src 的 compileall 与 `git diff --check` 通过；19 处本地文档链接全部存在，JSON/审阅脚本可解析。

最终文件仍未提交或推送，当前为 feat/agent。用户原有 `.gitignore` 和 HTML 未修改；没有把全套测试既有失败标为通过。

收费模型、真实 JEV 和外部存储没有运行；普通聊天不过度查询、模型遵循纠错流程、抵抗伪系统指令的行为不能由以上离线结果保证。工作区超限时是否启动阻断、用户可见降级状态、guidance 随工具面加载、统一英文化均未实施；其边界见 plan.md。

## 10. 逐条承接表与 WSL 验收轮（2026-11-04）

用户要求补齐 guidance → description 成品表，并明确以 WSL 为验收环境。本轮没有新增运行时代码改动，补充 [plan-rule-mapping.md](plan-rule-mapping.md)：36 个工具入口的覆盖清单、104 条规则/边界的原规则→最终所有者/文案→覆盖结论→验收场景，以及 E01–E14 的测试定位。共通规则、工具选择、跨插件分工、原本无独立 guidance 的入口和有意改变的策略均明确列出，不把“没有机械下沉”记为遗漏。

补正概览：Skill 生成/演化开关控制指导与执行许可，两个写工具仍注册，不能将“关闭开关”写成“未注册工具”。未改这个既有运行时设计。更新 plan.md 的所有者概览，review.md 保留基线发现并链接最终承接表，避免把历史缺口当作当前仍未修复。

在 WSL Ubuntu 的 Linux Python 3.14.4 中检查了承接表：36 个入口与 10 个 manifest 的 34 个工具及 2 个会话工具集合完全一致；104 个规则 ID 与 14 个证据 ID 均唯一；62 个本地链接全部存在，引用的 Python 文件可解析。此项只是文档/语法检查，不是模型行为验收。

Git 再次 fetch 原仓库 upstream；`upstream/feature` 仍为 `c019e9a7b7f12e175e59c4928c1678aba448cc70`，`git merge-base --is-ancestor upstream/feature HEAD` 返回 0。拟集成目标为 **Removel/Icarus 的 feature**，工作分支为 feat/agent；origin 为 xilele777/Icarus，不能把 origin/feature 当作本轮上游基线。

### WSL 环境和复核方法

先在 `/mnt/f/project/Icarus` 使用 Agent/Gateway 各自已有的 `.venv/bin/python`；已打印验证 `sys.platform=linux`、Python 3.14.4，以及 editable 导入指向当前 Agent src，没有使用根 PYTHONPATH 或 Windows Python 执行本轮验收。

- 第一批 Agent 聚焦测试：**118 passed**，见本地 `wsl-focused-tests.log/xml`。范围为 builtin tools、Memory/Relationship/Process 工具、Process 指导、base、Persona 静态贡献、tool_search/tool_execute。
- 挂载盘上的 Gateway 聚焦测试：**13 passed / 1 failed**，见本地 `wsl-gateway-focused-tests.log/xml`。失败为真实连接用例 `test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart` 在 `session.unload` 等待 WebSocket 回复 10 秒超时；不是提示词内容断言失败。先如实保留该失败，再做同环境基线对照，不能凭推测标成环境问题或通过。

为区分挂载盘 I/O 与源码行为，在 WSL 原生 `/tmp` 建立完整跟踪源码的隔离基线/修复副本。基线从本轮 HEAD 的 `git archive` 提取，修复副本只应用 Agent src/test 与 Gateway test 的当前差异；没有复制 `.env`、个人运行数据、生产数据库或用户 HTML。私有虚拟环境使用同一 Linux Python 和同一组依赖，并分别 editable 安装对应源码。副本路径、基线和差异信息记录在本地 `wsl-environment.json`。不修改测试超时，不添加 skip，不调用收费模型/外部存储。

### 中间失败与环境修正

挂载盘上的 Agent 全套运行至 1243 passed 后主动中断（24 分钟，`wsl-agent-tests.log/xml`）；这是未完成的一轮，不算全套通过。原生文件系统中首轮 archive 副本的 Agent 结果为修复版 1292 passed / 1 failed、基线 1273 passed / 1 failed：同一个环境加载测试默认预期仓库存在 `.env`，而隔离副本有意未复制该文件。查阅 `packages/runtime_environment.py` 确认有/无文件分别走显式路径/默认 dotenv 查找分支；随后在两边各创建一个只含注释、不含配置或凭据的空 `.env`，原用例 5 passed，再重跑全套。没有修改生产加载器、测试断言、时限或 skip。首轮记录保留为本地 `wsl-archive-*.log/xml`。

补齐测试环境后，原生 `/tmp` 副本得到 Agent 1293 passed、基线 1274 passed、Gateway 聚焦 14 passed、全套 50 passed。后续复核时该临时目录已不存在，不能再用它完成编译与源码比对；因此又在 WSL 用户主目录建立持久隔离副本，并在其中重复最终验收。没有根据临时目录消失推断源码问题，也没有省略最终检查。

最终副本位于 `/home/guoshangyong/icarus-prompt-guidance-iqvbrczn/{baseline,current}`，两边为 `c019e9a` 的 **detached worktree**；当前副本应用本轮源码/测试差异。没有创建命名分支、移动 feat/agent、提交或推送。依赖来自原 WSL 私有环境中的 Linux 文件，通过归档传入原生文件系统；Windows 只用于打包这些既有 Linux 文件，不执行验收。构建依赖两边一致为 setuptools 84.2.1 / wheel 0.48.0，应用各自正常 editable 安装。详细路径和环境说明保存在本地 `wsl-environment.json`。

### 最终 WSL 验收结果

以下为持久副本的最终结果，原始命令、退出码、时间在本地 `wsl-final-results.json`，完整日志与 JUnit 使用相同前缀。

| 范围 | 结果 | 本地日志/JUnit 前缀 |
|---|---|---|
| Agent 最小受影响工具/指导及环境前提 | **123 passed，0 failed，0 skipped** | `wsl-final-focused-tests` |
| 修复版 Agent 应用全套 | **1293 passed，0 failed，0 skipped** | `wsl-final-agent-tests` |
| 同环境上游基线 Agent 全套 | **1274 passed，0 failed，0 skipped** | `wsl-final-baseline-agent-tests` |
| Gateway 私有指令/提示词真实连接聚焦 | **14 passed，0 failed，0 skipped** | `wsl-final-gateway-focused-tests` |
| Gateway 应用全套 | **50 passed，0 failed，0 skipped** | `wsl-final-gateway-tests` |

Agent/Gateway src、test 及共享 packages 的 Linux compileall 通过，隔离工作树 `git diff --check` 通过。已将测试副本中 34 个发生变化的源码/测试文件与主工作树逐文件比较：统一 CRLF/LF 后内容完全一致，SHA-256 保存在本地 `wsl-final-checks.json`，不能把运行另一份未知源码的结果当成本地验收。全部文档的本地链接 86 处检查通过；承接表 36 个工具、104 条规则、14 组证据入口均已核对。最终 46 个建议提交文件也已整体比对并检查，原始验收产物未混入提交清单。

本轮以这些 WSL 结果取代 Windows 的验收结论，不把前轮 Windows 既有失败带入 Linux 通过数，也不混加重叠聚焦用例。最终没有发现需要继续修改运行时代码的问题；模型行为评估边界仍以承接表为准。

### PR 准备

已准备 [PR 标题、正文与提交拆分](plan-pull-request.md)，本地 `pr-files.txt` 明确列出 46 个建议提交文件：既有 34 个源码/测试、2 个架构文档、本目录 10 个审阅/计划/测量支持文件。原始日志、JUnit、环境/验收 JSON 和清单本身只保留本地，不使用整目录暂存；用户原有 `.gitignore` 与 `pelican-bicycle.html` 排除。Git 仍为 feat/agent，未 commit、push 或创建远端 PR。

## 11. 本地提交与手工验收准备（2026-11-05）

用户授权提交提示词修复，并要求原始日志可以删除或不提交。本轮选择保留本地原始证据，不将 `.log`、JUnit `.xml`、WSL 环境/验收 JSON 或 `pr-files.txt` 纳入提交；通过 `.git/info/exclude` 的本机规则隐藏这些产物，未修改用户原有 `.gitignore`。凭据、本机运行配置、虚拟环境、数据库恢复材料和用户 HTML 均不在提交范围。

提交前重新 fetch upstream/feature，仍为 `c019e9a`，基线包含检查通过。原拟提交 46 个文件与 WSL 最终验收保存的规范化 SHA-256 全部一致；其中 34 个源码/测试没有新增变动，沿用第 10 节已经通过的 WSL 测试结果，未重复运行整套测试。第一笔提交为 `272a922`（`fix(agent): clarify prompt ownership and standalone tool descriptions`），仅包含这 34 个源码/测试文件。

文档提交补充 [真实模型手工验收问句](plan-manual-acceptance.md)：6 组无需持久写入的任务、4 组可选测试写入/续读任务及结果记录方式，明确后台自动召回不等于模型主动查询、回复自述不等于执行证据、单次通过不代表稳定通过。此文档为待执行方案，没有运行收费模型、外部服务行为评估或真实 JEV。

最终提交范围为 47 个文件：原有 46 个，加手工验收方案。代码/测试与文档分别提交，未推送或创建远端 PR。第 9、10 节的“未提交”是当轮历史状态，以本节更新为准。

## 12. 英文化与上下文交付轮（2026-11-05）

本轮用户新要求：整理Codex调研文档，对照当前方案继续精简和增强提示词，统一英文，并调整各插件的注入位置/时机。继续 `feat/agent`，实施基线为上轮已提交的 `13743b0`。开始时fetch原仓库upstream/feature并检查祖先关系，上游仍为 `c019e9a`；未创建分支、合并、提交或推送。用户原有.gitignore、HTML，以及同期新增的web-search自主性文档不在本轮修改范围。

### 资料与改动

将根docs中的用户调研原文迁到 [reference-codex-research.md](reference-codex-research.md)，归属现有Agent专题。核对其本地Codex源码基线 `de6e72addd67d35e6bf83b7acac406116bf598a6`，将可采纳方法与不能直接证明的推论分开：稳定ID不保证缓存、增量不等于免除历史计费、未见源码引用不证明文件必为导出、标签不提供执行授权。官方文档检索/页面读取未返回可用正文，因此没有伪造官方引文或据此更换API/模型。

实施和位置/时机表见 [plan-context-delivery.md](plan-context-delivery.md)；当前 [104条规则承接表](plan-context-rule-mapping.md) 保留旧ID逐条核对，旧表标注为历史文案。同步更新Prompt/Persona当前架构，不将旧中文短引文写成最终源码。

- base、插件指导、内置工具/字段描述、记忆包装/状态、联网提示/错误、摘要、标题、JEV判据、默认人格统一英文。回复与标题仍按用户语言；用户/工作区/自定义人格/Skill/第三方内容不翻译。
- Memory/Relationship/Process/Knowledge/Skill的System只保留发现和必要触发规则；完整跨工具指导在tool_search.scope_guidance交付。仅有效延迟scope采样，core仅保存普通字符串，不依赖应用契约；无效规则阻止装配。执行器在裁剪后验证指导与工具定义完整性，同步/异步可恢复。
- Skill启动不再扫描目录用于提示词；调用list/search时扫描，正文再read；produce/evolve开关及Job确认按需交付。仅evolve开放的情况也明确要求确认作业完成。
- Persona核心身份/完整边界保持System；人物档案按任务刷新，演化观察按会话冻结后作为当前User背景交付。关系与JEV并行且失败独立；原文人物关系方向保留，中文事实不翻译。防御/重锚仍条件注入且预算不变。
- WebSearch当轮只交付时间、开关、深度及发现入口，具体调用/来源/预算规则放工具定义和实际结果。Memory仍走原Event/ACK链，不在Prompt复制召回正文。

### 复核发现与修正

首轮聚焦233项有5项失败：4项旧语言或注入位置断言、1项英文观察头占用过小自定义overlay预算。后者是真回归，缩短头部而不提高配置预算；补完整边界内容断言，防御默认200/自设110测试继续通过。进一步发现Skill仅evolve开放时的作业验证规则需要明确承接，已修正文案和独立用例；档案关系英文表达改为明确的“source is target's detail”，避免箭头语义含糊。

全套mypy初次缺开发依赖，随后直接按文件运行又未加载应用pyproject，出现import-untyped；安装隔离开发依赖并改用仓库规定的make typecheck-agent，最终273源文件通过。没有为通过检查添加ignore或放宽类型规则，也未增加运行时依赖。

### WSL验收和测量

新建原生WSL持久隔离目录 `/home/guoshangyong/icarus-prompt-english-8q88kg5u/{baseline,current}`，从13743b0归档构造；当前副本应用本轮src/test变更，分别editable安装。仅注释空.env，不带凭据、服务运行数据、个人配置或生产数据库。最终命令由Python subprocess记录真实returncode，避免外层Shell重定向/尾部输出掩盖退出状态。

最终：聚焦45 passed；Agent全套1304 passed；Gateway全套50 passed；Agent全套类型273文件通过；两应用脚本内compileall通过；WSL以最终补丁对基线执行git apply --check --whitespace=error通过；55个改动源码/测试与主工作树规范化CRLF/LF后逐文件一致。文档检查104条规则集合完全对应且无重复、4组新增证据、120处本地链接存在，测量JSON/探针可解析。原始输出、命令/退出码、环境及SHA保留本地wsl-english文件，继续排除提交。

新增 [context_delivery_probe.py](context_delivery_probe.py)，使用生产运行时/Agent/执行器/历史/模型适配器和loopback脚本HTTP响应，两个版本各跑0/8个Skill、每组3任务5请求；断言搜索/执行均成功、System稳定、中文用户正文保留、Skill正文不预注入。tiktoken仅装在隔离开发环境。完整分项结果存 [改造前](measurements-context-before.json) / [改造后](measurements-context-current.json)，保留System/User/常驻schema/历史/当前工具步骤及各scope完整搜索响应。

最终o200k输入代理值：0个Skill首请求3562→2910（约-18.3%）；3任务5次输入合计20383→17608（约-13.6%）。8个Skill首请求3862→2910，合计21883→17608（约-19.5%）。cl100k分项也在JSON。按需范围规则增加首次搜索成本，未以System节省量单独宣称净收益。临时路径统一为占位符后计量；不把脚本假usage当token，不承诺线上账单或真实模型质量。

base字符/保守预算599→1497，但o200k代理394→270；生产Prompt12288/单节8192/Persona防御200不变，base单测文本护栏600→1800明确记录。Persona关系/观察移User后仍可能随历史重复累积，且开启关系时每任务增加读取；该成本夹具关闭这些可选背景，因此不外推节省比例。自定义关系超时大于Prompt等待窗口仍需协调配置。

本轮没有调用收费模型、真实JEV或外部数据库；中文回答质量、工具选择/身份确认、抗注入等行为继续待真实模型验收。本轮修改未提交/未推送，原始日志仅本地保留。

## 13. 草稿 PR 提交准备（2026-11-05）

用户明确授权提交并创建草稿 PR。重新 fetch 原仓库 upstream/feature，仍为 c019e9a，祖先关系通过；origin/feat/agent 是当前分支祖先，可正常推送，无需强推。提交前在 WSL 重新比对全部 55 个源码/测试文件，与已通过验收的原生副本规范化行尾后完全一致，沿用第 12 节完整测试结果；文档规则/链接/JSON/探针检查重新通过。没有重复调用模型或修改服务配置。

本轮代码与测试提交为 `077f9d2`（`feat(agent): deliver English prompt guidance on demand`）。更新 [PR 材料](plan-pull-request.md) 覆盖全部最终改动，文档单独提交。远端目标为 Removel/Icarus 的 feature，来源为 xilele777/Icarus 的 feat/agent；按用户要求发布为草稿，保留真实模型行为验收及远端 CI 的待办状态。用户其他改动和原始日志继续排除，不自动合并。

## 14. 架构文档一致性修正（2026-11-05）

草稿 PR #17 创建后，用户要求修正原架构文档残留的旧说明。重新核对 Persona plugin、RelationshipReader 和对应测试，统一启动时冻结身份/观察、按任务读取档案、最多两个静态分节、按完整事实预算逐项取舍，以及判定失败保留可用背景的描述；删除已不存在的 policy 段命名，明确后台档案读取与模型主动检索的区别。Prompt 架构补上遗漏的 Relationship 静态贡献者，将计数从六改为七。

本次仅修改两份架构文档和本过程记录。沿用已通过的运行时代码验收；提交前在 WSL 检查规则映射、本地链接、JSON/探针语法和 Git 差异格式，不重跑应用全套，也不调用真实模型。
