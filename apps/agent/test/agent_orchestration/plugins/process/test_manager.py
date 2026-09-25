import asyncio
import os
import time

import pytest

from apps.agent.src.agent_orchestration.plugins.process import ProcessOperationError
from apps.agent.test.agent_orchestration.plugins.process.helpers import (
    create_manager,
    wait_for_status,
)


pytestmark = pytest.mark.skipif(os.name != "posix", reason="requires process groups")


def test_start快速返回并自动完成且合并日志(tmp_path):
    async def run():
        manager, events, _, _ = create_manager(tmp_path)
        await manager.start_accepting()
        before = time.monotonic()
        started = await manager.start(
            "printf 'out\n'; printf 'err\n' >&2; sleep 0.15",
            origin_task_id="task-1",
        )
        elapsed = time.monotonic() - before
        completed = await wait_for_status(manager, started.process_id, "completed")
        logs = await manager.read_logs(started.process_id)
        await manager.shutdown()
        return elapsed, started, completed, logs, events

    elapsed, started, completed, logs, events = asyncio.run(run())

    assert elapsed < 0.12
    assert started.status == "running"
    assert started.origin_task_id == "task-1"
    assert completed.exit_code == 0
    assert "out\n" in logs["content"]
    assert "err\n" in logs["content"]
    assert [event.status for event in events] == ["running", "completed"]
    assert os.stat(started.log_path).st_mode & 0o777 == 0o600


def test_nonzero_exit自动失败且list_get无需刷新状态(tmp_path):
    async def run():
        manager, events, _, _ = create_manager(tmp_path)
        await manager.start_accepting()
        started = await manager.start("exit 7")
        failed = await wait_for_status(manager, started.process_id, "failed")
        listed = await manager.list()
        await manager.shutdown()
        return failed, listed, events

    failed, listed, events = asyncio.run(run())

    assert failed.exit_code == 7
    assert listed["processes"][0].status == "failed"
    assert [event.status for event in events] == ["running", "failed"]


def test_logs分页保持utf8边界且eof_cursor可读取追加内容(tmp_path):
    async def run():
        manager, _, _, workspace = create_manager(
            tmp_path, default_log_page_bytes=4, max_log_page_bytes=16
        )
        await manager.start_accepting()
        gate = workspace / "gate"
        command = (
            "printf 'a你'; while [ ! -f gate ]; do sleep 0.02; done; "
            "printf '后来'; sleep 0.05"
        )
        started = await manager.start(command)
        async with asyncio.timeout(2):
            while True:
                first = await manager.read_logs(started.process_id, limit_bytes=3)
                if first["content"]:
                    break
                await asyncio.sleep(0.01)
        assert first["content"] == "a"
        second = await manager.read_logs(
            started.process_id, cursor=first["next_cursor"], limit_bytes=4
        )
        assert second["content"] == "你"
        gate.touch()
        await wait_for_status(manager, started.process_id, "completed")
        later = await manager.read_logs(
            started.process_id, cursor=second["next_cursor"], limit_bytes=16
        )
        await manager.shutdown()
        return later

    assert asyncio.run(run())["content"] == "后来"


def test_log_hard_limit不会阻塞进程并发布截断状态(tmp_path):
    async def run():
        manager, events, _, _ = create_manager(tmp_path, max_log_bytes=32)
        await manager.start_accepting()
        started = await manager.start(
            "dd if=/dev/zero bs=1024 count=128 2>/dev/null; exit 0"
        )
        completed = await wait_for_status(manager, started.process_id, "completed")
        size = os.path.getsize(started.log_path)
        await manager.shutdown()
        return completed, size, events

    completed, size, events = asyncio.run(run())
    assert completed.log_truncated is True
    assert size == 32
    assert events[0].status == "running"
    assert any(event.log_truncated for event in events[1:])


