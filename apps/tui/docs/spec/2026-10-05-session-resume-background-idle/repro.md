# 有后台进程的 Session 无法重新进入（TUI `/resume` 误判非空闲）

日期：2026-10-05
涉及应用：`apps/tui`（缺陷所在）、`apps/agent`（后台工作计数的来源）

## 现象

Session 内有后台进程运行时，退出 TUI 后无法再通过 `/resume` 回到该 Session。

- 退出 TUI 后，Session 仍留在 Gateway 中，不被回收。
- 重开 TUI（不带 `--session-id`），输入 `/resume` 并选择该 Session，界面提示：

  ```text
  Unable to resume Session: Session <id> is not idle
  ```

- 只有 `icarus tui --session-id <id>` 能重新进入（该入口不检查空闲）。
- 只要后台进程还在，`/resume` 与 `/clear` 都会被拒绝。

## 复现步骤

1. 启动 Gateway，打开 TUI。
2. 让 Agent 使用 `background_process` 工具启动一个后台命令（例如 `sleep 3600`），确认启动成功。
3. 退出 TUI。
4. 重新打开 TUI（不带 `--session-id`），输入 `/resume`，选择刚才的 Session。
5. 观察到提示 `Unable to resume Session: Session <id> is not idle`。

## 根因

后台进程让 Session 的空闲判定永远为假：

1. `ProcessPlugin` 存在受管后台进程时，Plugin Runtime 的 `background_work_count > 0`。
2. Session 运行时快照汇总该计数，`RuntimeStatus.has_work` 为真，生命周期被置为 `running`
   （见 `apps/agent/src/application/runtime_status.py`、`apps/agent/src/application/agent_runtime.py`）。
3. TUI 的空闲判定把 `background_work_count` 也计入“非空闲”，并且只在生命周期属于
   `{ready, unloaded}` 时才认为空闲：

   `apps/tui/src/app.py` → `_status_is_idle()`

   ```python
   @staticmethod
   def _status_is_idle(status: dict[str, Any]) -> bool:
       if status.get("lifecycle") not in {"ready", "unloaded"}:
           return False
       if status.get("active_task_ids"):
           return False
       return all(
           int(status.get(field, 0)) == 0
           for field in (
               "queued_task_count",
               "pending_event_count",
               "pending_plugin_event_count",
               "background_work_count",
           )
       )
   ```

4. `_begin_session_operation()` 与 `_resume_session()` 用该判定拦截 `/clear` 与 `/resume`，
   于是只要有后台进程就始终拒绝。
5. 退出 TUI 不会卸载 Session：`_release_runtime_resources()` 只做 `discard_empty_session` +
   `close()`；Gateway 的 `cleanup_idle_sessions()` 只回收 `ready` 状态，有后台进程的 Session 停在
   `running`，也无法靠“被回收”回到 `unloaded`。

后台进程是 Session 的业务工作，不代表用户正在对话。服务端用 `background_work_count` 防止空闲回收是正确的；
但用它阻止用户切换或恢复 Session，是把“有后台工作”误当成“用户会话忙”。

## 修复方案

放宽 TUI 的空闲判定：只把真正进行中的对话当作非空闲，后台工作不再阻塞。

`apps/tui/src/app.py`：

```python
@staticmethod
def _status_is_idle(status: dict[str, Any]) -> bool:
    if status.get("lifecycle") not in {"ready", "running", "unloaded"}:
        return False
    if status.get("active_task_ids"):
        return False
    return all(
        int(status.get(field, 0)) == 0
        for field in (
            "queued_task_count",
            "pending_event_count",
            "pending_plugin_event_count",
        )
    )
```

要点：

- 生命周期允许 `running`（只有后台工作时即为 `running`），显式排除 `loading` / `unloading` / `failed`。
- 移除 `background_work_count`；保留 `active_task_ids` 与排队/待处理事件计数，确保真正有对话任务在跑时
  仍然拒绝切换。

同步更新：

- `apps/tui/docs/spec/2026-09-02-tui-session-management/arch.md` 中关于空闲条件的描述。

## 影响与风险

- 修复后，只要有后台进程但没有进行中的对话，`/resume` 和 `/clear` 即可用。
- 切换 Session 时，原 Session 的后台进程仍归原 Session 所有，不应被 `discard_empty_session` 误删；
  只含后台进程、没有对话内容的 Session 是否算“空”，需要在实现时明确约定。

## 回归测试

在 `apps/tui/test/` 增加用例，构造 Session 状态：

- `lifecycle="running"`、`background_work_count=1`、`active_task_ids=[]` → 判定为空闲，`/resume` 应成功切换。
- `lifecycle="running"`、`active_task_ids=["task-1"]` → 判定为非空闲，`/resume` 应拒绝。
