# 质量检查报告管理

2026-10-01。

报告继续由现有 lint 流程写入 wiki/reports。WebUI 通过现有 list.reports 枚举、page 读取报告，不创建浏览器本地档案，也不修改报告内容。

新增 POST /api/v1/report/delete，复用 PageRequest 的 kb/path 和 PageDeleteResponse。鉴权沿用 require_bearer_token。report_ops 只允许 reports 下单层名称，拒绝其他页面类型、目录穿越和符号链接；在 KB ingest lock 中重新检查并删除文件，不触碰知识页面、索引、引用和原始资料。不存在返回 404。

专项 7 项通过，涵盖真实临时库的列举/读取/删除、鉴权、路径限制与符号链接。全套 1249 passed、16 failed；失败包括原有 Windows 路径/权限、机器全局 Skill、watcher 与 api.py/api_helpers.py 既有文件行数限制。新增文件没有触发大小限制。