def test_first_event发布期间仍持续排空pipe且不倒序发布(tmp_path):
    async def run():
        manager, events, _, _ = create_manager(
            tmp_path, max_log_bytes=512 * 1024
        )
        original_publish = manager._publish_snapshot
        publishing = asyncio.Event()
        release = asyncio.Event()

        async def delayed_publish(snapshot):
            if not events:
                publishing.set()
                await release.wait()
            await original_publish(snapshot)

        manager._publish_snapshot = delayed_publish
        await manager.start_accepting()
        starting = asyncio.create_task(
            manager.start("head -c 262144 /dev/zero")
        )
        await asyncio.wait_for(publishing.wait(), timeout=2)
        async with asyncio.timeout(2):
            while next(iter(manager._records.values())).log_bytes < 128 * 1024:
                await asyncio.sleep(0.01)
        release.set()
        started = await starting
        completed = await wait_for_status(manager, started.process_id, "completed")
        await manager.shutdown()
        return events, completed

    events, completed = asyncio.run(run())
    assert completed.exit_code == 0
    assert [event.status for event in events] == ["running", "completed"]


def test_log截断更新不会晚于terminal更新(tmp_path):
    async def run():
        manager, events, _, _ = create_manager(tmp_path, max_log_bytes=8)
        original_publish = manager._publish_snapshot

        async def slow_truncation(snapshot):
            if snapshot.status == "running" and snapshot.log_truncated:
                await asyncio.sleep(0.1)
            await original_publish(snapshot)

        manager._publish_snapshot = slow_truncation
        await manager.start_accepting()
        started = await manager.start(
            "head -c 65536 /dev/zero; exit 0"
        )
        completed = await wait_for_status(manager, started.process_id, "completed")
        await manager.shutdown()
        return events, completed

    events, completed = asyncio.run(run())
    assert completed.log_truncated is True
    assert [(event.status, event.log_truncated) for event in events] == [
        ("running", False),
        ("running", True),
        ("completed", True),
    ]


def test_group退出后日志reader不会让supervisor无限等待(tmp_path):
    async def run():
        manager, _, _, _ = create_manager(
            tmp_path, terminate_grace_seconds=0.05
        )
        reader = asyncio.create_task(asyncio.sleep(30))
        before = time.monotonic()
        await manager._finish_reader(reader)
        elapsed = time.monotonic() - before
        await manager.shutdown()
        return reader, elapsed

    reader, elapsed = asyncio.run(run())
    assert reader.cancelled() is True
    assert elapsed < 0.5


def test_running_limit与终态释放配额(tmp_path):
    async def run():
        manager, _, _, _ = create_manager(tmp_path, max_running_processes=1)
        await manager.start_accepting()
        first = await manager.start("sleep 30")
        with pytest.raises(ProcessOperationError, match="process_limit_reached"):
            await manager.start("exit 0")
        await manager.stop(first.process_id)
        second = await manager.start("exit 0")
        await wait_for_status(manager, second.process_id, "completed")
        await manager.shutdown()
        return first, second

    first, second = asyncio.run(run())
    assert first.process_id != second.process_id


def test_terminal记录裁剪后list仍按稳定cursor分页(tmp_path):
    async def run():
        manager, _, _, _ = create_manager(tmp_path, max_terminal_records=2)
        await manager.start_accepting()
        started = []
        for _ in range(3):
            item = await manager.start("exit 0")
            await wait_for_status(manager, item.process_id, "completed")
            started.append(item)
        first_page = await manager.list(page_size=1)
        second_page = await manager.list(
            cursor=first_page["next_cursor"], page_size=1
        )
        with pytest.raises(ProcessOperationError, match="process_not_found"):
            await manager.get(started[0].process_id)
        await manager.shutdown()
        return started, first_page, second_page

    started, first_page, second_page = asyncio.run(run())
    assert [item.process_id for item in first_page["processes"]] == [
        started[2].process_id
    ]
    assert first_page["has_more"] is True
    assert first_page["next_cursor"]
    assert [item.process_id for item in second_page["processes"]] == [
        started[1].process_id
    ]
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None


