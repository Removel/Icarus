import asyncio
from datetime import timedelta
from fastapi import FastAPI
from fastapi.testclient import TestClient
from apps.argus.test.application.conversation_fakes import make_flow,receive,drain


def test_waiting_status_and_run_pagination_are_safe(tmp_path):
    async def exercise():
        flow=await make_flow(tmp_path)
        try:
            flow.gateway.set_running('a',request='DO_NOT_EXPOSE_SECRET')
            obs=await receive(flow,text='待处理问题',private=True)
            await drain(flow, settle=True)
            flow.gateway.finish('a')
            flow.judge.set_choices(intervene='no')
            flow.clock.advance(5)
            await flow.scheduler.check_due()
            await drain(flow, settle=True)
            response=await flow.store.status_details(obs.id,after_run=0,limit=1)
            assert response['status']=='finished'
            assert response['has_more'] and response['next_after_run']==1
            assert response['runs'][0]['status']=='completed'
            assert 'DO_NOT_EXPOSE_SECRET' not in str(response)
            assert 'params' not in response
            second=await flow.store.status_details(obs.id,after_run=1,limit=1)
            assert not second['has_more'] and len(second['runs'])==1
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_http_run_pagination_rejects_invalid_bounds(tmp_path):
    from apps.argus.src.main import create_app
    from apps.argus.src.config.config_model import ArgusConfig
    from apps.argus.test.persistence_fakes import ObservationStore
    config=ArgusConfig(host='127.0.0.1',port=8766,log_level='INFO',queue_capacity=5,worker_count=1)
    with TestClient(create_app(config,database=ObservationStore(tmp_path).database)) as client:
        assert client.get('/observations/missing/status?limit=0').status_code==422
        assert client.get('/observations/missing/status?after_run=-1').status_code==422
        assert client.get('/observations/missing/status?limit=101').status_code==422
        assert client.get('/observations/missing/status').status_code==404
