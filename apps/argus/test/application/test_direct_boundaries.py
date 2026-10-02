import asyncio
import json
from dataclasses import replace
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.entity.decision import ObservationBatch
from apps.argus.test.application.conversation_fakes import make_flow
from apps.argus.src.handler.qq_handler import QQHandler


def test_self_only_skips_evidence_and_remote_queries(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        calls = []
        async def unexpected(*args, **kwargs):
            calls.append(True)
            raise AssertionError('self-only must not collect')
        flow.service.decision_reader.evaluate = unexpected
        flow.service.graph = GraphCompiler().compile(flow.service._build_graph())
        obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
            message_id=1,message_type='group',group_id=42,user_id=10000,message='self'))
        batch = ObservationBatch('synthetic','qq','10000',obs.subject.key,(obs,),obs.received_at,obs.received_at)
        try:
            run = await flow.service.evaluate_batch(batch)
            assert run.output.action == 'silence'
            assert run.executions['decision_evidence'].status == 'skipped'
            assert calls == [] and flow.judge.call_count == 0
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_notification_failure_keeps_committed_observation(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        def broken(*args, **kwargs):
            raise RuntimeError('notification failure')
        flow.service.notify = broken
        obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
            message_id=1,message_type='private',user_id=7,message='receive'))
        obs = replace(obs,received_at=flow.clock.now)
        try:
            assert await flow.service.receive(obs)
            assert (await flow.service.store.get(obs.id)).source_message_id == '1'
            flow.clock.advance(6)
            await flow.scheduler.check_due()
            assert not flow.scheduler.ready.empty()
        finally:
            await flow.store.close()
    asyncio.run(exercise())