def test_spawn失败返回稳定错误并清理日志(tmp_path, monkeypatch):
    async def fail_spawn(*args, **kwargs):
        del args, kwargs
        raise OSError("private operating system detail")

    async def run():
        manager, _, _, _ = create_manager(tmp_path)
        await manager.start_accepting()
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fail_spawn)
        with pytest.raises(ProcessOperationError) as captured:
            await manager.start("exit 0")
        listed = await manager.list()
        logs = tuple(manager.processes_dir.iterdir())
        await manager.shutdown()
        return captured.value, listed, logs

    error, listed, logs = asyncio.run(run())
    assert error.code == "spawn_failed"
    assert "private operating system detail" not in str(error)
    assert listed["processes"] == []
    assert logs == ()


def test_stop终止进程组并且重复调用幂等(tmp_path):
    async def run():
        manager, events, _, workspace = create_manager(
            tmp_path, terminate_grace_seconds=0.2
        )
        await manager.start_accepting()
        child_file = workspace / "child.pid"
        started = await manager.start(
            f"sleep 30 & echo $! > {child_file.name}; wait"
        )
        async with asyncio.timeout(2):
            while not child_file.exists():
                await asyncio.sleep(0.01)
        child_pid = int(child_file.read_text().strip())
        stopped = await manager.stop(started.process_id)
        again = await manager.stop(started.process_id)
        await manager.shutdown()
        return started, stopped, again, child_pid, events

    started, stopped, again, child_pid, events = asyncio.run(run())
    assert stopped.status == "stopped"
    assert again == stopped
    assert events[-1].status == "stopped"
    with pytest.raises(ProcessLookupError):
        os.kill(started.pid, 0)
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_quiesce停止全部并拒绝新的start(tmp_path):
    async def run():
        manager, _, _, _ = create_manager(
            tmp_path, terminate_grace_seconds=0.3
        )
        await manager.start_accepting()
        one = await manager.start("trap '' TERM; sleep 30")
        two = await manager.start("trap '' TERM; sleep 30")
        await asyncio.sleep(0.05)
        before = time.monotonic()
        await manager.quiesce()
        quiesce_elapsed = time.monotonic() - before
        with pytest.raises(ProcessOperationError, match="tool_unavailable"):
            await manager.start("exit 0")
        await manager.drain()
        snapshots = await manager.list()
        await manager.shutdown()
        return one, two, snapshots, quiesce_elapsed

    one, two, listed, quiesce_elapsed = asyncio.run(run())
    assert quiesce_elapsed < 0.2
    assert {item.process_id for item in listed["processes"]} == {
        one.process_id,
        two.process_id,
    }
    assert {item.status for item in listed["processes"]} == {"stopped"}


def test_start首次event失败会回滚进程和记录(tmp_path):
    async def run():
        manager, _, tasks, _ = create_manager(
            tmp_path, terminate_grace_seconds=0.2
        )
        captured = []

        async def fail(snapshot):
            captured.append(snapshot)
            raise RuntimeError("publish failed")

        manager._publish_snapshot = fail
        await manager.start_accepting()
        with pytest.raises(RuntimeError, match="publish failed"):
            await manager.start("sleep 30")
        listed = await manager.list()
        await manager.shutdown()
        return captured[0].pid, listed, tasks

    pid, listed, tasks = asyncio.run(run())
    assert listed["processes"] == []
    assert all(task.done() for task in tasks)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_start在首次event提交期间取消仍完成接管(tmp_path):
    async def run():
        manager, events, _, _ = create_manager(tmp_path)
        original_publish = manager._publish_snapshot
        publishing = asyncio.Event()
        release = asyncio.Event()

        async def delayed_publish(snapshot):
            publishing.set()
            await release.wait()
            await original_publish(snapshot)

        manager._publish_snapshot = delayed_publish
        await manager.start_accepting()
        starting = asyncio.create_task(manager.start("sleep 30"))
        await asyncio.wait_for(publishing.wait(), timeout=2)
        starting.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        listed = await manager.list()
        assert len(listed["processes"]) == 1
        running = listed["processes"][0]
        await manager.stop(running.process_id)
        await manager.shutdown()
        return running, events

    running, events = asyncio.run(run())
    assert running.status == "running"
    assert [event.status for event in events] == ["running", "stopped"]
    with pytest.raises(ProcessLookupError):
        os.kill(running.pid, 0)


