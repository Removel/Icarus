"""Convert SnowLuma OneBot messages into QQ observations."""

from datetime import UTC, datetime
from math import isfinite
from typing import Any
from uuid import uuid4

from ..entity.qq_observation import QQObservation
from ..entity.observation import ActorRef, ContentPart, SourceRef, SubjectRef


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


class IgnoredOneBotEvent(ValueError):
    """A recognized, authenticated event outside the chat observation scope."""


class QQHandler:
    def __init__(self, bot_id: str, *, receive=None) -> None:
        self.bot_id = _positive_id(bot_id, 'bot_id')
        self.receive = receive

    async def handle(self, raw):
        try:
            observation = self.parse(raw)
        except IgnoredOneBotEvent:
            return None
        if self.receive is None:
            raise RuntimeError('Handler has no receiving service')
        await self.receive(observation)
        return observation

    def parse(self, raw: dict[str, Any]) -> QQObservation:
        if not isinstance(raw, dict):
            raise ValueError("OneBot event must be an object")
        if _positive_id(raw.get("self_id"), "self_id") != self.bot_id:
            raise ValueError("self_id does not match the configured bot")
        if raw.get("post_type") in {"meta_event", "notice", "request"}:
            raise IgnoredOneBotEvent("Non-chat OneBot event acknowledged without processing")
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
