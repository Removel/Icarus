# SkillPlugin Phase One Development Plan｜Skill 动态检索与注入开发计划

## 目标

实现 `skill-plugin-design.md` 的第一阶段：从全局和 Workspace 目录发现 Skill，使用 FastEmbed 动态检索 Top 3，维护会话累计注入列表，并通过 Blackboard 把 Skill 元信息加入实际 User Prompt 和跨轮历史。

本阶段不实现轮后自动生成、更新、合并或删除。

## 实施原则

- SkillPlugin 只依赖 `BaseEmbedding`，FastEmbed 差异留在 `model_provider`；
- Skill 文件是定义来源，SQLite 只保存 Workspace 使用状态；
- 每次 UserInput 只检索一次；
- Skill 检索失败降级，不阻塞主 Agent；
- Blackboard 保存实际发送给 Agent 的最终 User Prompt；
- 先验证小组件，再接 Plugin 和应用服务；
- 不修改无关工作树内容，不创建提交。

## 任务一：配置与模型适配层

**更新文件**

- `apps/agent/requirements.txt`
- `apps/agent/settings.json`
- `apps/agent/src/model_config/config_model.py`
- `apps/agent/src/model_config/config_loader.py`
- `apps/agent/src/model_config/__init__.py`
- `apps/agent/src/agent_orchestration/plugins/persistence/path_resolver.py`

**新增文件**

- `apps/agent/src/model_provider/base_embedding.py`
- `apps/agent/src/model_provider/embedding_factory.py`
- `apps/agent/src/model_provider/impl/fastembed_embedding.py`

**测试**

- 更新 `apps/agent/test/model_config/test_config_loader.py`
- 新增 `apps/agent/test/model_provider/test_embedding_factory.py`
- 新增 `apps/agent/test/model_provider/impl/test_fastembed_embedding.py`
- 更新 `apps/agent/test/agent_orchestration/plugins/persistence/test_path_metadata.py`

**实现内容**

- 增加 `EmbeddingSettings(provider, model_name)`；
- `settings.json` 默认配置 `fastembed + sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`；
- 定义异步 `BaseEmbedding`；
- FastEmbed 适配器复用一个 `TextEmbedding` 实例；
- 适配器直接调用 FastEmbed 的 `query_embed` 和 `passage_embed`；
- 同步推理通过 `asyncio.to_thread`；
- Factory 是唯一的 Provider 分支位置；
- 路径解析增加全局 Skill、Workspace Skill、SQLite 和模型缓存路径。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/model_config apps/agent/test/model_provider -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/persistence -q
```

## 任务二：Skill 检索核心组件

**新增目录**

```text
apps/agent/src/agent_orchestration/plugins/skill/
├── __init__.py
├── models.py
├── scanner.py
├── usage_store.py
├── ranker.py
└── session_state.py
```

**新增测试**

```text
apps/agent/test/agent_orchestration/plugins/skill/
├── __init__.py
├── test_scanner.py
├── test_usage_store.py
├── test_ranker.py
└── test_session_state.py
```

**实现内容**

- `SkillDefinition`、`SkillUsage`、`RankedSkill`；
- 扫描两层 Skill 目录并解析 YAML 头；
- Workspace 同名覆盖全局；
- 单个非法 Skill 记录并跳过；
- SQLite 建表、首次发现和 Top 3 使用 UPSERT；
- 生命周期边界和 `1.00/0.67/0.33/0.00` 分值；
- NumPy 余弦相似度归一化；
- 80/20 排名、Top 3、稳定 tie-break；
- 累计列表、无新增计数和第七轮完整刷新。
- 同名 Skill 定义变化时原位替换并重新完整注入；

**验证**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/skill -q
```

## 任务三：SkillPlugin 与 Context Event

**新增文件**

- `apps/agent/src/agent_orchestration/plugins/skill/plugin.py`

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/skill/__init__.py`
- `apps/agent/src/agent_orchestration/plugins/__init__.py`

**新增测试**

- `apps/agent/test/agent_orchestration/plugins/skill/test_plugin.py`

**实现内容**

- 只消费指定 UserInputPlugin 的 `UserInputEvent`；
- 每轮扫描、读取使用状态、Embedding 和排名；
- Top 3 更新使用状态并合并到会话累计列表；
- 生成确定性 `full` 或轻量 `unchanged` ContextBlock；
- 空目录发布 `completed + []`；
- 扫描、状态或 Embedding 异常发布 `failed`；
- 整段检索使用 30 秒预算，超时后在当前 Runtime 熔断；
- 扫描、排名和 SQLite 操作移到工作线程；
- task_id 原样透传。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/skill -q
```

## 任务四：Blackboard 最终 Prompt 所有权

**更新文件**

- `apps/agent/src/agent_orchestration/plugins/blackboard/events.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/state.py`
- `apps/agent/src/agent_orchestration/plugins/blackboard/plugin.py`
- `apps/agent/src/agent_orchestration/plugins/agent/context_converter.py`

**更新测试**

- `apps/agent/test/agent_orchestration/plugins/blackboard/test_plugin.py`
- `apps/agent/test/agent_orchestration/plugins/agent/test_context_converter.py`
- `apps/agent/test/agent_orchestration/plugins/agent/test_plugin.py`

**实现内容**

- 将稳定 Prompt 组合器放到 Blackboard 组件边界；
- Blackboard Context Event 携带最终 `prompt`；
- Task State 保存同一份最终 Prompt；
- AgentPlugin/Converter 原样传递，不再次解释 ContextBlock；
- 成功任务把最终 Prompt写入历史；
- 失败任务仍不提交历史；
- AgentPlugin 继续以扁平参数调用 ReActAgent。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/blackboard -q
apps/agent/.venv/bin/python -m pytest apps/agent/test/agent_orchestration/plugins/agent -q
```

## 任务五：应用组装与集成

**更新文件**

- `apps/agent/src/application/agent_runtime_service.py`
- `apps/agent/test/application/test_agent_runtime_service.py`

**实现内容**

- 应用层通过 `EmbeddingFactory` 创建适配器；
- 创建 SkillScanner、SkillUsageStore 和 SkillPlugin；
- 注册 SkillPlugin；
- `SkillPlugin` 订阅 `user-input`；
- Blackboard 订阅 `skill` 且将其设为必需 Context 来源；
- Runtime 停止时释放 Embedding 资源（如果实现需要）；
- 应用测试注入 Stub Embedding，避免下载真实模型。

**验证**

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test/application -q
apps/agent/.venv/bin/python -m pytest apps/tui/test -q
```

## 最终验证

```bash
apps/agent/.venv/bin/python -m pytest apps/agent/test -q
apps/agent/.venv/bin/python -m compileall -q apps/agent/src apps/agent/test
git diff --check
```

如模型已经缓存，再执行一个不进入常规测试门禁的真实 FastEmbed Smoke Test，验证中英文用户输入可以在小型 Skill 集合中返回合理候选。
