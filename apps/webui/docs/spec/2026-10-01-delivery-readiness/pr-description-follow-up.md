# feat(webui): refine workspace navigation and chat interactions

完善 PR #6 之后的 WebUI 体验。记忆列表固定每页 15 条，侧栏收起和窗口缩放保留卡片、分页及编辑状态；知识库桌面导航可展开或收起，窄屏保留分类快捷入口。对话支持 Markdown 代码高亮、代码及完整回答复制，并按需加载可折叠的思考明细。

对话同时包含消息队列、当前任务引导、稳定滚动跟随和未知结果重试处理。Agent/Gateway 配套提供模型生成的会话标题及软删除接口；Mem0 在服务端先筛选、排序再分页，并保留 Agent 来源属性。实现与功能测试放在相同逻辑提交中。

## 验证

- 本机 WSL2：WebUI 全套 170 passed、13 skipped；受影响浏览器专项 97 passed，记忆列表专项 24 passed。
- WSL 生产入口与真实服务：5 passed，包含 Mem0 修改/历史/分页、OpenKB 库生命周期、Gateway 真实模型回复/标题/历史/重连/停止/删除。
- Agent 全套 718 passed；Gateway 全套 19 passed；Mem0 分页与序列化 3 passed。
- ESLint、完整 Prettier、TypeScript、Vite build、Node/Python 编译和 diff 检查通过。

完整环境与覆盖边界见 `apps/webui/docs/spec/2026-10-01-delivery-readiness/acceptance-2026-10-05.md`。Playwright 在 Ubuntu 26.04 上使用其 Ubuntu 24.04 Linux 浏览器构建。本次没有执行其余 8 项真实服务/模型用例；远端 CI 待推送后验证。

## 兼容与边界

记忆分页需要配套 Mem0 管理接口，会话标题和删除需要配套 Gateway/Agent。仍是单用户工作台；记忆查询快照最多 1000 条，待发送队列仅保存在当前页面。未扩大到知识版本恢复、多租户或全供应商兼容验证。
