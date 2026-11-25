import asyncio

import pytest

from agent_app.application.agent_runtime import AgentRuntime, SubmissionConflictError
from apps.agent.test.agent_app.application.test_agent_runtime import RuntimeFactory, SessionStub, make_config


def test_search_mode_and_clock_participate_in_submission_idempotency(tmp_path, monkeypatch):
    received = []
    original = SessionStub.submit
    async def submit(self, prompt, input_images=None, *, task_id=None, request_options=None):
        received.append(request_options)
        return await original(self, prompt, input_images, task_id=task_id)
    monkeypatch.setattr(SessionStub, "submit", submit)
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"), session_factory=RuntimeFactory())
        await runtime.start()
        try:
            await runtime.create_session(tmp_path, "search")
            options = {"web_search": True, "deep_search": False, "local_time": "2026-10-23T20:00:00+08:00"}
            first = await runtime.submit(tmp_path, "search", "hello", submission_id="same", request_options=options)
            second = await runtime.submit(tmp_path, "search", "hello", submission_id="same", request_options=dict(options))
            assert first == second and len(received) == 1
            assert received[0] == options
            for changes in ({"web_search": False}, {"deep_search": True}, {"local_time": "2026-10-24T20:00:00+08:00"}):
                with pytest.raises(SubmissionConflictError):
                    await runtime.submit(tmp_path, "search", "hello", submission_id="same", request_options={**options, **changes})
        finally:
            await runtime.stop()
    asyncio.run(run())


@pytest.mark.parametrize("field", ["web_search", "deep_search"])
@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_invalid_search_options_rejected_before_loading_session(tmp_path, field, value):
    async def run():
        factory = RuntimeFactory()
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"), session_factory=factory)
        await runtime.start()
        try:
            # A missing session must not hide the input error or trigger loading.
            with pytest.raises(ValueError, match=field):
                await runtime.submit(tmp_path, "missing", "hello", submission_id="invalid",
                    request_options={field: value})
        finally:
            await runtime.stop()
    asyncio.run(run())


def test_default_options_retry_keeps_absence_and_explicit_false_conflicts(tmp_path, monkeypatch):
    received = []
    original = SessionStub.submit
    async def submit(self, prompt, input_images=None, *, task_id=None, request_options=None):
        received.append(request_options)
        return await original(self, prompt, input_images, task_id=task_id)
    monkeypatch.setattr(SessionStub, "submit", submit)
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"), session_factory=RuntimeFactory())
        await runtime.start()
        try:
            await runtime.create_session(tmp_path, "search")
            first = await runtime.submit(tmp_path, "search", "hello", submission_id="same")
            retry = await runtime.submit(tmp_path, "search", "hello", submission_id="same", request_options={})
            assert retry == first and received == [None]
            with pytest.raises(SubmissionConflictError):
                await runtime.submit(tmp_path, "search", "hello", submission_id="same", request_options={"web_search": False})
        finally:
            await runtime.stop()
    asyncio.run(run())
