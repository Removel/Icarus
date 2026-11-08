import asyncio
import json

from agent_app.application import AgentRuntime
from agent_app.plugins.persona.judge import EvolutionVerdict
from agent_app.plugins.persona.store import PersonaStore
from agent_app.plugins.persona.config import PersonaConfig
from apps.agent.test.agent_app.application.test_agent_runtime import RuntimeFactory, make_config
from icarus.core.models.messages import LLMResponse, Message, TextPart
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate
from datetime import UTC, datetime


class Model:
    def __init__(self, gate=None):
        self.gate = gate
        self.started = asyncio.Event()
        self.closed = False
        self.calls = []
        self.error = None

    async def ainvoke(self, messages, tools=None):
        self.calls.append(messages)
        self.started.set()
        if self.gate:
            await self.gate.wait()
        if self.error:
            raise self.error
        evidence = json.loads(messages[1].content[0].text)["dialogue"][0]["id"]
        return LLMResponse(Message("assistant", [TextPart(json.dumps([{
            "section": "voice", "note": "I keep replies concise.",
            "reason": "The user asked for lasting concision.", "evidence_ids": [evidence],
        }]))]))

    async def aclose(self):
        self.closed = True


class Judge:
    async def evolution_verdict(self, **kwargs):
        return EvolutionVerdict(0.9, 0.9, 0.9)

    async def aclose(self):
        pass


def settings(tmp_path):
    config = make_config(tmp_path / "data")
    config.get_model("perception").context_window = 128000
    config.runtime.plugin_config["persona"] = {"evolution_enabled": True, "jev_enabled": True}
    return config


async def history(api, workspace, session="old"):
    await api.create_session(workspace, session, load_runtime=False)
    identity = SessionIdentity.create(workspace, session)
    for kind, payload in [
        ("user.message", {"text": "以后回答请简洁一些"}),
        ("assistant.message", {"text": "好的", "step": 1}),
        ("task.finished", {"status": "completed"}),
    ]:
        await api._store().append_update(identity, RuntimeUpdate(
            workspace_key=identity.workspace_key, session_id=session, task_id="first",
            type=kind, payload=payload, occurred_at=datetime.now(UTC)))
    return identity


def wire(monkeypatch, model):
    from agent_app.application.services import persona_consolidation
    monkeypatch.setattr(persona_consolidation.LLMFactory, "create_llm", lambda *args, **kwargs: model)
    monkeypatch.setattr(persona_consolidation, "create_judge", lambda config: Judge())


def test_request_runs_in_background_and_old_session_remains_usable(tmp_path, monkeypatch):
    assert hasattr(AgentRuntime, "request_persona_consolidation"), "missing application consolidation request"
    async def run():
        gate = asyncio.Event()
        model = Model(gate)
        wire(monkeypatch, model)
        config = settings(tmp_path)
        api = AgentRuntime(config_loader=lambda: config, session_factory=RuntimeFactory())
        await api.start()
        try:
            identity = await history(api, tmp_path)
            result = await asyncio.wait_for(api.request_persona_consolidation(tmp_path, "old"), .5)
            assert result.status == "accepted"
            await asyncio.wait_for(model.started.wait(), 1)
            duplicate = await api.request_persona_consolidation(tmp_path, "old")
            assert duplicate.status == "already_running" and duplicate.request_id == result.request_id
            await asyncio.wait_for(api.create_session(tmp_path, "new", load_runtime=False), .5)
            await api.submit(tmp_path, "old", "继续聊", submission_id="later")
            gate.set()
            await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
            store = PersonaStore(config.icarus_data_dir, "default", PersonaConfig())
            assert store.processed_through(identity) == 3
            assert len(store.read_entries()) == 1 and model.closed
        finally:
            await api.stop()
    asyncio.run(run())


