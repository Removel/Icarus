"""A generic semantic subject reaches recall without leaking transport tutorials."""
import asyncio
import hashlib
import json

import pytest

from icarus.core.contracts.input import UserInputEvent
from agent_app.application.services.common import _submission_fingerprint
from apps.agent.test.agent_app.plugins.memory.test_plugin import BackendStub, plugin, bind_background
from apps.agent.test.agent_app.plugins.user_input.test_plugin import SessionStub
from agent_app.plugins.user_input.plugin import UserInputPlugin


def test_semantic_text_drives_memory_query_and_result():
    assert 'semantic_text' in UserInputEvent.__dataclass_fields__, 'missing semantic input'
    async def run():
        backend=BackendStub();target=plugin(backend);published=[];bind_background(target,published)
        event=UserInputEvent(task_id='task',prompt='technical envelope',semantic_text='picture viewer requirements')
        await target.consume('user-input',event);await target.drain()
        await asyncio.gather(*target._recall_tasks)
        assert backend.calls[0][1]=='picture viewer requirements'
        assert 'technical' not in str(backend.calls)
        assert target._automatic_results['task'][1].query=='picture viewer requirements'
    asyncio.run(run())


def test_semantic_text_default_keeps_old_submission_fingerprint():
    expected=hashlib.sha256(json.dumps({'prompt':'hello','display_text':None,'resources':[]},
        ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert _submission_fingerprint('hello',(),None)==expected
    import inspect
    assert 'semantic_text' in inspect.signature(_submission_fingerprint).parameters
    assert _submission_fingerprint('hello',(),None,semantic_text=None)==expected
    assert _submission_fingerprint('hello',(),None,semantic_text='one') != _submission_fingerprint('hello',(),None,semantic_text='two')


def test_semantic_text_validation_and_user_input_port():
    assert 'semantic_text' in UserInputEvent.__dataclass_fields__
    for bad in ('', 'x'*8193, 123):
        with pytest.raises((ValueError,TypeError)):
            UserInputEvent(prompt='hello',semantic_text=bad)
    async def run():
        target=UserInputPlugin('input',SessionStub());seen=[];received=asyncio.Event()
        async def publish(event):
            seen.append(event)
            if isinstance(event,UserInputEvent):received.set()
        target.bind_publisher(publish)
        await target.start()
        try:
            await target.submit('full evidence',semantic_text='subject')
            await asyncio.wait_for(received.wait(),2)
            event=next(e for e in seen if isinstance(e,UserInputEvent))
            assert event.semantic_text=='subject' and event.prompt=='full evidence'
        finally:await target.stop()
    asyncio.run(run())