def test_quiesce与spawn竞争时回滚尚未提交的进程(tmp_path, monkeypatch):
    async def run():
        manager, _, _, _ = create_manager(
            tmp_path, terminate_grace_seconds=0.2
        )
        await manager.start_accepting()
        original_spawn = asyncio.create_subprocess_exec
        entered = asyncio.Event()
        release = asyncio.Event()
        spawned = []

        async def delayed_spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            process = await original_spawn(*args, **kwargs)
            spawned.append(process)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
        starting = asyncio.create_task(manager.start("sleep 30"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        await manager.quiesce()
        draining = asyncio.create_task(manager.drain())
        await asyncio.sleep(0.05)
        assert draining.done() is False
        release.set()
        with pytest.raises(ProcessOperationError, match="tool_unavailable"):
            await starting
        await draining
        listed = await manager.list()
        await manager.shutdown()
        return spawned[0].pid, listed, tuple(manager.processes_dir.iterdir())

    pid, listed, logs = asyncio.run(run())
    assert listed["processes"] == []
    assert logs == ()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_spawn等待期间调用取消仍回滚已创建进程(tmp_path, monkeypatch):
    async def run():
        manager, _, _, _ = create_manager(
            tmp_path, terminate_grace_seconds=0.2
        )
        await manager.start_accepting()
        original_spawn = asyncio.create_subprocess_exec
        entered = asyncio.Event()
        release = asyncio.Event()
        spawned = []

        async def delayed_spawn(*args, **kwargs):
            entered.set()
            await release.wait()
            process = await original_spawn(*args, **kwargs)
            spawned.append(process)
            return process

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed_spawn)
        starting = asyncio.create_task(manager.start("sleep 30"))
        await asyncio.wait_for(entered.wait(), timeout=2)
        starting.cancel()
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await starting
        listed = await manager.list()
        await manager.shutdown()
        return spawned[0].pid, listed, tuple(manager.processes_dir.iterdir())

    pid, listed, logs = asyncio.run(run())
    assert listed["processes"] == []
    assert logs == ()
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_stop取消后supervisor仍升级kill并收束(tmp_path):
    async def run():
        manager, _, _, _ = create_manager(
            tmp_path, terminate_grace_seconds=0.1
        )
        await manager.start_accepting()
        started = await manager.start("trap '' TERM; sleep 30")
        await asyncio.sleep(0.05)
        stopping = asyncio.create_task(manager.stop(started.process_id))
        await asyncio.sleep(0.03)
        stopping.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stopping
        stopped = await wait_for_status(
            manager, started.process_id, "stopped", timeout=2
        )
        await manager.shutdown()
        return started, stopped

    started, stopped = asyncio.run(run())
    assert stopped.stop_reason == "requested"
    with pytest.raises(ProcessLookupError):
        os.kill(started.pid, 0)


def test_leader退出但同组child存活时保持running直到stop(tmp_path):
    async def run():
        manager, _, _, workspace = create_manager(
            tmp_path, terminate_grace_seconds=0.2
        )
        await manager.start_accepting()
        child_file = workspace / "orphan.pid"
        started = await manager.start(
            f"sleep 30 & echo $! > {child_file.name}; exit 0"
        )
        async with asyncio.timeout(2):
            while not child_file.exists():
                await asyncio.sleep(0.01)
        async with asyncio.timeout(2):
            while True:
                running = await manager.get(started.process_id)
                if running.exit_code == 0:
                    break
                await asyncio.sleep(0.01)
        stopped = await manager.stop(started.process_id)
        child_pid = int(child_file.read_text().strip())
        await manager.shutdown()
        return running, stopped, child_pid

    running, stopped, child_pid = asyncio.run(run())
    assert running.status == "running"
    assert running.exit_code == 0
    assert stopped.status == "stopped"
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)


def test_workdir必须位于workspace内(tmp_path):
    async def run():
        manager, _, _, _ = create_manager(tmp_path)
        await manager.start_accepting()
        with pytest.raises(ProcessOperationError, match="invalid_workdir"):
            await manager.start("exit 0", workdir=str(tmp_path))
        await manager.shutdown()

    asyncio.run(run())
