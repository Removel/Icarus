import asyncio
import pytest
from apps.argus.src.handler.qq_handler import QQHandler


def test_handler_delegates_parsed_business_object():
    async def exercise():
        received = []
        async def receive(observation):
            received.append(observation)
        handler = QQHandler('10000',receive=receive)
        observation = await handler.handle(dict(post_type='message',self_id=10000,time=1,
            message_id=3,message_type='private',user_id=7,message='hello'))
        assert received == [observation]
        assert observation.source.source_message_id == '3' and observation.content[0].value == 'hello'
        assert await handler.handle({'self_id':10000,'post_type':'meta_event'}) is None
        assert received == [observation]
        with pytest.raises(ValueError):
            await handler.handle({'self_id':10000,'post_type':'message'})
        assert received == [observation]
    asyncio.run(exercise())
