import asyncio
from datetime import UTC,datetime
from typesafe_sdk import ChoiceAnswer,SystemOneResponse,Usage
from apps.argus.test.persistence_fakes import ObservationStore,SessionLink
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQService
from apps.argus.src.entity.decision import ObservationBatch
from apps.argus.src.clients.jev_client import JevCallError


class Capture:
    def __init__(self):self.states=[]
    async def ask(self,*,state,questions):
        self.states.append(state)
        return SystemOneResponse(model='fake',usage=Usage(),answers={k:ChoiceAnswer(choice='no',confidence=1.,probabilities={'no':1.}) for k in questions})


def message(n,text):
    return QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,
        message_type='group',group_id=42,user_id=7,message=text))


def test_bot_names_and_ordered_transcript_survive_group_batch(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();judge=Capture()
        messages=(message(1,'上一句'),message(2,'ＩＣＡＲＵＳ 在吗'))
        for m in messages:await store.insert(m)
        batch=await BatchStore(store).close_batch(('qq','10000','qq:group:42'),tuple(m.id for m in messages),'quiet')
        service=QQService(store,judge,None,workspace_path=str(tmp_path),bot_names=('icarus','伊卡洛斯'))
        await service.process_batch(batch.id)
        state=judge.states[0]
        assert state['content'].index('上一句')<state['content'].index('ＩＣＡＲＵＳ')
        assert state['name_mentioned'] and state['bot_names']==['icarus','伊卡洛斯']
        assert (await BatchStore(store).record(batch.id))['status']=='finished'
        await store.close()
    asyncio.run(exercise())


def test_fault_is_not_normal_silence_and_does_not_dispatch(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start()
        class Fail:
            async def ask(self,**_):raise JevCallError('offline')
        obs=message(3,'请问');await store.insert(obs)
        batches=BatchStore(store)
        batch=await batches.close_batch(('qq','10000',obs.subject.key),(obs.id,),'quiet')
        await QQService(store,Fail(),None,workspace_path=str(tmp_path)).process_batch(batch.id)
        record=await batches.record(batch.id)
        assert record['status']=='failed' and record['route']['action']=='silence'
        assert any(t['error_type']=='JevCallError' for t in record['trace'][0]['nodes'])
        await store.close()
    asyncio.run(exercise())


def test_image_name_is_not_text_name_mention():
    from apps.argus.src.evidence.qq.qq_facts import QQBatchFactsReader
    obs=message(4,[{'type':'image','data':{'file':'icarus.png'}}])
    batch=ObservationBatch('b','qq','10000',obs.subject.key,(obs,),obs.received_at,datetime.now(UTC))
    facts=asyncio.run(QQBatchFactsReader(('icarus',)).evaluate(batch))
    assert facts.state['name_mentioned'] is False
