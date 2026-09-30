import asyncio
from typesafe_sdk import SystemOneResponse,Usage,ChoiceAnswer
from apps.argus.src.dao.observation_store import ObservationStore
from apps.argus.src.dao.batch_store import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.src.service.qq_service import QQService


def test_second_batch_can_use_previous_subject_messages(tmp_path):
    async def exercise():
        store=ObservationStore(tmp_path);await store.start();batches=BatchStore(store)
        messages=[]
        for n in (1,2):
            obs=QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,
                message_id=n,message_type='group',group_id=42,user_id=7,message=f'part{n}'))
            await store.insert(obs);messages.append(obs)
        first=await batches.close_batch(('qq','10000',messages[0].subject.key),(messages[0].id,),'quiet')
        await batches.transition(first.id,('ready',),status='finished')
        second=await batches.close_batch(('qq','10000',messages[1].subject.key),(messages[1].id,),'quiet')
        seen=[]
        class Capture:
            async def ask(self,*,state,questions):
                seen.append(state)
                return SystemOneResponse(model='fake',usage=Usage(),answers={'intervene':ChoiceAnswer(choice='no',confidence=1.,probabilities={'no':1.})})
        await QQService(store,Capture(),None,workspace_path=str(tmp_path)).process_batch(second.id)
        assert 'part1' in seen[0]['recent_messages']
        assert 'part2' not in seen[0]['recent_messages']
        await store.close()
    asyncio.run(exercise())
