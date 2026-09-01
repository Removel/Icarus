# Agent Session Management Development Plan｜Agent Session 管理实施计划

## 目标

为跨应用 Session 管理功能提供非空 Session 摘要和安全空 Session 丢弃接口，同时保持列表读取轻量、
SessionRuntime 按需加载以及现有多 Session 隔离。

设计依据：

- `spec/session-management.md`；
- `apps/agent/docs/arch/session-management-design.md`。

## 实施顺序

### 阶段一：领域类型

更新 `apps/agent/src/application/runtime_status.py`：

- 增加 `SessionSummary`；
- 增加 `DiscardSessionResult` 和明确状态 Literal；
- 从 `application/__init__.py` 导出公共应用类型；
- 不把 Gateway wire model 引入 Agent 应用层。

定向测试：构造、不可变性和状态值。

### 阶段二：Conversation 摘要读取

更新 Persistence ConversationStore：

- 增加只读摘要结果；
- 复用现有 journal 解码和完整性校验；
- 找到第一条有效 `user.message`；
- 在 Persistence 内部记录最后公共活动时间，仅供排序；
- 归一化空白并限制服务端摘要长度；
- 支持图片-only 和兼容性回退摘要，任何 `user.message` 都判为非空；
- 摘要读取不修复文件、不更新 sequence cache；
- 增加清除指定 Session sequence cache 的方法，供安全删除使用。

定向测试：空历史、文本、图片、兼容性回退、多轮、截断尾行只读、中间损坏和摘要长度。

### 阶段三：AgentRuntime Session 摘要

在 `AgentRuntime` 增加：

```python
def list_session_summaries(
    self, workspace_path: str | Path
) -> tuple[SessionSummary, ...]
```

实现：

- 从 DataPathResolver 枚举 Session ID；
- 不创建 `_SessionEntry`；
- 排除空 Session；
- 以内部分页无关的最后公共活动时间倒序、Session ID 升序稳定排序；
- 返回的 `SessionSummary` 只包含 Session ID 和第一条用户输入；
- 保留现有 `list_session_statuses()` 供运行诊断，不混合两种职责。

定向测试：落盘 Session 可枚举、活动和落盘去重、排序、无 Runtime 加载和 Workspace 隔离。

### 阶段四：安全丢弃空 Session

在 `AgentRuntime` 增加 `discard_empty_session()`：

- 在 Session mutation lock 下完成权威检查；
- Busy 或非空时返回结构化结果；
- 已加载空 Session 在锁内发起正常 unload，锁外等待后重新检查；
- unload 后再次校验历史仍为空；
- 增加应用层 `discarding` 并让 create/submit 等 mutation 识别，竞争调用统一得到
  `SessionNotFoundError`；
- 最终检查和删除期间持有 mutation lock，只删除 DataPathResolver 返回的精确 Session 目录；
- 删除成功后清除 ConversationStore cache，并在 entries lock 下移除同一个 Registry Entry；
- 删除失败不提前更新内存状态。

如需在 Persistence 层封装目录删除，接口必须接收 `SessionIdentity`，不能接收任意 Path。

定向测试：未加载空 Session、已加载空 Session、非空、Busy、并发 submit、删除失败和路径安全。

### 阶段五：回归与文档同步

- 验证现有 `create_session`、`submit`、`get_session_history` 和 `unload_session` 行为；
- 验证丢弃空 Session 不影响其他 Workspace/Session；
- 根据最终实现更新 Agent README 和相关路线图中的 Session 能力状态；
- 不实现 `/compact`、非空 Session 删除或 Session 索引数据库。

## 验证命令

按顺序执行：

```bash
apps/agent/.venv/bin/python -m pytest \
  apps/agent/test/agent_orchestration/plugins/persistence \
  apps/agent/test/application/test_agent_runtime.py -q

make test-agent
git diff --check
```

跨应用完成后再执行：

```bash
make test
```

## 完成标准

- 非空 Session 摘要可以由 Gateway 直接消费；
- 列表查询不加载 SessionRuntime；
- 空 Session 清理具有权威 Busy/非空保护；
- 现有 Agent 测试全部通过；
- 文档与最终公开类型、状态值一致。
