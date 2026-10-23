# 联网搜索验证记录

验证日期：2026-10-23 至 2026-10-24；对照基线：`57e4a41`。
原规格与适配范围见 [实施计划](plan.md)。本记录对应本地验证，未执行推送或部署。

## 已通过的检查

| 范围 | 结果 | 覆盖内容 |
| --- | --- | --- |
| Agent 搜索及关联检查，最终两组 | 28 + 29 passed | 管线、真实插件图、取消、缺少 Key、超时、关闭时零请求、动态时间、幂等、输入队列、公开更新、依赖边界、发现与懒加载 |
| Gateway 完整测试 | 36 passed | 请求选项严格校验、旧客户端兼容与既有 RPC 行为 |
| WebUI 搜索专项，最终复跑 | 8 passed | 开关与排队快照、本地时间、来源与引用、历史、停止、查询轮播、失败降级、移动端布局 |
| WebUI 静态检查 | 通过 | `pnpm typecheck`、`pnpm lint`、`pnpm build`；构建保留既有 chunk 大小提示 |
| Agent 类型与编译 | 通过 | 15 个受影响文件 mypy；最终修正后再次检查新增插件与契约的 7 个文件；compileall |
| 文档与补丁 | 通过 | 本功能文档的相对链接检查、`git diff --check` |

Agent 最终两组分别检查：

```text
test/agent_app/plugins/web_search/
test/agent_app/application/test_search_submission.py
test/icarus/test_import_boundaries.py
test/icarus/runtime/test_discovery_resolver.py

test/agent_app/plugins/user_input/
test/agent_app/plugins/runtime_update/
test/agent_app/application/test_runtime_update_stream.py
test/test_packaging.py::test_installed_discovery_keeps_plugins_lazy
```

Windows 执行这些检查时设置仓库根目录与 Agent `src` 的 `PYTHONPATH`，并开启
`PYTHONUTF8=1`。WebUI 使用独立 Vite 实例、Edge 和模拟 Gateway/HTTP；已查看
390px 与 1440px 截图。没有向第三方 favicon 服务发出请求。

## 应用全套与基线对照

Agent 全套首次运行得到 **1027 passed、55 failed、36 skipped**。其中新增插件导致的
发现数量断言已从 14 更新为 15，随后单项和最终关联检查通过。其余 54 个失败用
`git archive HEAD` 导出的原始代码、同一 Python 依赖环境逐项复跑：50 个失败复现；
4 个 OpenKB compose 测试在隔离归档中通过，工作区失败涉及本地配置装载与
`ICARUS_DATA_DIR` 路径。未修改这些无关测试或服务配置，也未把全套标为通过。

WebUI 全套得到 **223 passed、14 failed、13 skipped、1 error**。将 14 个失败的测试身份
逐项在基线 WebUI 上重跑，得到 **14 failed、1 error**。对照使用相同依赖与 Edge，
独立 Vite 实例指向归档源码，workspace 包映射到归档目录；没有改动测试或浏览器行为。
失败分布如下：

- 4 个聊天布局参数用例：既有历史 fixture 缺少 `session_id`；附带的截图清理错误也复现。
- 3 个记忆页面用例：焦点、滚动与错误提示断言。
- 7 个安装/启动脚本用例：Windows 的 POSIX 工具与模拟可执行文件行为。

搜索专项最终再次独立运行，8 项全部通过。此次保留无关失败，没有增加 skip 或改写断言。

## 实际服务验收范围

外部搜索、Fetch 与规划模型均使用隔离 fixture；尚未执行真实 Key 或付费模型验收。
按 [WebUI README](../../../README.md#启动) 配置 `ICARUS_WEB_SEARCH_API_KEY`，更新并
重启 Gateway 后，仍需确认实际供应商/中转支持两个接口，以及真实模型的引用遵循情况。

当前为每轮一次有预算的多查询检索，尚未实现模型判断资料不足后的自动追加检索。
默认最多 2 条查询、3 页；深度最多 5 条查询、6 页；来源不足不会补造。
费用显示仅为官方成功接口调用估算，不包含规划和回答模型费用。
