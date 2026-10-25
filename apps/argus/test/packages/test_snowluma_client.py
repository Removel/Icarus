import json
from datetime import UTC, datetime

import httpx
import pytest

from packages.snowluma import HistoryMessage, SnowLumaClient, SnowLumaError


def onebot_message(message_id, stamp, sender_id, name, role, text, **extra):
    return {
        'message_id': message_id,
        'time': stamp,
        'sender': {'user_id': sender_id, 'nickname': name, 'card': name, 'role': role},
        'message': [{'type': 'text', 'data': {'text': text}}],
        'raw_message': text,
        'message_type': 'group',
        **extra,
    }


async def test_group_history_posts_onebot_action_and_parses_messages():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={'status': 'ok', 'retcode': 0, 'data': {'messages': [
            onebot_message(-111, 1792923448, 3922275740, 'Icarus', 'member', 'hello', group_id=1106632456),
            onebot_message(222, 1792923467, 3635668126, '鱼梦', 'owner', '你好', group_id=1106632456),
        ]}})

    client = SnowLumaClient('http://snowluma.test/', 'secret-token', self_id='3922275740',
                            transport=httpx.MockTransport(handler))
    try:
        messages = await client.group_history('1106632456', count=2)
    finally:
        await client.aclose()

    assert len(calls) == 1
    request = calls[0]
    assert request.method == 'POST'
    assert str(request.url) == 'http://snowluma.test/'
    assert request.headers['authorization'] == 'Bearer secret-token'
    assert json.loads(request.content) == {
        'action': 'get_group_msg_history',
        'params': {'group_id': 1106632456, 'count': 2},
    }
    assert len(messages) == 2
    assert all(isinstance(value, HistoryMessage) for value in messages)
    assert messages[0].message_id == '-111'
    assert messages[0].is_self is True
    assert messages[0].sender_name == 'Icarus'
    assert messages[0].sender_role == 'member'
    assert messages[0].sent_at == datetime.fromtimestamp(1792923448, UTC)
    assert messages[0].message == ({'type': 'text', 'data': {'text': 'hello'}},)
    assert messages[1].is_self is False
    assert messages[1].sender_name == '鱼梦'
    assert messages[1].sender_role == 'owner'


async def test_friend_history_uses_friend_action():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'ok', 'retcode': 0, 'data': {'messages': [
            onebot_message(1, 1792923000, 3635668126, '鱼梦', 'owner', 'hi',
                           message_type='private', user_id=3635668126),
        ]}})

    client = SnowLumaClient('http://snowluma.test/', 'token', self_id='3922275740',
                            transport=httpx.MockTransport(handler))
    try:
        messages = await client.friend_history('3635668126', count=1)
    finally:
        await client.aclose()

    assert calls[0]['action'] == 'get_friend_msg_history'
    assert calls[0]['params'] == {'user_id': 3635668126, 'count': 1}
    assert messages[0].is_self is False
    assert messages[0].user_id == '3635668126'


async def test_count_is_bounded_and_optional_anchor_is_forwarded():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={'status': 'ok', 'retcode': 0, 'data': {'messages': []}})

    client = SnowLumaClient('http://snowluma.test/', 'token', self_id='1', count_limit=10,
                            transport=httpx.MockTransport(handler))
    try:
        await client.group_history('1', count=0)
        await client.group_history('1', count=999, message_id=-5, reverse_order=True)
    finally:
        await client.aclose()

    assert calls[0]['params']['count'] == 1
    assert calls[1]['params']['count'] == 10
    assert calls[1]['params']['message_id'] == -5
    assert calls[1]['params']['reverse_order'] is True


async def test_failed_envelope_raises_without_leaking_token():
    token = 'super-secret-token'

    def handler(request):
        return httpx.Response(200, json={'status': 'failed', 'retcode': 1401, 'wording': 'unauthorized'})

    client = SnowLumaClient('http://snowluma.test/', token, self_id='1',
                            transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SnowLumaError) as exc:
            await client.group_history('1', count=1)
    finally:
        await client.aclose()
    assert exc.value.retcode == 1401
    assert exc.value.wording == 'unauthorized'
    assert token not in str(exc.value)


async def test_http_error_raises_with_status():
    def handler(request):
        return httpx.Response(503, text='unavailable')

    client = SnowLumaClient('http://snowluma.test/', 'token', self_id='1',
                            transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(SnowLumaError) as exc:
            await client.friend_history('1', count=1)
    finally:
        await client.aclose()
    assert exc.value.status == 503
