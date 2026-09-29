"""Explicit QQ observation storage format."""

from datetime import UTC

from apps.argus.src.entity.QQObservation import QQObservation
from apps.argus.src.entity.observation import ActorRef, ContentPart, SourceRef, SubjectRef
from .entities import _ObservationRow


class UnsupportedObservationError(ValueError):
    pass


class QQObservationCodec:
    subtype = "qq_message"
    version = 1

    def to_row_fields(self, obs: QQObservation) -> dict[str, object]:
        return {
            "id": obs.id,
            "platform": obs.source.platform,
            "account_id": obs.source.account_id,
            "source_message_id": obs.source.source_message_id,
            "subject": obs.subject.key,
            "sender_id": obs.actor.external_id,
            "sender_name": obs.actor.display_name,
            "occurred_at": obs.source.occurred_at,
            "received_at": obs.received_at,
            "content": [{"kind": part.kind, "value": part.value} for part in obs.content],
            "subtype": self.subtype,
            "detail_version": self.version,
            "detail": {
                "message_type": obs.message_type,
                "group_id": obs.group_id,
                "is_self": obs.is_self,
                "mentions_bot": obs.mentions_bot,
            },
            "updated_at": obs.received_at,
        }

    def from_row(self, row: _ObservationRow) -> QQObservation:
        if row.subtype != self.subtype or row.detail_version != self.version:
            raise UnsupportedObservationError(f"Unsupported observation {row.subtype}:{row.detail_version}")
        platform, kind, external_id = row.subject.split(":", 2)
        try:
            return QQObservation(
                id=row.id,
                source=SourceRef(
                    platform=row.platform, account_id=row.account_id,
                    source_message_id=row.source_message_id,
                    occurred_at=row.occurred_at.replace(tzinfo=UTC),
                ),
                subject=SubjectRef(platform=platform, kind=kind, external_id=external_id),
                actor=ActorRef(external_id=row.sender_id, display_name=row.sender_name),
                content=tuple(ContentPart(**part) for part in row.content),
                received_at=row.received_at.replace(tzinfo=UTC),
                message_type=row.detail["message_type"],
                group_id=row.detail["group_id"],
                is_self=row.detail["is_self"],
                mentions_bot=row.detail["mentions_bot"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise UnsupportedObservationError("Malformed QQ observation") from error
