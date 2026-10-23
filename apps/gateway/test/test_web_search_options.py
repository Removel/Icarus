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
