from datetime import UTC, datetime

import pytest


def event(**changes):
    return {'self_id': 123, 'post_type': 'message', 'message_type': 'private', 'message_id': -789,
            'user_id': 456, 'sender': {'nickname': 'peer'}, 'time': 1700000000,
            'message': '帮我整理笔记', **changes}


def test_parser_keeps_reply_and_readable_text_without_media_placeholders():
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    result = QQParser('123').parse(event(message=[
        {'type': 'reply', 'data': {'id': '-12'}}, {'type': 'at', 'data': {'qq': '123'}},
        {'type': 'text', 'data': {'text': ' help '}}, {'type': 'image', 'data': {'file': 'ref'}}]))
    assert result.detail['reply_to'] == '-12'
    assert result.detail['readable_text'] == 'help'
    assert result.content[-1].value == 'ref'


def test_private_is_strong_without_name():
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    result = QQParser('123').parse(event())
    assert result.chat_id == 'qq:123:private:456' and result.strong_signal
    assert result.message_id == '-789'


def test_private_self_uses_target_and_never_strong():
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    result = QQParser('123').parse(event(post_type='message_sent', user_id=123, target_id=456))
    assert result.chat_id == 'qq:123:private:456' and result.is_self and not result.strong_signal


def test_group_name_normalization_and_segments():
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    result = QQParser('123', ['Icarus']).parse(event(message_type='group', group_id=999,
        message=[{'type': 'text', 'data': {'text': 'ＩＣＡＲＵＳ'}},
                 {'type': 'image', 'data': {'file': 'image-ref'}}, {'type': 'face', 'data': {}}]))
    assert result.strong_signal
    assert [part.kind for part in result.content] == ['text', 'image', 'other']


def test_recall_without_original_is_tombstone():
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    result = QQParser('123').parse(event(post_type='notice', notice_type='group_recall', group_id=999))
    assert result.deleted and result.content == () and result.chat_id == 'qq:123:group:999'


@pytest.mark.parametrize('changes', [{'time': True}, {'time': float('nan')}, {'message_id': 0},
                                   {'self_id': 321}, {'message': '[CQ:at,qq=123]'}])
def test_invalid_event_rejected(changes):
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    with pytest.raises(ValueError):
        QQParser('123').parse(event(**changes))


async def test_mention_only_edit_creates_new_revision(database):
    from uuid import uuid4
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    from apps.argus.src.common.repositories.observation_repository import ObservationRepository
    parser = QQParser('123')
    repo = ObservationRepository(database, boot_id=uuid4())
    plain = event(message_type='group', group_id=999, message=[{'type': 'text', 'data': {'text': 'help'}}])
    first, _ = await repo.upsert_revision(parser.parse(plain))
    mentioned = {**plain, 'message': plain['message'] + [{'type': 'at', 'data': {'qq': '123'}}]}
    changed, inserted = await repo.upsert_revision(parser.parse(mentioned))
    assert inserted and changed.revision == 2 and changed.strong_signal
    assert first.strong_signal is False
    assert any(part.value == '@123' for part in changed.content)


def test_meta_ignored_after_account_validation():
    from apps.argus.src.surfaces.qq.services.parser_service import QQParser
    assert QQParser('123').parse(event(post_type='meta_event')) is None
