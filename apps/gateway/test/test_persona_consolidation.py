import asyncio
import pytest

from agent_app.application import AgentRuntime
from agent_app.contracts.persona import PersonaConsolidationRequest
from agent_app.plugins.persona.store import PersonaStore
from agent_app.plugins.persona.config import PersonaConfig
from apps.agent.test.agent_app.application.test_agent_runtime import RuntimeFactory
from apps.agent.test.agent_app.application.test_persona_consolidation import settings, history, Model, wire
from apps.gateway.src.protocol.methods import GatewayMethods
from apps.gateway.src.protocol.errors import GatewayRpcError


@pytest.mark.parametrize("status,reason", [("accepted", None), ("already_running", None), ("skipped", "busy")])
def test_rpc_forwards_old_session_without_waiting_for_a_model(status, reason):
    class Runtime:
        async def request_persona_consolidation(self, workspace_path, session_id):
            self.received = workspace_path, session_id
            return PersonaConsolidationRequest(session_id, status, "request" if reason is None else None, reason)
    async def run():
        runtime = Runtime()
        methods = GatewayMethods(runtime)
        result = await methods.dispatch("session.consolidate_persona", {
            "workspace_path": "/workspace", "session_id": "old",
        }, set())
        assert result == {"session_id": "old", "status": status,
                          "request_id": "request" if reason is None else None, "reason": reason}
        assert runtime.received == ("/workspace", "old")
    asyncio.run(run())


def test_rpc_real_application_background_job_is_independent_of_new_chat(tmp_path, monkeypatch):
    async def run():
        gate = asyncio.Event()
        model = Model(gate)
        wire(monkeypatch, model)
        config = settings(tmp_path)
        api = AgentRuntime(config_loader=lambda: config, session_factory=RuntimeFactory())
        await api.start()
        try:
            identity = await history(api, tmp_path)
            methods = GatewayMethods(api)
            params = {"workspace_path": str(tmp_path), "session_id": "old"}
            result = await asyncio.wait_for(methods.dispatch("session.consolidate_persona", params, set()), 1)
            assert result["status"] == "accepted"
            await model.started.wait()
            created = await methods.dispatch("session.create", {
                "workspace_path": str(tmp_path), "session_id": "new", "load_runtime": False,
            }, set())
            assert created["session_id"] == "new"
            gate.set()
            await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
            assert PersonaStore(config.icarus_data_dir, "default", PersonaConfig()).processed_through(identity) == 3
            with pytest.raises(GatewayRpcError):
                await methods.dispatch("session.consolidate_persona", {**params, "session_id": "missing"}, set())
            with pytest.raises(GatewayRpcError):
                await methods.dispatch("session.consolidate_persona", {**params, "extra": True}, set())
        finally:
            await api.stop()
    asyncio.run(run())
