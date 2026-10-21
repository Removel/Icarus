"""Behavioral contracts that must survive the framework/application split."""

import asyncio
from dataclasses import is_dataclass
from datetime import UTC, datetime
from pathlib import Path

from agent_app import application
from agent_app.plugins.persistence import SessionIdentity
from agent_app.application.agent_runtime import SubmissionConflictError
from agent_app.application.runtime_update_stream import RuntimeUpdateStream
from agent_app.application.repositories.session_store import SessionStore
from icarus.core.runtime_update import RuntimeUpdate


def test_facade_exports_canonical_objects():
    assert application.RuntimeUpdate is RuntimeUpdate
    assert application.SubmissionConflictError is SubmissionConflictError
    assert application.SessionStore is SessionStore


def test_public_history_survives_restart_and_soft_delete(tmp_path):
    async def run():
        identity = SessionIdentity.create(tmp_path / "workspace", "contract")
        store = SessionStore(tmp_path / "data")
        await store.start()
        await store.create_session(identity)
        original = RuntimeUpdate(
            workspace_key=identity.workspace_key,
            session_id=identity.session_id,
            task_id="task",
            type="user.message",
            payload={"text": "visible prompt", "resources": []},
            occurred_at=datetime.now(UTC),
        )
        saved = await store.append_update(identity, original)
        await store.close()
        restored = SessionStore(tmp_path / "data")
        await restored.start()
        records, cursor = await restored.read_updates(identity)
        assert records == (saved,)
        assert cursor == 1
        assert records[0].payload["text"] == "visible prompt"
        assert is_dataclass(records[0])
        assert await restored.soft_delete_session(identity, reason="user_delete") == "discarded"
        assert not await restored.session_exists(identity)
        assert await restored.session_exists(identity, include_deleted=True)
        await restored.close()

    asyncio.run(run())


def test_subscription_preserves_order_and_closes_waiter():
    async def run():
        stream = RuntimeUpdateStream(capacity=2)
        subscription = stream.subscribe()
        update = RuntimeUpdate(
            workspace_key="workspace", session_id="session", task_id=None,
            type="session.lifecycle", payload={"status": "ready"},
            occurred_at=datetime.now(UTC),
        )
        await stream.publish(update)
        assert await subscription.next_update() == update
        subscription.close()
        assert subscription.closed
        stream.close()

    asyncio.run(run())
