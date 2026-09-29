from datetime import UTC, datetime

import pytest

from apps.argus.src.handler.QQHandler import QQHandler
from apps.argus.src.entity.QQObservation import QQObservation


def test_group_message_preserves_order_and_mention():
    observation = QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 301,
        "message_type": "group", "group_id": 42, "user_id": 7,
        "sender": {"nickname": "小明"},
        "message": [
            {"type": "at", "data": {"qq": "10000"}},
            {"type": "text", "data": {"text": "你好"}},
            {"type": "image", "data": {"file": "a.png"}},
        ],
    })
    assert isinstance(observation, QQObservation)
    assert observation.source.source_message_id == "301"
    assert observation.subject.key == "qq:group:42"
    assert [(part.kind, part.value) for part in observation.content] == [
        ("text", "你好"), ("image", "a.png")
    ]
    assert observation.mentions_bot and not observation.is_self
    assert observation.source.occurred_at == datetime.fromtimestamp(1700000000, UTC)


def test_signed_onebot_message_id_is_preserved():
    observation = QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": 1700000000,
        "message_id": -2147483647, "message_type": "private", "user_id": 7,
        "message": "你好",
    })
    assert observation.source.source_message_id == "-2147483647"


def test_unsupported_segments_are_kept_as_explicit_parts():
    observation = QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": 1700000000,
        "message_id": 306, "message_type": "group", "group_id": 42, "user_id": 7,
        "message": [
            {"type": "at", "data": {"qq": "10000"}},
            {"type": "text", "data": {"text": "看图"}},
            {"type": "face", "data": {"id": "1"}},
            {"type": "reply", "data": {"id": "99"}},
        ],
    })
    assert [(part.kind, part.value) for part in observation.content] == [
        ("text", "看图"), ("other", "face"), ("other", "reply")
    ]
    assert observation.mentions_bot


def test_face_only_message_is_accepted():
    observation = QQHandler("10000").parse({
        "post_type": "message", "self_id": 10000, "time": 1700000000,
        "message_id": 307, "message_type": "private", "user_id": 7,
        "message": [{"type": "face", "data": {"id": "1"}}],
    })
    assert [part.kind for part in observation.content] == ["other"]


def test_self_private_message_uses_target():
    observation = QQHandler("10000").parse({
        "post_type": "message_sent", "self_id": "10000", "time": 1700000000, "message_id": 302,
        "message_type": "private", "user_id": 10000, "target_id": 7,
        "message": "已回复",
    })
    assert observation.subject.key == "qq:private:7"
    assert observation.is_self


def test_temporary_private_message_is_private():
    observation = QQHandler("10000").parse({
        "post_type": "message", "time": 1700000000, "message_id": 303, "self_id": 10000,
        "message_type": "private", "sub_type": "group", "user_id": 7,
        "sender": {"group_id": 42}, "message": "你好",
    })
    assert observation.subject.key == "qq:private:7"
    assert observation.message_type == "private"


@pytest.mark.parametrize("change", [
    {"message_id": None}, {"message_id": 0}, {"message_id": True},
    {"self_id": 20000}, {"group_id": None},
    {"post_type": "notice"}, {"time": float("nan")},
    {"message": "[CQ:at,qq=10000]你好"},
    {"message": [{"type": "text", "data": []}]},
    {"message": [{"data": {}}]},
])
def test_invalid_events_are_rejected(change):
    raw = {
        "post_type": "message", "self_id": 10000, "time": 1700000000, "message_id": 304,
        "message_type": "group", "group_id": 42, "user_id": 7,
        "message": "你好",
    }
    raw.update(change)
    with pytest.raises(ValueError):
        QQHandler("10000").parse(raw)


def test_all_mention_and_plain_text_do_not_count_as_bot_mention():
    handler = QQHandler("10000")
    base = {
        "post_type": "message", "time": 1700000000, "message_id": 305, "self_id": 10000, "message_type": "group",
        "group_id": 42, "user_id": 7,
    }
    for message in ([{"type": "at", "data": {"qq": "all"}}], "@Icarus 你好"):
        assert not handler.parse({**base, "message": message}).mentions_bot
