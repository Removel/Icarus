import asyncio

import pytest

from agent_app.application.services.argus_bridge import BridgeConflict
from .test_argus_admission import setup_bridge


def test_notify_only_signals_the_existing_live_binding(tmp_path):
    async def run():
        store,runtime,bridge,request=await setup_bridge(tmp_path)
        try:
            assert hasattr(bridge,'notify'),'protected running-task signal is missing'
            result=await bridge.wake(request);calls=[]
            bridge.listeners[result['task_id']]=lambda task,watermark:calls.append((task,watermark)) or True
            assert (await bridge.notify(request.wake_id,request.session_id,request.binding_id,'watermark'))['notified']
            assert calls==[(result['task_id'],'watermark')]
            assert runtime.calls==1
            with pytest.raises(BridgeConflict):await bridge.notify(request.wake_id,'wrong',request.binding_id,'two')
            bridge._live_tasks.clear()
            assert not (await bridge.notify(request.wake_id,request.session_id,request.binding_id,'two'))['notified']
            assert runtime.calls==1
        finally:await store.close()
    asyncio.run(run())
