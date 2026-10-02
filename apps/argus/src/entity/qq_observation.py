"""QQ-specific observation facts and their table representation."""
from dataclasses import dataclass
from typing import Literal
from .observation import Observation, ActorRef, ContentPart, SourceRef, SubjectRef
from .records import ObservationRow
from ..utils.datetime_utils import utc


class UnsupportedObservationError(ValueError):
    pass


@dataclass(frozen=True, kw_only=True)
class QQObservation(Observation):
    message_type: Literal['group', 'private']
    group_id: str | None
    is_self: bool
    mentions_bot: bool

    def to_row(self, *, is_strong_signal=False):
        return ObservationRow(id=self.id, platform=self.source.platform, account_id=self.source.account_id,
            source_message_id=self.source.source_message_id, subject=self.subject.key,
            sender_id=self.actor.external_id, sender_name=self.actor.display_name,
            occurred_at=self.source.occurred_at, received_at=self.received_at,
            content=[dict(kind=part.kind, value=part.value) for part in self.content],
            subtype='qq_message', detail_version=1, detail=dict(message_type=self.message_type,
                group_id=self.group_id, is_self=self.is_self, mentions_bot=self.mentions_bot),
            is_strong_signal=is_strong_signal)

    @classmethod
    def from_row(cls, row):
        if row.subtype != 'qq_message' or row.detail_version != 1:
            raise UnsupportedObservationError(f'Unsupported observation {row.subtype}:{row.detail_version}')
        try:
            platform, kind, external_id = row.subject.split(':', 2)
            return cls(id=row.id, source=SourceRef(platform=row.platform, account_id=row.account_id,
                source_message_id=row.source_message_id, occurred_at=utc(row.occurred_at)),
                subject=SubjectRef(platform=platform, kind=kind, external_id=external_id),
                actor=ActorRef(external_id=row.sender_id, display_name=row.sender_name),
                content=tuple(ContentPart(**part) for part in row.content), received_at=utc(row.received_at),
                message_type=row.detail['message_type'], group_id=row.detail['group_id'],
                is_self=row.detail['is_self'], mentions_bot=row.detail['mentions_bot'])
        except (KeyError, TypeError, ValueError) as error:
            raise UnsupportedObservationError('Malformed QQ observation') from error
