import asyncio

import pytest

from apps.gateway.src.protocol.errors import GatewayRpcError
from apps.gateway.src.protocol.methods import GatewayMethods
from icarus.core.contracts.input import InputAccepted


def test_submit_validates_and_forwards_search_options_without_changing_prompt():
    class Runtime:
        async def submit(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs
            return InputAccepted("task", 0)
    async def run():
        runtime = Runtime()
        methods = GatewayMethods(runtime)
        params = {"workspace_path": "/workspace", "session_id": "session", "prompt": "hello",
            "submission_id": "one", "request_options": {"web_search": True, "deep_search": False,
                "local_time": "2026-10-23T20:00:00+08:00", "time_zone": "Asia/Shanghai"}}
        result = await methods.dispatch("session.submit", params, set())
        assert result["task_id"] == "task"
        assert runtime.args[-1] == "hello"
        assert runtime.kwargs["request_options"] == params["request_options"]
        for options in ({"web_search": "true"}, {"api_key": "must-not-be-accepted"}, {"deep_search": 1}, {"local_time": "x" * 101}):
            with pytest.raises(GatewayRpcError):
                await methods.dispatch("session.submit", {**params, "request_options": options}, set())
    asyncio.run(run())


@pytest.mark.parametrize("options", [None, {}, {"local_time": "2026-11-05T12:00:00+08:00"},
    {"time_zone": "Asia/Shanghai"}, {"deep_search": True}, {"web_search": False},
    {"web_search": True}, {"web_search": False, "deep_search": True}])
def test_submit_preserves_only_explicit_request_options(options):
    class Runtime:
        async def submit(self, *args, **kwargs):
            self.kwargs = kwargs
            return InputAccepted("task", 0)
    async def run():
        runtime = Runtime()
        params = {"workspace_path": "/workspace", "session_id": "session", "prompt": "hello", "submission_id": "one"}
        if options is not None:
            params["request_options"] = options
        await GatewayMethods(runtime).dispatch("session.submit", params, set())
        assert runtime.kwargs.get("request_options", {}) == (options or {})
    asyncio.run(run())


@pytest.mark.parametrize("field", ["web_search", "deep_search"])
@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_invalid_search_options_never_call_runtime(field, value):
    class Runtime:
        async def submit(self, *args, **kwargs):
            raise AssertionError("Invalid options must not reach Runtime")
    async def run():
        params = {"workspace_path": "/workspace", "session_id": "session", "prompt": "hello",
            "submission_id": "one", "request_options": {field: value}}
        with pytest.raises(GatewayRpcError):
            await GatewayMethods(Runtime()).dispatch("session.submit", params, set())
    asyncio.run(run())
