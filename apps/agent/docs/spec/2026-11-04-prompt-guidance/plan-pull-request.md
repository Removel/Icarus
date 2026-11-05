# PR 准备材料

日期：2026-11-04；提交准备更新于 2026-11-05。工作分支 `feat/agent`，拟提交到 `Removel/Icarus` 的 `feature`；提交来源可为 `xilele777/Icarus:feat/agent`，以实际推送位置为准。最近 fetch 核实上游集成基线为 `c019e9a7b7f12e175e59c4928c1678aba448cc70`，当前分支已包含。用户已授权本地提交，代码与测试已提交为 `272a922`；尚未推送或创建远端 PR。

## 拟用标题

`fix(agent): clarify prompt ownership and standalone tool descriptions`

## 拟用正文

Agent 的插件 guidance 混有参数细则，部分工具单独加载后缺少前置条件、字段用法或恢复说明；原检索指导也容易把普通聊天引向不必要的查询。本次将共通事实/授权约束放在 base，保留 guidance 的调用场景和跨工具分工，把文件分页、Memory 纠错、Relationship 身份确认、Process action 与知识库写入等具体规则落实到工具及字段描述。

Persona 的执行约束归 base，演化指导改为独立、条件贡献的段，保留人格边界与 JEV invariants 来源。补充工作区规范省略、人格动态预算和常驻工具缺失的诊断，以及摘要的来源/授权/未验证状态约束。逐条承接表登记 36 个工具入口、104 条规则和验收场景，明确哪些是离线协议证据、哪些真实模型行为尚待评估。

保留现有主要中文指导与工具描述语言，不以英文化或放宽预算作为修复方式。固定合成静态样本的保守预算从 2976 降至 2225；所测 21 个工具定义序列化预算则从 11939 增至 14654。两者不是完整线上请求，也不是服务商 tokenizer，不能据此宣称线上 token 净节省。

验证使用 WSL Ubuntu / Linux Python 3.14.4 的原生文件系统隔离副本，各应用私有环境分别 editable 安装对应源码，使用不含凭据的空 `.env` 满足既有环境测试前提；未修改测试超时或增加 skip：

- Agent 聚焦：123 passed；修复版全套：1293 passed；同环境上游基线全套：1274 passed。
- Gateway 聚焦：14 passed；全套：50 passed。
- 全部结果为 0 failed / 0 skipped；Agent/Gateway 编译与 `git diff --check` 通过，34 个源码/测试文件与主工作树在统一行尾后完全一致。

过程中的挂载盘卸载超时、隔离环境缺失 `.env` 及最终复核均如实记录在 [process.md](process.md) 第 10 节。未运行收费模型、真实 JEV 或外部存储评估。[手工验收问句](plan-manual-acceptance.md) 供后续真实模型验收使用，不作为已通过证据。

## 建议的提交拆分

1. Agent 生产代码和对应测试；Gateway 仅包含受指导文案变化影响的端到端断言更新。提交信息可用标题同名。
2. 已有 Prompt/Persona 架构文档更新，以及本目录的审阅、计划、承接表、过程、离线探针与测量数据。提交信息建议 `docs(agent): document prompt rule ownership and validation`。

两个提交共同构成该修复；如果先发布代码提交，评审仍需能找到第二个提交中的承接表。提交前按文件清单明确选择，不对整个工作树或本目录执行无差别暂存。

## 文件范围

代码范围为本轮已经修改的 `apps/agent/src/` 与镜像 `apps/agent/test/`，以及 `apps/gateway/test/test_prompt_e2e.py`。已有架构文档仅包含 `2026-10-17-prompt-plugin/arch.md` 和 `2026-10-20-persona-plugin/arch.md`。

本目录纳入的文档/支持文件为：`review.md`、`plan.md`、`plan-rule-mapping.md`、`plan-manual-acceptance.md`、`process.md`、`plan-pull-request.md`、`reference-feishu.md`、`audit_probe.py`、`measurements.json`、`measurements-before.json`、`measurements-after.json`。飞书副本和原测量保留历史审阅语境；最终状态以承接表和过程第 10、11 节为准。

用户原有 `.gitignore` 和 `pelican-bicycle.html` 不在本次提交范围。所有原始 `.log`/JUnit `.xml`、`wsl-environment.json`、`wsl-native-results.json`、`wsl-final-results.json`、`wsl-final-checks.json` 和本地暂存清单仅保留本地验收证据；虚拟环境、依赖归档和临时副本同样不纳入。实际文件清单在本地 `pr-files.txt` 中生成，提交前可再次核对。
