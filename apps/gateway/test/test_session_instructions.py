import asyncio

import pytest

from agent_app.application import AgentRuntime
from icarus.core.identity import SessionIdentity
from apps.agent.test.agent_app.application.test_agent_runtime import make_config
from apps.gateway.src.protocol.errors import GatewayRpcError, INVALID_PARAMS
from apps.gateway.src.protocol.methods import GatewayMethods


@pytest.mark.parametrize("instructions", [None, "", "\nCALLER-私人😀\n"])
def test_create_instructions_persist_but_are_not_public(tmp_path, instructions):
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"))
        await runtime.start()
        try:
            methods = GatewayMethods(runtime)
            result = await methods.dispatch("session.create", {"workspace_path": str(tmp_path), "session_id": "private", "load_runtime": False, "instructions": instructions}, set())
            assert "instructions" not in result
            history = await methods.dispatch("session.get_history", {"workspace_path": str(tmp_path), "session_id": "private"}, set())
            assert history["records"] == []
            assert history["history_cursor"] == 0
        finally:
            await runtime.stop()
    asyncio.run(run())


@pytest.mark.parametrize("instructions", [
    "x" * 8193,
    pytest.param("😀" * 8192, id="emoji-overflow"),
    "\ud800",
    123,
    [],
])
def test_invalid_instructions_are_rejected_before_create_without_echo(tmp_path, instructions):
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"))
        await runtime.start()
        try:
            with pytest.raises(GatewayRpcError) as caught:
                await GatewayMethods(runtime).dispatch("session.create", {"workspace_path": str(tmp_path), "session_id": "invalid", "load_runtime": False, "instructions": instructions}, set())
            assert caught.value.code == INVALID_PARAMS
            assert "\ud800" not in str(caught.value)
            assert not await runtime._store().list_session_ids(SessionIdentity.create(tmp_path).workspace_key)
        finally:
            await runtime.stop()
    asyncio.run(run())


@pytest.mark.parametrize("method,extra", [
    ("session.submit", {"prompt": "hello", "submission_id": "one"}),
    ("session.steer", {"task_id": "task", "prompt": "hello"}),
    ("session.get", {}),
])
def test_instructions_cannot_be_replaced_mid_session(tmp_path, method, extra):
    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"))
        with pytest.raises(GatewayRpcError) as caught:
            await GatewayMethods(runtime).dispatch(method, {"workspace_path": str(tmp_path), "session_id": "private", "instructions": None, **extra}, set())
    asyncio.run(run())


def test_failed_private_insert_does_not_leak_bound_parameters_in_logs(tmp_path, caplog):
    from sqlalchemy import text
    from apps.gateway.src.protocol.errors import INTERNAL_ERROR

    async def run():
        runtime = AgentRuntime(config_loader=lambda: make_config(tmp_path / "data"))
        await runtime.start()
        try:
            async with runtime._store()._engine.begin() as db:
                await db.execute(text("CREATE TRIGGER fail_private BEFORE INSERT ON conversation_updates WHEN NEW.sequence=0 BEGIN SELECT missing_private_insert_function(); END"))
            with pytest.raises(GatewayRpcError) as caught:
                await GatewayMethods(runtime).dispatch("session.create", {
                    "workspace_path": str(tmp_path), "session_id": "db-failed", "load_runtime": False,
                    "instructions": "SECRET-INSERT-PARAMETER",
                }, set())
            assert caught.value.code == INTERNAL_ERROR
            assert "SECRET-INSERT-PARAMETER" not in caplog.text
            assert not await runtime._store().session_exists(SessionIdentity.create(tmp_path, "db-failed"))
        finally:
            await runtime.stop()
    asyncio.run(run())
