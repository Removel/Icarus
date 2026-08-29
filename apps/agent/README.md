# Icarus Agent Runtime

`apps/agent` 提供 Icarus 的模型接入、Agent 执行、Tool、Plugin Runtime、多 Session 管理和本地
持久化能力。它是 Python 运行库，由 Gateway 在同一进程中加载，不提供独立启动命令。

## 当前能力

- OpenAI 与 Anthropic 协议模型接入；
- 无状态 ReAct Agent、流式输出和 Tool 调用；
- Manifest 驱动的 Plugin 发现、依赖解析、生命周期和状态恢复；
- Blackboard 上下文、历史提交和自动 Compact；
- 设备级 AgentRuntime 与多个相互隔离的 SessionRuntime；
- Session 创建、恢复、提交、取消、状态查询和卸载；
- 会话记录、Plugin State、Trace、日志和图片 Asset 持久化；
- Skill 发现、搜索、生产和演化。

## 安装依赖

运行环境：

```bash
./apps/agent/scripts/install.sh
```

开发和测试环境：

```bash
./apps/agent/scripts/install.sh --dev
```

依赖安装在 `apps/agent/.venv`。

## 配置

复制并填写 `apps/agent/.example.env`：

```dotenv
OPENAI_API_KEY=your-api-key
ANTHROPIC_API_KEY=your-api-key
ICARUS_DATA_DIR=/absolute/path/to/icarus-data
```

只需配置当前协议使用的 API Key。模型、Plugin 目录与运行参数在 `apps/agent/settings.json` 中设置。

## 测试

```bash
./apps/agent/scripts/test.sh
```

架构设计位于 `apps/agent/docs/arch/`，实施计划位于 `apps/agent/docs/plan/`。
