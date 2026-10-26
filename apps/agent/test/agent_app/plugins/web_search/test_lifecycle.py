import asyncio

import pytest
import httpx

from agent_app.plugins.web_search.http_backend import SearchHttpBackend
from icarus.core.contracts.input import UserInputEvent, InputFinishedEvent
from icarus.core.models.messages import ToolCall
from icarus.core.run_control import TaskCancelRequestedEvent
from apps.agent.test.agent_app.plugins.web_search.test_tools import setup_plugin


@pytest.mark.parametrize("fetch", [False, True])
def test_cancel_reports_stopped_after_real_start_and_blocks_late_results(fetch):
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        entered = asyncio.Event()
        stopped = asyncio.Event()
        async def slow(*args):
            await args[-1]()
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        if fetch:
            backend.fetch = slow
        else:
            backend.search = slow
        call = ToolCall("call", "web_fetch" if fetch else "web_search",
            {"url": "https://example.com/a"} if fetch else {"query": "facts"})
        task = asyncio.create_task(executor.aexecute(call, task_id="task"))
        await entered.wait()
        plugin.task_channels.request_cancel("task", "stop")
        await plugin.on_stop("user-input", TaskCancelRequestedEvent(task_id="task", reason="stop"))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set() and events[-1].status == "stopped" and not events[-1].browsing
        assert events[1].browsing if fetch else events[1].status == "searching"
        await plugin.on_stop("user-input", InputFinishedEvent(task_id="task", status="cancelled"))
        assert plugin.tasks == {}
        await plugin.stop()
    asyncio.run(run())


def test_time_budget_excludes_idle_but_debits_failed_work_and_waiting():
    async def run():
        now = [0.0]
        plugin, backend, events, executor = await setup_plugin(deadline=3, clock=lambda: now[0])
        now[0] = 100  # Idle time before any tool must not spend the budget.
        original = backend.search
        async def work(*args):
            result = await original(*args)
            now[0] += 2
            return result
        backend.search = work
        first = await executor.aexecute(ToolCall("1", "web_search", {"query": "facts"}), task_id="task")
        assert first.success and first.output["budget"]["work_seconds_remaining"] == 1
        now[0] += 100  # Model thought time must not spend the remaining second.
        assert plugin.tasks["task"].remaining(now[0], 3) == 1
        state = plugin.tasks["task"]
        await state.lock.acquire()
        waiting = asyncio.create_task(executor.aexecute(ToolCall("2", "web_search", {"query": "followup"}), task_id="task"))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        now[0] += 2
        state.lock.release()
        result = await waiting
        assert not result.success and len(backend.calls) == 1
        assert state.remaining(now[0], 3) == 0
        await plugin.stop()
    asyncio.run(run())


def test_operation_timeout_includes_lock_wait_and_does_not_start_http():
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        await plugin.tasks["task"].lock.acquire()
        result = await plugin.build_tools()[0].ainvoke({"query": "facts"}, task_id="task", timeout_seconds=.05)
        assert not result.success and backend.calls == [] and len(events) == 1
        assert plugin.tasks["task"].used_seconds > 0
        plugin.tasks["task"].lock.release()
        await plugin.stop()
    asyncio.run(run())


def test_deep_call_and_content_budgets_and_task_isolation():
    async def run():
        plugin, backend, events, executor = await setup_plugin(deep=True)
        for i in range(5):
            result = await executor.aexecute(ToolCall(str(i), "web_search", {"query": str(i)}), task_id="task")
            assert result.success
        assert not (await executor.aexecute(ToolCall("6", "web_search", {"query": "six"}), task_id="task")).success
        for i in range(4):
            result = await executor.aexecute(ToolCall(f"f{i}", "web_fetch", {"url": f"https://example.com/{i}"}), task_id="task")
            assert result.success
        assert not (await executor.aexecute(ToolCall("f5", "web_fetch", {"url": "https://example.com/next"}), task_id="task")).success
        assert len([c for c in backend.calls if c[0] == "fetch"]) == 4
        await plugin.on_stop("user-input", InputFinishedEvent(task_id="task", status="completed"))
        channel = plugin.task_channels.create("next")
        channel.mark_preparing_context()
        channel.start_run("next-run")
        await plugin.on_input("user-input", UserInputEvent(task_id="next", prompt="new", request_options={"web_search": True}))
        result = await executor.aexecute(ToolCall("new", "web_fetch", {"url": "https://example.com/new"}), task_id="next")
        assert result.success and result.output["citeIndex"] == 1
        assert result.output["budget"]["fetches_remaining"] == 2
        await plugin.stop()
    asyncio.run(run())


def test_stop_is_bounded_even_when_backend_swallows_cancellation():
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        plugin.STOP_TIMEOUT = .02
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn(*args):
            await args[-1]()
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return backend.rows
        backend.search = stubborn
        task = asyncio.create_task(executor.aexecute(ToolCall("s", "web_search", {"query": "facts"}), task_id="task"))
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(plugin.stop(), .2)
        before = len(events)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert plugin.tasks == {} and len(events) == before
    asyncio.run(run())


