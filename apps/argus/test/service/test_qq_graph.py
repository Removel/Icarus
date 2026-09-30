import asyncio
from datetime import UTC,datetime
from typesafe_sdk import SystemOneResponse,Usage,ChoiceAnswer
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.service.qq_service import QQService
from apps.argus.src.entity.decision import ObservationBatch
from apps.argus.src.handler.qq_handler import QQHandler


class ReaderGateway:
    async def session_get(self,path,sid):
        return dict(workspace_key='key',session_id=sid,lifecycle='ready',active_task_ids=[])


class Jev:
    def __init__(self):self.calls=[]
    async def ask(self,*,state,questions):
        self.calls.append(state)
        return SystemOneResponse(model='fake',usage=Usage(),answers={k:ChoiceAnswer(choice='yes',confidence=1.,probabilities={'yes':1.}) for k in questions})


def test_private_and_self_are_code_only_group_uncertainty_calls_jev(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();jev=Jev()
        service=QQService(store,jev,ReaderGateway(),workspace_path=str(tmp_path),bot_names=('icarus',))
        for n,kind,sender,expected in [(1,'private',7,'submit'),(2,'private',10000,'silence'),(3,'group',7,'submit')]:
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,
                message_type=kind,group_id=42,user_id=sender,target_id=7,message='hello'))
            batch=ObservationBatch(str(n),'qq','10000',obs.subject.key,(obs,),obs.received_at,datetime.now(UTC))
            result=await service.evaluate_batch(batch)
            assert result.status=='completed' and result.decision.action==expected
        assert len(jev.calls)==1
        await store.close()
    asyncio.run(exercise())
