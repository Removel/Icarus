"""Actual tool lifetime remains observable after callers cancel their wait."""

import asyncio
from concurrent.futures import Future
import threading

import pytest

from icarus.core.models.messages import ToolCall, ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult, ToolExecutor, ToolRegistry


class BlockingTool(BaseTool):
    def __init__(self, started, release):
        self.started, self.release = started, release

    @property
    def definition(self):
        return ToolDefinition("blocking", "fixture", {"type": "object", "properties": {}})

    def invoke(self, arguments, **execution):
        self.started.set()
        assert self.release.wait(3)
        return ToolExecutionResult(True, output="finished")


def test_cancelled_sync_tool_stays_pending_until_thread_really_exits():
    async def run():
        from icarus.core.tools.execution_tracker import ToolExecutionTracker

        tracker = ToolExecutionTracker()
        started, release = threading.Event(), threading.Event()
        registry = ToolRegistry()
        registry.register(BlockingTool(started, release))
        executor = ToolExecutor(registry, tracker=tracker).snapshot()
        task = asyncio.create_task(executor.aexecute(ToolCall("call", "blocking", {})))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert tracker.pending_count == 1
            waiter = asyncio.create_task(tracker.drain())
            await asyncio.sleep(0)
            assert not waiter.done()
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert tracker.pending_count == 1
        finally:
            release.set()
            await tracker.drain()
        assert tracker.pending_count == 0

    asyncio.run(run())


def test_async_tool_ignoring_cancellation_stays_pending():
    async def run():
        from icarus.core.tools.execution_tracker import ToolExecutionTracker

        started, release = asyncio.Event(), asyncio.Event()

        class AsyncTool(BlockingTool):
            async def ainvoke(self, arguments, **execution):
                started.set()
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    await release.wait()
                return ToolExecutionResult(True, output="finished")

        tracker = ToolExecutionTracker()
        registry = ToolRegistry()
        registry.register(AsyncTool(None, None))
        executor = ToolExecutor(registry, tracker=tracker)
        executor.ASYNC_CANCEL_GRACE_SECONDS = 0.01
        task = asyncio.create_task(executor.aexecute(ToolCall("call", "blocking", {})))
        try:
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert tracker.pending_count == 1
        finally:
            release.set()
            await tracker.drain()
        assert tracker.pending_count == 0

    asyncio.run(run())


def test_tracker_can_wait_for_completion_from_another_thread():
    async def run():
        from icarus.core.tools.execution_tracker import ToolExecutionTracker

        tracker = ToolExecutionTracker()
        future = Future()
        tracker.track(future)
        assert tracker.pending_count == 1
        waiter = asyncio.create_task(tracker.drain())
        await asyncio.to_thread(future.set_result, None)
        await asyncio.wait_for(waiter, 1)
        assert tracker.pending_count == 0

    asyncio.run(run())


def test_synchronous_executor_registers_real_call_lifetime():
    async def run():
        from icarus.core.tools.execution_tracker import ToolExecutionTracker

        tracker = ToolExecutionTracker()
        started, release = threading.Event(), threading.Event()
        registry = ToolRegistry()
        registry.register(BlockingTool(started, release))
        executor = ToolExecutor(registry, tracker=tracker)
        task = asyncio.create_task(asyncio.to_thread(executor.execute, ToolCall("sync", "blocking", {})))
        try:
            assert await asyncio.to_thread(started.wait, 1)
            assert tracker.pending_count == 1
        finally:
            release.set()
            assert (await task).success
        await tracker.drain()
        assert tracker.pending_count == 0

    asyncio.run(run())
