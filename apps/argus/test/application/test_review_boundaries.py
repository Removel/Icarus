import asyncio
from apps.argus.test.application.conversation_fakes import make_flow, receive, drain
from apps.argus.src.clients.icarus_client import GatewayTransportError
from apps.argus.test.persistence_fakes import BatchScheduler


def test_final_session_lookup_detects_new_queued_work(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            original = flow.gateway.session_get
            calls = 0
            async def changed(path, sid):
                nonlocal calls
                calls += 1
                if calls == 5:
                    flow.gateway.tasks['other'] = 'accepted'
                return await original(path, sid)
            flow.gateway.session_get = changed
            obs = await receive(flow, text='执行原请求', private=True)
            await drain(flow, settle=True)
            assert flow.gateway.submissions == []
            assert (await flow.store.status_details(obs.id))['status'] == 'waiting'
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_cancellation_during_final_lookup_prevents_old_delivery(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            original = flow.gateway.session_get
            calls = 0
            async def changed(path, sid):
                nonlocal calls
                calls += 1
                if calls == 5:
                    await receive(flow, text='撤销刚才请求，不要执行', private=True)
                return await original(path, sid)
            flow.gateway.session_get = changed
            obs = await receive(flow, text='执行原请求', private=True)
            await drain(flow, settle=True)
            assert flow.gateway.submissions == []
            assert (await flow.store.status_details(obs.id))['status'] == 'waiting'
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_cancellation_after_work_completed_is_not_code_only_yes(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            flow.gateway.set_running('a', request='原任务')
            old = await receive(flow, text='等空闲时写材料', private=True)
            await drain(flow, settle=True)
            flow.gateway.finish('a')
            flow.judge.set_choices(intervene='no')
            cancel = await receive(flow, text='材料不用写了，不要回复', private=True)
            await drain(flow, settle=True)
            assert (await flow.store.status_details(old.id))['status'] == 'finished'
            assert (await flow.store.status_details(cancel.id))['status'] == 'finished'
            assert flow.gateway.submissions == []
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_pre_send_transport_retry_has_deadline_and_does_not_starve_other_scope(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            flow.scheduler = BatchScheduler(flow.store, flow.batches, capacity=1,
                clock=lambda:flow.clock.now.timestamp(), waiting_changed=flow.service.waiting_changed)
            original = flow.gateway.session_get
            count = 0
            async def unavailable_after_judgment(path, sid):
                nonlocal count
                count += 1
                if sid == 's' and count >= 3:
                    raise GatewayTransportError('offline')
                return await original(path, sid)
            flow.gateway.session_get = unavailable_after_judgment
            bad = await receive(flow, text='请求', private=True)
            from apps.argus.src.entity.session_link import SessionLink
            await flow.store.save_link(SessionLink('qq','10000','qq:private:8',str(tmp_path),'key','healthy'))
            await receive(flow, text='健康请求', private=True, sender_id='8')
            flow.clock.advance(6)
            await flow.scheduler.check_due()
            for _ in range(4):
                if flow.scheduler.ready.empty():
                    break
                id = await flow.scheduler.ready.get()
                try:
                    await flow.service.process_batch(id)
                finally:
                    flow.scheduler.done(id)
                await flow.scheduler.replenish()
            bad_row = await flow.batches.record((await flow.store.status_details(bad.id))['batch_id'])
            assert bad_row['status'] == 'decided' and bad_row['next_check_at'] > flow.clock.now
            assert len(flow.gateway.submissions) == 1
            assert flow.gateway.submissions[0]['session_id'] == 'healthy'
            assert flow.scheduler.ready.empty()
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_rejected_receipt_is_already_waiting_if_interrupted_after_commit(tmp_path, monkeypatch):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            flow.gateway.set_running('a', request='原任务')
            flow.gateway.reject_steer = True
            flow.judge.set_choices(relation='steer')
            from apps.argus.src.service import batch_state
            original = batch_state.mark_rejected
            async def interrupted(*args, **kwargs):
                await original(*args, **kwargs)
                raise asyncio.CancelledError
            monkeypatch.setattr(batch_state, 'mark_rejected', interrupted)
            obs = await receive(flow, text='补充请求', private=True)
            try:
                await drain(flow, settle=True)
            except asyncio.CancelledError:
                pass
            await flow.store.close()
            await flow.store.start()
            await flow.batches.reset_interrupted()
            record = await flow.batches.record((await flow.store.status_details(obs.id))['batch_id'])
            assert record['status'] == 'waiting'
            assert record['delivery_attempts'][-1]['outcome'] == 'rejected'
            assert record['next_check_at'] is not None
            assert flow.gateway.submissions == []
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_followup_after_delivery_statistics_expire_still_uses_history(tmp_path):
    async def exercise():
        flow = await make_flow(tmp_path)
        try:
            await receive(flow, text='第一个请求', private=True)
            await drain(flow, settle=True)
            flow.gateway.finish('new-1')
            flow.clock.advance(301)
            flow.judge.set_choices(intervene='no')
            obs = await receive(flow, text='别再生成了，也别回复了', private=True)
            await drain(flow, settle=True)
            assert (await flow.store.status_details(obs.id))['status'] == 'finished'
            assert len(flow.gateway.submissions) == 1
            assert flow.judge.call_count == 1
        finally:
            await flow.store.close()
    asyncio.run(exercise())


def test_invalid_rpc_parameters_are_failed_not_unknown(tmp_path):
    async def exercise():
        from apps.argus.src.clients.icarus_client import GatewayBusinessError
        flow = await make_flow(tmp_path)
        try:
            async def invalid(**params):
                raise GatewayBusinessError('-32602','invalid params')
            flow.gateway.session_submit = invalid
            obs = await receive(flow,text='新请求',private=True)
            await drain(flow, settle=True)
            assert (await flow.store.status_details(obs.id))['status'] == 'failed'
        finally:
            await flow.store.close()
    asyncio.run(exercise())
