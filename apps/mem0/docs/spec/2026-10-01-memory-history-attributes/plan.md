# 记忆属性变更审计

2026-10-01。

更新历史继续使用 ADD / UPDATE / DELETE，不改变现有事件值。新增可选 `changes` 对象，记录 `expiration_date`、`category` 的 `{before, after}`；正文前后值继续由 `old_memory` / `new_memory` 表示。没有差异时写入空对象；历史旧记录返回 null，不能据此重建过去的暂停或恢复操作。

SQLite 在现有标准表上使用 ALTER TABLE 添加可空 TEXT 列，JSON 保存属性差异；更早的历史表迁移保持现有列数据。历史按实际更新时间（缺省创建时间）与 rowid 稳定排序。同步 Memory 与 AsyncMemory 使用相同记录内容。

验证包括真实 SQLite 持久化/重开、旧 schema 迁移、同步与异步属性更新历史。存储和 memory/main 专项 76 项通过；全库测试因 37 个可选供应商依赖收集错误中断（9 skipped），不声称全库通过。

部署前备份 history.db。旧版本迁移逻辑可能删除新增列，回滚必须保留数据库备份，不能把降级后的库当作完整审计备份。WebUI 不支持服务端历史分页，仍读取全量后展示分页。
