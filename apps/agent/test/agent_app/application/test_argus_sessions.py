import asyncio
import importlib
import importlib.util
from datetime import UTC, datetime

import pytest

from agent_app.application.repositories.session_store import SessionStore
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate


class Runtime:
    def __init__(self, store):
        self.session_store = store
        self.configured = []
    def configure_bridge_session(self, session_id, config):
        self.configured.append(session_id)
    async def create_session(self, workspace, session_id, **kwargs):
        await self.session_store.create_session(SessionIdentity.create(workspace, session_id))


def selector(runtime, workspace):
    assert importlib.util.find_spec('agent_app.application.services.argus_sessions'), 'Session selection component missing'
    cls = importlib.import_module('agent_app.application.services.argus_sessions').ArgusSessions
    return cls(runtime, workspace)


def test_session_request_uses_explicit_selection_key():
    from agent_app.argus_protocol import SessionRequest
    value = SessionRequest(channel_id='channel', request_id='request', session_key='choice-a')
    assert value.session_key == 'choice-a'
    with pytest.raises(ValueError):
        SessionRequest(channel_id='channel', request_id='request')


def test_channel_has_multiple_sessions_and_keys_survive_restart(tmp_path):
    async def run():
        store = SessionStore(tmp_path / 'data')
        await store.start()
        try:
            runtime = Runtime(store)
            selected = selector(runtime, tmp_path / 'work')
            a, again = await asyncio.gather(selected.select('channel', 'key-a', '1'), selected.select('channel', 'key-a', '2'))
            assert a == again
            b = await selected.select('channel', 'key-b', '3')
            assert a.session_id != b.session_id
            assert await selected.require('channel', 'key-a', a.session_id) == a
            with pytest.raises(ValueError):
                await selected.require('other-channel', 'key-a', a.session_id)
        finally:
            await store.close()
        reopened = SessionStore(tmp_path / 'data')
        await reopened.start()
        try:
            restored = selector(Runtime(reopened), tmp_path / 'work')
            assert await restored.select('channel', 'key-a', '4') == a
            await reopened.soft_delete_session(a, reason='user-request')
        finally:
            await reopened.close()
        reopened = SessionStore(tmp_path / 'data')
        await reopened.start()
        try:
            with pytest.raises(ValueError, match='deleted'):
                await selector(Runtime(reopened), tmp_path / 'work').select('channel', 'key-a', '5')
        finally:
            await reopened.close()
    asyncio.run(run())


def test_adopts_legacy_session_only_on_first_key(tmp_path):
    async def run():
        store = SessionStore(tmp_path / 'data')
        await store.start()
        try:
            legacy = SessionIdentity.create(tmp_path / 'work', 'argus-legacy')
            await store.create_session(legacy)
            await store.append_update(legacy, RuntimeUpdate(workspace_key=legacy.workspace_key,
                session_id=legacy.session_id, task_id=None, type='argus.session', payload={'channel_id': 'channel'},
                occurred_at=datetime.now(UTC)))
            selected = selector(Runtime(store), legacy.workspace_path)
            assert await selected.select('channel', 'key-a', '1') == legacy
            newer = await selected.select('channel', 'key-b', '2')
            assert newer.session_id != legacy.session_id
            assert await selected.select('channel', 'key-a', '3') == legacy
        finally:
            await store.close()
    asyncio.run(run())


def test_reserved_selection_recovers_creation_gap(tmp_path):
    async def run():
        store = SessionStore(tmp_path / 'data')
        await store.start()
        try:
            runtime = Runtime(store)
            selected = selector(runtime, tmp_path / 'work')
            original = runtime.create_session
            async def interrupt(*args, **kwargs):
                await original(*args, **kwargs)
                raise RuntimeError('crash before ready')
            runtime.create_session = interrupt
            with pytest.raises(RuntimeError):
                await selected.select('channel', 'key', '1')
            runtime.create_session = original
            result = await selector(runtime, tmp_path / 'work').select('channel', 'key', '2')
            assert len(await store.list_session_ids(result.workspace_key)) == 1
        finally:
            await store.close()
    asyncio.run(run())
