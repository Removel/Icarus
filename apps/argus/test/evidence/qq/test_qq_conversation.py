import asyncio
from apps.argus.test.persistence_fakes import ObservationStore
from apps.argus.test.persistence_fakes import BatchStore
from apps.argus.src.handler.qq_handler import QQHandler
from apps.argus.test.persistence_fakes import QQConversationReader


def test_following_messages_are_background_not_members(tmp_path):
    async def exercise():
        store = ObservationStore(tmp_path)
        await store.start()
        try:
            messages = []
            for n in (1,2,3):
                obs = QQHandler('10000').parse(dict(post_type='message',self_id=10000,time=1,message_id=n,message_type='group',group_id=42,user_id=7,message=f'part{n}'))
                await store.insert(obs)
                messages.append(obs)
            batch = await BatchStore(store).close_batch(('qq','10000','qq:group:42'),(messages[0].id,),'quiet')
            evidence = await QQConversationReader(store).evaluate(batch=batch)
            assert evidence.current == (messages[0],)
            assert evidence.following == tuple(messages[1:])
            assert evidence.through_seq == evidence.human_through_seq == 3
            assert (await BatchStore(store).get(batch.id)).messages == (messages[0],)
        finally:
            await store.close()
    asyncio.run(exercise())
