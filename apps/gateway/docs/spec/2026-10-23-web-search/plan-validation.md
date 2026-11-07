# 默认自主联网：Gateway 验证记录

日期：2026-11-05。实施基线：`96006ec`，工作分支 `feat/agent`。
方案见 [plan-agent-autonomy.md](plan-agent-autonomy.md)，当前契约见 [arch.md](arch.md)。

## 实现结果

`session.submit` 校验后以 `model_dump(exclude_unset=True)` 透传 request_options。
对象省略、空对象和只带时间均不补 web_search=false；显式 false 不被删除。
选项类型、未知字段和时间长度校验保持，未新增 RPC、搜索逻辑或数据库字段。

新增 `test/test_web_search_e2e.py` 使用真实 WebSocket、生产 Runtime/模型协议适配器、
工具执行器和临时 SQLite，只以本地 HTTP fixture 代替模型、Search/Fetch 和其它外部服务。
七种场景为缺省、空对象、仅时间、关闭、关闭加深度、模型不调用工具、缺 Key。
涵盖工具结果进入后续请求、来源历史回放、同 ID 同参数重试及改变开关返回 submission_conflict。

## 检查结果

| 检查 | 结果 |
| --- | --- |
| 改动前新增“仅传显式字段”测试 | 7 failed、1 passed，均为旧代码补入默认字段的行为差异 |
| Windows 选项与新 e2e 专项 | 28 passed（增加 socket 冲突断言前） |
| Windows Gateway 全套 | 77 passed in 23.15s（增加 socket 冲突断言前） |
| 原生 WSL `make test-gateway`，最终 socket 冲突断言在内 | **77 passed in 10.17s**，脚本 compileall 同时通过 |

WSL 最终副本为 `/home/guoshangyong/icarus-web-autonomy-9fj6v8ep`：从 HEAD archive 加本功能
源码/测试补丁构造，各应用独立 editable 环境，Python 3.14，只有注释空 .env，无开发机凭据或数据库。
原始输出位于该机器 `/tmp/icarus-web-gateway-test.log`。副本依赖初次缺少已声明的
typesafe-sdk，安装 `typesafe-sdk==0.7.2` 后重新执行；缺依赖时的失败不计通过。
12 个修改/新增源码及测试文件与主工作树逐文件 SHA 校验一致。

在 `/mnt/f` 挂载盘运行标准全套得到 76 passed、1 failed：
`test_prompt_e2e.py::test_prompt_contributor_to_model_and_private_instructions_survive_real_gateway_restart`
于 session.unload 超时。相同补丁在原生 WSL 全套通过，未改动该无关用例或放宽超时。

未部署、提交、推送，未访问真实模型/搜索服务。此记录证明默认策略与协议链路，不证明模型
自主判断质量或远端抓取服务可用性；这些边界与 Agent [验证记录](../../../../agent/docs/spec/2026-10-23-web-search/plan-validation.md) 一致。