def test_timeout_returns_without_waiting_for_a_backend_that_ignores_cancel():
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn(*args):
            await args[-1]()
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return backend.rows
        backend.search = stubborn
        task = asyncio.create_task(plugin.build_tools()[0].ainvoke({"query": "facts"}, task_id="task", timeout_seconds=.01))
        await entered.wait()
        done, _ = await asyncio.wait({task}, timeout=.1)
        try:
            assert task in done, "A timed out request must not wait for uncooperative transport cleanup"
            result = task.result()
            assert not result.success
            snapshot_count = len(events)
            release.set()
            await asyncio.sleep(.01)
            assert len(events) == snapshot_count and events[-1].sources == ()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
            await plugin.stop()
    asyncio.run(run())


def test_waiting_operation_cannot_hide_an_active_fetch_or_double_debit_time():
    async def run():
        now = [0.0]
        plugin, backend, events, _ = await setup_plugin(clock=lambda: now[0])
        entered, release = asyncio.Event(), asyncio.Event()
        async def fetch(*args):
            await args[-1]()
            entered.set()
            await release.wait()
            return "Page", "body facts"
        backend.fetch = fetch
        first = asyncio.create_task(plugin.build_tools()[1].ainvoke({"url": "https://example.com/a"}, task_id="task"))
        await entered.wait()
        now[0] = 1
        waiting = asyncio.create_task(plugin.build_tools()[0].ainvoke({"query": "facts"}, task_id="task", timeout_seconds=.02))
        result = await waiting
        assert not result.success and events[-1].browsing
        assert plugin.tasks["task"].searches == 0
        now[0] = 3
        release.set()
        assert (await first).success
        assert plugin.tasks["task"].used_seconds == 3
        assert len(events[-1].pages) == 1 and not events[-1].browsing
        await plugin.stop()
    asyncio.run(run())


def test_cancel_before_request_slot_does_not_count_or_publish_progress():
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        waiting = asyncio.Event()
        async def before_slot(*args):
            waiting.set()
            await asyncio.Event().wait()
        backend.search = before_slot
        task = asyncio.create_task(executor.aexecute(ToolCall("s", "web_search", {"query": "facts"}), task_id="task"))
        await waiting.wait()
        plugin.task_channels.request_cancel("task", "stop")
        await plugin.on_stop("user-input", TaskCancelRequestedEvent(task_id="task", reason="stop"))
        with pytest.raises(asyncio.CancelledError):
            await task
        state = plugin.tasks["task"]
        assert state.searches == 0 and state.queries == [] and len(events) == 1
        await plugin.stop()
    asyncio.run(run())


def test_outer_stop_deadline_is_not_swallowed_and_old_state_cannot_publish():
    async def run():
        plugin, backend, events, _ = await setup_plugin()
        old_state = plugin.tasks["task"]
        entered, release = asyncio.Event(), asyncio.Event()
        async def close():
            entered.set()
            await release.wait()
        backend.aclose = close
        stop = asyncio.create_task(plugin.stop())
        await entered.wait()
        stop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await stop
        await plugin._emit("task", old_state, status="complete")
        assert plugin.tasks == {} and len(events) == 1
        assert not (await plugin.build_tools()[0].ainvoke({"query": "facts"}, task_id="task")).success
        release.set()
        await asyncio.sleep(0)
    asyncio.run(run())


def test_cross_task_request_gate_wait_spends_work_time_without_counting_http():
    async def run():
        plugin, _, events, executor = await setup_plugin()
        requests = []
        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"results": []})
        plugin.backend = SearchHttpBackend("https://search.example", "fixture", transport=httpx.MockTransport(transport))
        channel = plugin.task_channels.create("next")
        channel.mark_preparing_context()
        channel.start_run("next-run")
        await plugin.on_input("user-input", UserInputEvent(task_id="next", prompt="next", request_options={"web_search": True}))
        assert (await executor.aexecute(ToolCall("first", "web_search", {"query": "one"}), task_id="task")).success
        before = len(events)
        result = await plugin.build_tools()[0].ainvoke({"query": "two"}, task_id="next", timeout_seconds=.05)
        state = plugin.tasks["next"]
        assert not result.success and len(requests) == 1 and len(events) == before
        assert state.used_seconds > 0 and state.searches == 0 and state.queries == []
        await plugin.stop()
    asyncio.run(run())


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_input_finished_discards_old_response_even_after_task_identity_is_reused(status):
    async def run():
        plugin, backend, events, executor = await setup_plugin()
        entered, release = asyncio.Event(), asyncio.Event()
        async def stubborn(*args):
            await args[-1]()
            entered.set()
            while not release.is_set():
                try:
                    await release.wait()
                except asyncio.CancelledError:
                    continue
            return backend.rows
        backend.search = stubborn
        task = asyncio.create_task(executor.aexecute(ToolCall("s", "web_search", {"query": "old"}), task_id="task"))
        await entered.wait()
        await plugin.on_stop("user-input", InputFinishedEvent(task_id="task", status=status))
        with pytest.raises(asyncio.CancelledError):
            await task
        assert plugin.tasks == {}
        await plugin.on_input("user-input", UserInputEvent(task_id="task", prompt="replacement", request_options={"web_search": False}))
        before = len(events)
        release.set()
        await asyncio.sleep(.01)
        assert len(events) == before and plugin.tasks["task"].sources.snapshot() == ()
        assert not (await plugin.build_tools()[0].ainvoke({"query": "new"}, task_id="task")).success
        await plugin.stop()
    asyncio.run(run())
