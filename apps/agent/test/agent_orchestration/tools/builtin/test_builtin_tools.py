import asyncio
import time

import pytest

from apps.agent.src.agent_orchestration.tools.builtin import (
    BashTool,
    InsertTool,
    ReadTool,
    WriteTool,
    create_builtin_tools,
)


def test_builtin_tools_默认工具定义完整():
    tools = create_builtin_tools()

    assert [tool.definition.name for tool in tools] == [
        "read",
        "write",
        "insert",
        "bash",
    ]
    assert ReadTool().can_run_parallel({"path": "a.txt"}) is True
    assert WriteTool().can_run_parallel({"path": "a.txt", "content": ""}) is False
    assert InsertTool().can_run_parallel(
        {"path": "a.txt", "line": 1, "content": ""}
    ) is False
    assert BashTool().can_run_parallel({"command": "git status"}) is False
    assert BashTool().can_run_parallel(
        {"command": "git status", "parallel": True}
    ) is True


def test_read_write_insert_完成文件读写与插入(tmp_path):
    path = tmp_path / "nested" / "demo.txt"

    written = WriteTool().invoke({"path": str(path), "content": "line-1\nline-3\n"})
    inserted = InsertTool().invoke(
        {
            "path": str(path),
            "line": 2,
            "content": "line-2\n",
        }
    )
    read = ReadTool().invoke({"path": str(path)})

    assert written.success is True
    assert inserted.success is True
    assert read.output["content"] == "line-1\nline-2\nline-3\n"


def test_insert_行号越界返回统一失败结果(tmp_path):
    path = tmp_path / "demo.txt"
    path.write_text("only\n", encoding="utf-8")

    result = InsertTool().invoke(
        {"path": str(path), "line": 3, "content": "missing\n"}
    )

    assert result.success is False
    assert result.error == "line out of range: 3"


def test_read_文件不存在返回统一失败结果(tmp_path):
    result = ReadTool().invoke({"path": str(tmp_path / "missing.txt")})

    assert result.success is False
    assert result.error


def test_read_按行分页并返回下一偏移(tmp_path):
    path = tmp_path / "paged.txt"
    path.write_text("one\ntwo\nthree\n", encoding="utf-8")

    first = ReadTool().invoke(
        {"path": str(path), "offset": 1, "limit": 2}
    )
    second = ReadTool().invoke(
        {"path": str(path), "offset": 3, "limit": 2}
    )

    assert first.output["content"] == "one\ntwo\n"
    assert first.output["has_more"] is True
    assert first.output["next_offset"] == 3
    assert second.output["content"] == "three\n"
    assert second.output["has_more"] is False
    assert second.output["next_offset"] is None


@pytest.mark.parametrize("value", [0, -1, True, 2001])
def test_read_拒绝非法limit(tmp_path, value):
    path = tmp_path / "paged.txt"
    path.write_text("one\n", encoding="utf-8")

    with pytest.raises(ValueError):
        ReadTool().invoke({"path": str(path), "limit": value})


def test_bash_返回退出码标准输出和标准错误(tmp_path):
    success = BashTool().invoke(
        {
            "command": "printf hello",
            "workdir": str(tmp_path),
        }
    )
    failed = BashTool().invoke(
        {
            "command": "printf failure >&2; exit 7",
            "workdir": str(tmp_path),
        }
    )

    assert success.success is True
    assert success.output == {
        "exit_code": 0,
        "stdout": "hello",
        "stderr": "",
    }
    assert failed.success is False
    assert failed.output["exit_code"] == 7
    assert failed.error == "failure"


def test_bash_异步返回与同步路径相同结果(tmp_path):
    async def run():
        return await BashTool().ainvoke(
            {"command": "printf async", "workdir": str(tmp_path)}
        )

    result = asyncio.run(run())

    assert result.success is True
    assert result.output == {
        "exit_code": 0,
        "stdout": "async",
        "stderr": "",
    }


def test_bash_异步超时终止子进程(tmp_path):
    async def run():
        started_at = time.monotonic()
        result = await BashTool().ainvoke(
            {
                "command": "sleep 10",
                "workdir": str(tmp_path),
                "timeout": 0.05,
            }
        )
        return result, time.monotonic() - started_at

    result, elapsed = asyncio.run(run())

    assert result.success is False
    assert "timed out" in result.error
    assert elapsed < 2


def test_bash_framework超时与tool超时取较小值(tmp_path):
    started_at = time.monotonic()
    result = BashTool().invoke(
        {
            "command": "sleep 10",
            "workdir": str(tmp_path),
            "timeout": 5,
        },
        timeout_seconds=0.05,
    )

    assert result.success is False
    assert "0.05 seconds" in result.error
    assert time.monotonic() - started_at < 2


def test_bash_timeout终止同进程组的后台子进程(tmp_path):
    marker = tmp_path / "leaked.txt"
    result = BashTool().invoke(
        {
            "command": (
                f"(sleep 0.3; printf leaked > {marker.name}) & wait"
            ),
            "workdir": str(tmp_path),
        },
        timeout_seconds=0.05,
    )

    time.sleep(0.4)
    assert result.success is False
    assert result.metadata["disposition"] == "timed_out"
    assert marker.exists() is False


@pytest.mark.parametrize("async_mode", [False, True])
def test_bash_超过采集上限时终止并返回受限结果(tmp_path, async_mode):
    tool = BashTool(max_output_bytes=128)
    arguments = {
        "command": "while true; do printf 1234567890; done",
        "workdir": str(tmp_path),
    }

    if async_mode:
        result = asyncio.run(tool.ainvoke(arguments))
    else:
        result = tool.invoke(arguments)

    assert result.success is False
    assert result.metadata["disposition"] == "output_limit_exceeded"
    assert result.metadata["output_capture_incomplete"] is True
    assert len(result.output["stdout"].encode()) <= 128


def test_bash_异步取消终止子进程(tmp_path):
    async def run():
        tool = BashTool()
        task = asyncio.create_task(
            tool.ainvoke(
                {"command": "sleep 10", "workdir": str(tmp_path)}
            )
        )
        await asyncio.sleep(0.05)
        started_at = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.monotonic() - started_at

    assert asyncio.run(run()) < 2


def test_bash_终止超时后kill并回收子进程():
    class IgnoringProcess:
        def __init__(self) -> None:
            self.returncode = None
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        async def wait(self) -> int:
            if not self.killed:
                await asyncio.Event().wait()
            return self.returncode

    async def run():
        process = IgnoringProcess()
        original_grace = BashTool.TERMINATE_GRACE_SECONDS
        BashTool.TERMINATE_GRACE_SECONDS = 0.01
        try:
            await BashTool._terminate(process)
        finally:
            BashTool.TERMINATE_GRACE_SECONDS = original_grace
        return process

    process = asyncio.run(run())

    assert process.terminated is True
    assert process.killed is True
    assert process.returncode == -9