def test_failure_is_retryable_without_progress_and_shutdown_cleans_tasks(tmp_path, monkeypatch):
    assert hasattr(AgentRuntime, "request_persona_consolidation"), "missing application consolidation request"
    async def run():
        model = Model()
        model.error = RuntimeError("PRIVATE")
        wire(monkeypatch, model)
        config = settings(tmp_path)
        api = AgentRuntime(config_loader=lambda: config, session_factory=RuntimeFactory())
        await api.start()
        identity = await history(api, tmp_path)
        await api.request_persona_consolidation(tmp_path, "old")
        await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
        store = PersonaStore(config.icarus_data_dir, "default", PersonaConfig())
        assert store.processed_through(identity) == 0
        model.error = None
        await api.request_persona_consolidation(tmp_path, "old")
        await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
        assert store.processed_through(identity) == 3
        # A later retry of unchanged history must not call the model again.
        await api.request_persona_consolidation(tmp_path, "old")
        await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
        assert len(model.calls) == 2
        await api.stop()
        assert not api.persona_consolidation.tasks
    asyncio.run(run())


def test_disabled_and_busy_do_not_start_a_model_task(tmp_path, monkeypatch):
    assert hasattr(AgentRuntime, "request_persona_consolidation"), "missing application consolidation request"
    async def run():
        model = Model()
        wire(monkeypatch, model)
        config = settings(tmp_path)
        factory = RuntimeFactory()
        api = AgentRuntime(config_loader=lambda: config, session_factory=factory)
        await api.start()
        try:
            await api.create_session(tmp_path, "old")
            factory.created[0].busy = True
            result = await api.request_persona_consolidation(tmp_path, "old")
            assert result.status == "skipped" and result.reason == "busy"
            factory.created[0].busy = False
            config.runtime.plugin_config["persona"]["evolution_enabled"] = False
            result = await api.request_persona_consolidation(tmp_path, "old")
            assert result.reason == "evolution_disabled"
            assert not model.calls and not api.persona_consolidation.tasks
        finally:
            await api.stop()
    asyncio.run(run())


def test_capacity_and_shutdown_cancel_pending_models_without_advancing_progress(tmp_path, monkeypatch):
    async def run():
        model = Model(asyncio.Event())
        wire(monkeypatch, model)
        config = settings(tmp_path)
        api = AgentRuntime(config_loader=lambda: config, session_factory=RuntimeFactory())
        await api.start()
        identities = [await history(api, tmp_path, session) for session in ("one", "two", "three")]
        first = await api.request_persona_consolidation(tmp_path, "one")
        await asyncio.wait_for(model.started.wait(), 1)
        second = await api.request_persona_consolidation(tmp_path, "two")
        await asyncio.sleep(0)
        third = await api.request_persona_consolidation(tmp_path, "three")
        assert first.status == second.status == "accepted"
        assert third.status == "skipped" and third.reason == "capacity"
        await asyncio.wait_for(api.stop(), 2)
        assert model.closed and not api.persona_consolidation.tasks
        store = PersonaStore(config.icarus_data_dir, "default", PersonaConfig())
        assert all(store.processed_through(identity) == 0 for identity in identities)
        assert all(result["reason"] == "cancelled" for result in api.persona_consolidation.results.values())
    asyncio.run(run())


def test_queued_history_is_flushed_before_snapshot_and_continued_history_is_incremental(tmp_path, monkeypatch):
    async def run():
        model = Model()
        wire(monkeypatch, model)
        config = settings(tmp_path)
        api = AgentRuntime(config_loader=lambda: config, session_factory=RuntimeFactory())
        await api.start()
        try:
            identity = await history(api, tmp_path)
            await api.request_persona_consolidation(tmp_path, "old")
            await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
            async def flush_queued_history():
                # Updates arrive after admission but before the snapshot's barrier.
                for kind, payload in [
                    ("user.message", {"text": "以后语气也温和些"}),
                    ("task.finished", {"status": "completed"}),
                ]:
                    await api._handle_update(RuntimeUpdate(workspace_key=identity.workspace_key,
                        session_id="old", task_id="second", type=kind, payload=payload,
                        occurred_at=datetime.now(UTC)))
                await api.updates.flush()
            api.persona_consolidation.flush_updates = flush_queued_history
            await api.request_persona_consolidation(tmp_path, "old")
            await asyncio.gather(*(job[1] for job in api.persona_consolidation.tasks.values()))
            assert len(model.calls) == 2
            second = json.loads(model.calls[1][1].content[0].text)["dialogue"]
            assert [item["text"] for item in second] == ["以后语气也温和些"]
            store = PersonaStore(config.icarus_data_dir, "default", PersonaConfig())
            assert store.processed_through(identity) == 5
            assert len(store.read_entries()) == 1  # Duplicate style does not append again.
        finally:
            await api.stop()
    asyncio.run(run())
