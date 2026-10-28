from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError


def test_name_alias_is_not_an_exclusive_identity():
    from utils.normalize import normalize_alias

    assert normalize_alias("name", " 小张 ") == ("小张", None)
    assert normalize_alias("qq", " QQ 123456 ") == ("123456", "qq:123456")
    assert normalize_alias("wechat", " WxAbC ") == ("WxAbC", "wechat:WxAbC")
    with pytest.raises(ValueError):
        normalize_alias("qq", "abc")


def test_relation_normalization_preserves_a_is_bs_semantics():
    from schemas.relation import RelationWrite

    relation = RelationWrite(from_person_id="z", to_person_id="a", kind="亲属|任意",
                             direction="two_way", forward_detail="表哥", reverse_detail="表弟", source="user")
    result = relation.canonical()
    assert result["from_person_id"] == "a"
    assert result["to_person_id"] == "z"
    assert result["forward_detail"] == "表弟"
    assert result["reverse_detail"] == "表哥"
    reverse = RelationWrite(from_person_id="a", to_person_id="z", kind="亲属|任意",
                            direction="two_way", forward_detail="表弟", reverse_detail="表哥", source="user")
    assert result["relation_key"] == reverse.canonical()["relation_key"]


@pytest.mark.parametrize("direction,reverse", [("two_way", None), ("one_way", "下属")])
def test_relation_requires_consistent_direction(direction, reverse):
    from schemas.relation import RelationWrite

    with pytest.raises(ValidationError):
        RelationWrite(from_person_id="a", to_person_id="b", kind="work", direction=direction,
                      forward_detail="上司", reverse_detail=reverse, source="agent")


def test_profile_allows_json_null_but_not_future_or_naive_time():
    from schemas.profile import ProfileWrite

    assert ProfileWrite(value=None, source="user").value is None
    for time in (datetime.now(), datetime.now(UTC) + timedelta(days=1)):
        with pytest.raises(ValidationError):
            ProfileWrite(value="wrong time", source="user", recorded_at=time)
    with pytest.raises(ValidationError):
        ProfileWrite(value="a" * 65536, source="agent")
    with pytest.raises(ValidationError):
        ProfileWrite(value=float("nan"), source="agent")


def test_cursor_binds_filter_and_rejects_tampering():
    from utils.pagination import decode_cursor, encode_cursor
    from errors import ServiceError

    cursor = encode_cursor("p_2", {"query": "张"})
    assert decode_cursor(cursor, {"query": "张"}) == "p_2"
    with pytest.raises(ServiceError):
        decode_cursor(cursor, {"query": "李"})
    with pytest.raises(ServiceError):
        decode_cursor("not-a-cursor", {})
