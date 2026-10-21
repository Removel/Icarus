"""Atomic observation revisions, judgement reservation and processing states."""

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import and_, exists, func, or_, select, text, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import aliased

from ...core.database import Database
from ..models.observation import ObservationRow
from ..models.wake import WakeRow
from ..schemas.observation import ObservedItem, ObservedItemDraft, PendingWindow
from ..schemas.judgement import JudgementOutcome
from ..schemas.wake import ACTIVE_STATUSES, WakeItem, WakeOutcome, WakePayload, WakeView


class NotFound(LookupError):
    pass


class StaleSelection(RuntimeError):
    pass


class ActiveWakeExists(RuntimeError):
    pass


def observed(row):
    return ObservedItem.model_validate({column.name: getattr(row, column.name)
                                        for column in ObservationRow.__table__.columns})


def wake_view(row):
    return WakeView.model_validate({column.name: getattr(row, column.name)
                                   for column in WakeRow.__table__.columns})


class ObservationRepository:
    def __init__(self, database: Database, *, boot_id: UUID):
        self.database = database
        self.boot_id = boot_id

    @staticmethod
    def _identity(value):
        return json.dumps([value.source, value.chat_id, value.message_id], separators=(',', ':'))

    async def _lock_message(self, session, value):
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))'),
                              {'identity': self._identity(value)})

    @staticmethod
    def _latest():
        newer = aliased(ObservationRow)
        return ~exists(select(newer.id).where(newer.source == ObservationRow.source,
                                              newer.chat_id == ObservationRow.chat_id,
                                              newer.message_id == ObservationRow.message_id,
                                              newer.revision > ObservationRow.revision))

    def _pending(self):
        return (ObservationRow.boot_id == self.boot_id, ObservationRow.status == 'new',
                ObservationRow.wake_id.is_(None), ObservationRow.deleted.is_(False), self._latest())

    async def upsert_revision(self, draft: ObservedItemDraft):
        value = draft.model_dump(mode='json')
        fingerprint = sha256(json.dumps([value['content'], draft.deleted], sort_keys=True,
                                       separators=(',', ':')).encode()).hexdigest()
        async with self.database.begin() as session:
            await self._lock_message(session, draft)
            identity = (ObservationRow.source == draft.source, ObservationRow.chat_id == draft.chat_id,
                        ObservationRow.message_id == draft.message_id)
            duplicate = await session.scalar(select(ObservationRow).where(*identity,
                                               ObservationRow.fingerprint == fingerprint))
            if duplicate is not None:
                return observed(duplicate), False
            latest = await session.scalar(select(ObservationRow).where(*identity)
                                          .order_by(ObservationRow.revision.desc()).limit(1))
            if latest is not None and latest.deleted and not draft.deleted:
                return observed(latest), False
            values = draft.model_dump()
            values['content'] = value['content']
            row = ObservationRow(**values, id=uuid4(), boot_id=self.boot_id,
                                 revision=1 if latest is None else latest.revision + 1,
                                 fingerprint=fingerprint, status='new')
            session.add(row)
            await session.flush()
            return observed(row), True

    async def get_item(self, item_id):
        async with self.database.session() as session:
            row = await session.get(ObservationRow, item_id)
            if row is None:
                raise NotFound('Observation not found')
            return observed(row)

    async def latest_open_items(self, chat_id, *, limit=20):
        async with self.database.session() as session:
            rows = await session.scalars(select(ObservationRow).where(*self._pending(),
                ObservationRow.chat_id == chat_id).order_by(ObservationRow.observed_at, ObservationRow.id).limit(limit))
            return tuple(observed(row) for row in rows)

    async def recent_context(self, chat_id, *, before, limit=10):
        if limit == 0:
            return ()
        async with self.database.session() as session:
            rows = await session.scalars(select(ObservationRow).where(
                ObservationRow.boot_id == self.boot_id, ObservationRow.chat_id == chat_id,
                ObservationRow.deleted.is_(False), self._latest(),
                tuple_(ObservationRow.observed_at, ObservationRow.id) < before)
                .order_by(ObservationRow.observed_at.desc(), ObservationRow.id.desc()).limit(limit))
            return tuple(reversed([observed(row) for row in rows]))

    async def pending_window(self, chat_id):
        async with self.database.session() as session:
            row = (await session.execute(select(func.min(ObservationRow.observed_at),
                func.max(ObservationRow.observed_at), func.bool_or(ObservationRow.strong_signal))
                .where(*self._pending(), ObservationRow.chat_id == chat_id))).one()
            return None if row[0] is None else PendingWindow(first_observed_at=row[0],
                last_observed_at=row[1], has_strong_signal=bool(row[2]))

    async def chats_to_scan(self):
        async with self.database.session() as session:
            pending = select(ObservationRow.chat_id).where(*self._pending())
            active = select(WakeRow.chat_id).where(WakeRow.boot_id == self.boot_id,
                                                 WakeRow.status.in_(ACTIVE_STATUSES))
            return tuple(sorted((await session.scalars(pending.union(active))).all()))

    async def active_wake(self, chat_id):
        async with self.database.session() as session:
            row = await session.scalar(select(WakeRow).where(WakeRow.boot_id == self.boot_id,
                WakeRow.chat_id == chat_id, WakeRow.status.in_(ACTIVE_STATUSES)))
            return None if row is None else wake_view(row)

    async def commit_judgement(self, chat_id: str, outcome: JudgementOutcome):
        selected = [value.item.id for value in outcome.selected]
        ignored = [identifier for group in outcome.ignored for identifier in group.item_ids]
        identifiers = selected + ignored
        if len(set(identifiers)) != len(identifiers):
            raise StaleSelection('Judgement IDs overlap')
        if not identifiers:
            return None
        try:
            async with self.database.begin() as session:
                rows = (await session.scalars(select(ObservationRow).where(
                    ObservationRow.id.in_(identifiers)))).all()
                for row in sorted(rows, key=self._identity):
                    await self._lock_message(session, row)
                if len(rows) != len(identifiers):
                    raise StaleSelection('Unknown judgement IDs')
                valid = set((await session.scalars(select(ObservationRow.id).where(
                    ObservationRow.id.in_(identifiers), ObservationRow.chat_id == chat_id,
                    *self._pending()))).all())
                if valid != set(identifiers):
                    raise StaleSelection('Observation changed during judgement')
                by_id = {row.id: row for row in rows}
                for group in outcome.ignored:
                    for identifier in group.item_ids:
                        by_id[identifier].status = 'ignored'
                        by_id[identifier].status_reason = group.reason
                if outcome.decision == 'ignore':
                    for identifier in selected:
                        by_id[identifier].status = 'ignored'
                        by_id[identifier].status_reason = outcome.reason
                        by_id[identifier].decision_detail = outcome.decision_detail
                    return None
                if not selected:
                    return None
                now, identifier = datetime.now(UTC), uuid4()
                payload = WakePayload(wake_id=identifier, boot_id=self.boot_id, chat_id=chat_id,
                    reason=outcome.reason, rule_id=outcome.rule_id,
                    items=tuple(WakeItem(item_id=value.item.id, source=value.item.source,
                        chat_id=chat_id, message_id=value.item.message_id, revision=value.item.revision,
                        locator=value.item.locator, excerpt=value.excerpt, truncated=value.truncated)
                        for value in outcome.selected))
                wake = WakeRow(id=identifier, boot_id=self.boot_id, chat_id=chat_id, status='waiting',
                    reason=outcome.reason, rule_id=outcome.rule_id, payload=payload.model_dump(mode='json'),
                    decision_detail=outcome.decision_detail, attempt_count=0, created_at=now, updated_at=now)
                session.add(wake)
                await session.flush()
                for identifier in selected:
                    by_id[identifier].wake_id = wake.id
                    by_id[identifier].status_reason = outcome.reason
                    by_id[identifier].decision_detail = outcome.decision_detail
                await session.flush()
                return wake_view(wake)
        except IntegrityError as error:
            if getattr(error.orig, 'sqlstate', None) == '23505':
                raise ActiveWakeExists('Chat already has an active request') from None
            raise

    async def get_wake(self, wake_id):
        async with self.database.session() as session:
            row = await session.get(WakeRow, wake_id)
            if row is None:
                raise NotFound('Wake not found')
            return wake_view(row)

    async def list_wakes(self, *, status=None, chat_id=None, limit=50, offset=0):
        query = select(WakeRow)
        if status is not None:
            query = query.where(WakeRow.status == status)
        if chat_id is not None:
            query = query.where(WakeRow.chat_id == chat_id)
        async with self.database.session() as session:
            rows = await session.scalars(query.order_by(WakeRow.created_at.desc(), WakeRow.id.desc())
                                         .limit(limit).offset(offset))
            return tuple(wake_view(row) for row in rows)

    async def wake_items(self, wake_id):
        async with self.database.session() as session:
            rows = await session.scalars(select(ObservationRow).where(ObservationRow.wake_id == wake_id)
                                         .order_by(ObservationRow.observed_at, ObservationRow.id))
            return tuple(observed(row) for row in rows)

    async def _request(self, session, wake_id):
        row = await session.scalar(select(WakeRow).where(WakeRow.id == wake_id,
            WakeRow.boot_id == self.boot_id).with_for_update())
        if row is None:
            raise NotFound('Current wake not found')
        return row

    async def begin_attempt(self, wake_id, *, now):
        async with self.database.begin() as session:
            row = await self._request(session, wake_id)
            if row.status != 'waiting':
                raise ValueError('Wake is not waiting')
            if row.attempt_count == 0:
                items = (await session.scalars(select(ObservationRow).where(
                    ObservationRow.wake_id == wake_id))).all()
                for item in sorted(items, key=self._identity):
                    await self._lock_message(session, item)
                expected = {UUID(value['item_id']) for value in row.payload['items']}
                valid = set((await session.scalars(select(ObservationRow.id).where(
                    ObservationRow.id.in_(expected), ObservationRow.boot_id == self.boot_id,
                    ObservationRow.deleted.is_(False), self._latest()))).all())
                if valid != expected:
                    raise StaleSelection('Request changed before first submission')
            row.status, row.updated_at = 'sent', now
            row.attempt_count += 1
            row.next_attempt_at = None
            await session.flush()
            return wake_view(row)

    async def schedule_retry(self, wake_id, *, error_type, next_attempt_at):
        async with self.database.begin() as session:
            row = await self._request(session, wake_id)
            if row.status not in ('waiting', 'sent'):
                return
            row.status, row.error_type = 'waiting', error_type
            row.next_attempt_at = next_attempt_at
            row.updated_at = datetime.now(UTC)

    async def mark_accepted(self, wake_id, *, now, deadline_at):
        async with self.database.begin() as session:
            row = await self._request(session, wake_id)
            if row.status not in ('waiting', 'sent'):
                return
            row.status, row.accepted_at, row.deadline_at = 'accepted', now, deadline_at
            row.updated_at, row.error_type = now, None
            items = await session.scalars(select(ObservationRow).where(ObservationRow.wake_id == wake_id))
            for item in items:
                item.status = 'delivered'

    async def record_poll_error(self, wake_id, *, error_type):
        async with self.database.begin() as session:
            row = await self._request(session, wake_id)
            if row.status == 'accepted':
                row.error_type = error_type

    async def finish_wake(self, wake_id, *, status, outcome, error_type=None):
        if status not in ('resolved', 'no_action', 'failed'):
            raise ValueError('Invalid terminal status')
        async with self.database.begin() as session:
            row = await self._request(session, wake_id)
            encoded = None if outcome is None else outcome.model_dump(mode='json')
            if row.status not in ACTIVE_STATUSES:
                if row.status == status and row.outcome == encoded:
                    return
                raise ValueError('Conflicting terminal outcome')
            expected = {UUID(item['item_id']) for item in row.payload['items']}
            if outcome is not None and (outcome.wake_id != wake_id or set(outcome.item_ids) != expected
                                        or outcome.status != status):
                raise ValueError('Outcome does not match request')
            row.status, row.outcome, row.error_type = status, encoded, error_type
            row.updated_at = datetime.now(UTC)
            items = await session.scalars(select(ObservationRow).where(ObservationRow.wake_id == wake_id))
            for item in items:
                item.status = 'failed' if status == 'failed' else 'processed'
                item.status_reason = error_type or (outcome.reason if outcome else status)
