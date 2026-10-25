from datetime import UTC, datetime

from apps.argus.src.surfaces.qq.services.history_service import QQHistoryService
from apps.argus.src.surfaces.qq.services.parser_service import QQParser
from packages.snowluma import HistoryMessage, SnowLumaError


def history(message_id, stamp, user_id, name, role, text, *, group_id='1106632456',
            message_type='group', target_id=None):
    return HistoryMessage(
        message_id=str(message_id),
        sent_at=datetime.fromtimestamp(stamp, UTC),
        sender_id=str(user_id),
        sender_name=name,
        sender_role=role,
        is_self=str(user_id) == '3922275740',
        message=({'type': 'text', 'data': {'text': text}},),
        raw_message=text,
        group_id=group_id if message_type == 'group' else None,
        user_id=str(user_id) if message_type == 'private' else None,
        target_id=target_id,
    )


class FakeClient:
    def __init__(self, messages):
        self.messages = tuple(messages)
        self.calls = []

    async def group_history(self, group_id, *, count=20, **kwargs):
        self.calls.append(('group', str(group_id), count))
        return self.messages

    async def friend_history(self, user_id, *, count=20, **kwargs):
        self.calls.append(('friend', str(user_id), count))
        return self.messages


class FailingClient:
    async def group_history(self, group_id, *, count=20, **kwargs):
        raise SnowLumaError('down')

    async def friend_history(self, user_id, *, count=20, **kwargs):
        raise SnowLumaError('down')


async def test_history_service_converts_self_and_other_messages():
    client = FakeClient([
        history(-111, 1792923448, 3922275740, 'Icarus', 'member', 'hello'),
        history(222, 1792923467, 3635668126, '鱼梦', 'owner', '你好'),
    ])
    service = QQHistoryService(client, QQParser('3922275740', ('icarus',)), limit=15)
    drafts = await service.recent('qq:3922275740:group:1106632456',
                                  before=datetime.fromtimestamp(1792923500, UTC), limit=15)
    assert len(drafts) == 2
    assert drafts[0].is_self is True
    assert drafts[0].sender_name == 'Icarus'
    assert drafts[0].chat_id == 'qq:3922275740:group:1106632456'
    assert drafts[0].sent_at == datetime.fromtimestamp(1792923448, UTC)
    assert drafts[0].observed_at == drafts[0].sent_at
    assert drafts[0].content[0].value == 'hello'
    assert drafts[1].is_self is False
    assert drafts[1].sender_name == '鱼梦'
    assert client.calls[0][0] == 'group'


async def test_history_service_filters_before_and_keeps_last_limit():
    client = FakeClient([
        history(1, 100, 3635668126, '鱼梦', 'owner', 'one'),
        history(2, 200, 3922275740, 'Icarus', 'member', 'two'),
        history(3, 300, 3635668126, '鱼梦', 'owner', 'three'),
    ])
    service = QQHistoryService(client, QQParser('3922275740'), limit=15)
    drafts = await service.recent('qq:3922275740:group:1',
                                  before=datetime.fromtimestamp(250, UTC), limit=15)
    assert [draft.message_id for draft in drafts] == ['1', '2']


async def test_history_service_returns_empty_when_snowluma_fails():
    service = QQHistoryService(FailingClient(), QQParser('3922275740'), limit=15)
    assert await service.recent('qq:3922275740:group:1', limit=15) == ()


async def test_history_service_uses_friend_action_for_private_chat():
    client = FakeClient([
        history(1, 100, 3635668126, '鱼梦', 'owner', 'hi', message_type='private'),
        history(2, 200, 3922275740, 'Icarus', 'member', 'hello', message_type='private',
                target_id='3635668126'),
    ])
    service = QQHistoryService(client, QQParser('3922275740'), limit=15)
    drafts = await service.recent('qq:3922275740:private:3635668126',
                                  before=datetime.fromtimestamp(300, UTC), limit=15)
    assert client.calls[0][0] == 'friend'
    assert client.calls[0][1] == '3635668126'
    assert all(draft.chat_id == 'qq:3922275740:private:3635668126' for draft in drafts)
    assert drafts[1].is_self is True
