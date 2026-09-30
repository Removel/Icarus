"""Convert SnowLuma OneBot messages into QQ observations."""

import json
from datetime import UTC, datetime
from math import isfinite
from typing import Any
from uuid import uuid4

from apps.argus.src.entity.qq_observation import QQObservation
from apps.argus.src.entity.observation import ActorRef, ContentPart, SourceRef, SubjectRef


def _positive_id(value: Any, field: str) -> str:
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return str(int(value))
    raise ValueError(f"{field} must be a positive identifier")


def _message_id(value: Any) -> str:
    if type(value) is int and value != 0:
        return str(value)
    if isinstance(value, str) and value.isascii():
        try:
            parsed = int(value)
        except ValueError:
            pass
        else:
            if parsed != 0 and value.lstrip("-").isdecimal():
                return str(parsed)
    raise ValueError("message_id must be a nonzero signed integer")


class QQHandler:
    def __init__(self, bot_id: str) -> None:
        self.bot_id = _positive_id(bot_id, "bot_id")

    @staticmethod
    def reply_hint(subject: str) -> str:
        platform, kind, target = subject.split(":", 2)
        if platform != "qq" or kind not in {"group", "private"}:
            raise ValueError("Unsupported QQ reply target")
        action = "send_group_msg" if kind == "group" else "send_private_msg"
        key = "group_id" if kind == "group" else "user_id"
        example = json.dumps({
            "action": action,
            "params": {key: int(target), "message": [{"type": "text", "data": {"text": "回复内容"}}]},
        }, ensure_ascii=False)
        return f"\n请用 snowluma 的 invoke_action 将回复发到 QQ：{example}；只在会话里回复，对方收不到。"

    def parse(self, raw: dict[str, Any]) -> QQObservation:
        if not isinstance(raw, dict):
            raise ValueError("OneBot event must be an object")
        if _positive_id(raw.get("self_id"), "self_id") != self.bot_id:
            raise ValueError("self_id does not match the configured bot")
        if raw.get("post_type") not in {"message", "message_sent"}:
            raise ValueError("Only OneBot chat messages are supported")
        message_id = _message_id(raw.get("message_id"))
        kind = raw.get("message_type")
        if kind not in {"group", "private"}:
            raise ValueError("message_type must be group or private")
        sender = raw.get("sender", {})
        if not isinstance(sender, dict):
            raise ValueError("sender must be an object")
        sender_id = _positive_id(raw.get("user_id", sender.get("user_id")), "user_id")
        is_self = raw["post_type"] == "message_sent" or sender_id == self.bot_id
        group_id = _positive_id(raw.get("group_id"), "group_id") if kind == "group" else None
        peer_id = (
            _positive_id(raw.get("target_id"), "target_id")
            if is_self and kind == "private" else sender_id
        )
        subject = SubjectRef(
            platform="qq", kind=kind,
            external_id=group_id if group_id is not None else peer_id,
        )
        parts: list[ContentPart] = []
        mentions_bot = False
        message = raw.get("message")
        if isinstance(message, str):
            if "[CQ:" in message:
                raise ValueError("CQ string messages are unsupported; use messageFormat=array")
            parts.append(ContentPart(kind="text", value=message))
        elif isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                    raise ValueError("message segments require an object data field")
                data = segment["data"]
                segment_type = segment.get("type")
                if segment_type == "text":
                    if not isinstance(data.get("text"), str):
                        raise ValueError("text segment must contain text")
                    parts.append(ContentPart(kind="text", value=data["text"]))
                elif segment_type == "image":
                    reference = data.get("file")
                    if not isinstance(reference, str) or not reference:
                        raise ValueError("image segment must contain a file reference")
                    parts.append(ContentPart(kind="image", value=reference))
                elif segment_type == "at":
                    if str(data.get("qq")) == self.bot_id:
                        mentions_bot = True
                elif isinstance(segment_type, str) and segment_type:
                    parts.append(ContentPart(kind="other", value=segment_type))
                else:
                    raise ValueError("message segment requires a type")
        else:
            raise ValueError("message must be text or an array of segments")
        stamp = raw.get("time")
        try:
            if type(stamp) not in (int, float) or not isfinite(stamp):
                raise ValueError("Invalid Unix timestamp")
            occurred_at = datetime.fromtimestamp(stamp, UTC)
        except (OSError, OverflowError, ValueError) as error:
            raise ValueError("Invalid Unix timestamp") from error
        display_name = sender.get("card") or sender.get("nickname")
        return QQObservation(
            id=uuid4().hex,
            source=SourceRef(platform="qq", account_id=self.bot_id,
                             source_message_id=message_id, occurred_at=occurred_at),
            subject=subject,
            actor=ActorRef(external_id=sender_id,
                           display_name=str(display_name) if display_name is not None else None),
            content=tuple(parts), received_at=datetime.now(UTC),
            message_type=kind, group_id=group_id, is_self=is_self,
            mentions_bot=mentions_bot,
        )
