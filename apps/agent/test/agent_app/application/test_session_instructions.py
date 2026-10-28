import asyncio
from datetime import UTC, datetime
import inspect

import pytest
from sqlalchemy import select

from agent_app.application.errors import ConversationHistoryCorruptError, SessionAlreadyExistsError
from agent_app.application.repositories.session_entities import _ConversationUpdateRow
from agent_app.application.repositories.session_store import SessionStore
from agent_app.application.runtime_update_stream import RuntimeUpdateStream
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate


PRIVATE = "\nPRIVATE-哨兵😀\n"


def test_instructions_are_private_immutable_and_do_not_consume_public_sequence(tmp_path):
    assert "instructions" in inspect.signature(SessionStore.create_session).parameters

    async def run():
        identity = SessionIdentity.create(tmp_path / "workspace", "session")
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            await store.create_session(identity, instructions=PRIVATE)
            assert await store.get_session_instructions(identity) == PRIVATE
            assert await store.read_updates(identity) == ((), 0)
            session = await store.get_session(identity)
            assert session.last_public_activity_at is None
            assert session.first_user_input is None
            assert await store.list_session_summaries(identity.workspace_key) == ()
            with pytest.raises(SessionAlreadyExistsError):
                await store.create_session(identity, instructions="replacement")
            for value in ("one", "two"):
                result = await store.append_update(identity, RuntimeUpdate(
                    identity.workspace_key, identity.session_id, "task", "user.message",
                    {"text": value}, datetime.now(UTC),
                ))
                assert result.sequence == (1 if value == "one" else 2)
            records, cursor = await store.read_updates(identity)
            assert cursor == 2
            assert [row.payload["text"] for row in records] == ["one", "two"]
            async with store._require_started()() as db:
                rows = tuple(await db.scalars(select(_ConversationUpdateRow)))
                assert [row.sequence for row in rows] == [0, 1, 2]
                assert rows[0].update_type == "session.instructions"
        finally:
            await store.close()
        restored = SessionStore(tmp_path / "data")
        await restored.start()
        try:
            assert await restored.get_session_instructions(identity) == PRIVATE
        finally:
            await restored.close()
    asyncio.run(run())


@pytest.mark.parametrize("bad", [{"content": 1}, {"secret": PRIVATE}])
def test_corrupt_private_instructions_fail_closed(tmp_path, bad):
    assert hasattr(SessionStore, "get_session_instructions")

    async def run():
        identity = SessionIdentity.create(tmp_path, "broken")
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            await store.create_session(identity, instructions=PRIVATE)
            async with store._require_started().begin() as db:
                row = await db.get(_ConversationUpdateRow, (identity.workspace_key, identity.session_id, 0))
                row.payload = bad
            with pytest.raises(ConversationHistoryCorruptError):
                await store.get_session_instructions(identity)
        finally:
            await store.close()
    asyncio.run(run())


def test_lazy_create_validates_without_loading_and_survives_restart(tmp_path):
    from agent_app.application import AgentRuntime
    from agent_app.contracts.prompt import PromptError
    from apps.agent.test.agent_app.application.test_agent_runtime import make_config

    assert "instructions" in inspect.signature(AgentRuntime.create_session).parameters

    async def run():
        config = make_config(tmp_path / "data")
        created = []

        def forbidden(*args, **kwargs):
            created.append(kwargs)
            raise AssertionError("Lazy create must not construct a runtime")

        runtime = AgentRuntime(config_loader=lambda: config, session_factory=forbidden)
        await runtime.start()
        identity = SessionIdentity.create(tmp_path, "session")
        try:
            with pytest.raises(PromptError):
                await runtime.create_session(tmp_path, "oversize", instructions="x" * 8193, load_runtime=False)
            assert not await runtime._store().session_exists(SessionIdentity.create(tmp_path, "oversize"))
            await runtime.create_session(tmp_path, "session", instructions=PRIVATE, load_runtime=False)
            assert created == []
            assert await runtime._store().get_session_instructions(identity) == PRIVATE
            assert await runtime.get_session_history(tmp_path, "session") == ((), 0)
        finally:
            await runtime.stop()
        received = []

        def capture(*args, **kwargs):
            received.append(kwargs["instructions"])
            raise RuntimeError("stop after restore capture")

        restored = AgentRuntime(config_loader=lambda: config, session_factory=capture)
        await restored.start()
        try:
            with pytest.raises(RuntimeError, match="restore capture"):
                await restored.submit(tmp_path, "session", "hello", submission_id="submit")
            assert received == [PRIVATE]
        finally:
            await restored.stop()
    asyncio.run(run())


def test_instructions_insert_failure_rolls_back_session(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session
    original = Session.flush
    def fail_private(self, *args, **kwargs):
        if any(isinstance(row, _ConversationUpdateRow) and row.sequence == 0 for row in self.new):
            raise RuntimeError("private storage failed")
        return original(self, *args, **kwargs)

    async def run():
        identity = SessionIdentity.create(tmp_path, "atomic")
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            monkeypatch.setattr(Session, "flush", fail_private)
            with pytest.raises(RuntimeError, match="private storage failed"):
                await store.create_session(identity, instructions=PRIVATE)
            assert not await store.session_exists(identity)
        finally:
            await store.close()
    asyncio.run(run())


def test_private_only_session_can_be_discarded(tmp_path):
    async def run():
        identity = SessionIdentity.create(tmp_path, "empty")
        store = SessionStore(tmp_path / "data")
        await store.start()
        try:
            await store.create_session(identity, instructions=PRIVATE)
            assert await store.soft_delete_empty_session(identity, reason="empty") == "discarded"
        finally:
            await store.close()
    asyncio.run(run())


def test_private_markers_cannot_enter_live_stream():
    async def run():
        stream = RuntimeUpdateStream()
        private = RuntimeUpdate("wk", "session", None, "session.instructions", {"content": PRIVATE}, datetime.now(UTC))
        with pytest.raises(ValueError):
            await stream.publish(private)
        stream.close()
    asyncio.run(run())
