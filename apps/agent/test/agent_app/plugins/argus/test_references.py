import asyncio
import importlib.util
from types import SimpleNamespace

import pytest

from agent_app.application.repositories.session_store import SessionStore
from agent_app.application.services.argus_bridge import ArgusBridge
from icarus.core.identity import SessionIdentity


def test_reference_does_not_rebind_across_rounds_or_restart(tmp_path):
    async def run():
        assert importlib.util.find_spec('agent_app.plugins.argus.references'), 'missing durable aliases'
        from agent_app.plugins.argus.references import References
        store = SessionStore(tmp_path/'data'); await store.start()
        bridge = ArgusBridge(SimpleNamespace(session_store=store), workspace_path=tmp_path/'work')
        identity = SessionIdentity.create(tmp_path/'work','argus-test')
        await store.create_session(identity)
        try:
            refs = References(bridge)
            first, sources = await refs.ensure(identity, observation_ids=('a',),source_keys=('source',))
            assert first == {'a':'m1'} and sources == {'source':'s1'}
            second, _ = await References(bridge).ensure(identity, observation_ids=('b','a'),source_keys=('source',))
            assert second == {'a':'m1','b':'m2'}
            records,_ = await store.read_updates(identity)
            assert len([r for r in records if r.type=='argus.references']) == 2
        finally: await store.close()
    asyncio.run(run())


def test_conflicting_reference_log_fails_closed(tmp_path):
    async def run():
        assert importlib.util.find_spec('agent_app.plugins.argus.references'), 'missing durable aliases'
        from agent_app.plugins.argus.references import References
        store = SessionStore(tmp_path/'data'); await store.start()
        bridge = ArgusBridge(SimpleNamespace(session_store=store), workspace_path=tmp_path/'work')
        identity = SessionIdentity.create(tmp_path/'work','argus-test'); await store.create_session(identity)
        try:
            await bridge.append(identity,'argus.references',{'messages':{'a':'m1'},'sources':{}})
            await bridge.append(identity,'argus.references',{'messages':{'b':'m1'},'sources':{}})
            with pytest.raises(ValueError,match='reference_state_corrupt'):
                await References(bridge).ensure(identity,observation_ids=('c',),source_keys=())
        finally: await store.close()
    asyncio.run(run())
