"""Atomic observation revisions, judgement reservation and processing states."""

from datetime import UTC, datetime, timedelta
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
from ..schemas.wake import ACTIVE_STATUSES, WakeItem, WakeOutcome, WakePayload, WakeView, request_fingerprint


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
    def __init__(self, database: Database, *, boot_id: UUID, clock=lambda: datetime.now(UTC)):
        self.database = database
        self.boot_id = boot_id
        self.clock = clock

    @staticmethod
    async def lock_scope(session, source, chat_id):
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))'),
                              {'identity': json.dumps(['scope', source, chat_id], separators=(',', ':'))})

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
            await self.lock_scope(session, draft.source, draft.chat_id)
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
            maximum = await session.scalar(select(func.max(ObservationRow.observed_at)).where(
                ObservationRow.source == draft.source, ObservationRow.chat_id == draft.chat_id))
            now = self.clock()
            values['observed_at'] = max(now, maximum + timedelta(microseconds=1)) if maximum is not None else now
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

    async def latest_open_items(self, chat_id, *, limit=20, routing_fair=False):
        async with self.database.session() as session:
            query = select(ObservationRow).where(*self._pending(), ObservationRow.chat_id == chat_id)
            if routing_fair:
                # Untouched arrivals get priority; deferred replies rotate by last
                # attempt, so neither a blocked head nor a full head window wins forever.
                query = query.order_by(func.coalesce(ObservationRow.decision_detail['routing_attempted_at'].astext, ''))
            rows = await session.scalars(query.order_by(ObservationRow.observed_at, ObservationRow.id).limit(limit))
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

    @staticmethod
    def _real_active():
        return and_(WakeRow.handoff['mode'].astext == 'gateway',
                    func.coalesce(WakeRow.handoff['local_terminal'].astext, 'false') != 'true',
                    func.coalesce(WakeRow.handoff['release_complete'].astext, 'false') != 'true',
                    or_(func.coalesce(WakeRow.handoff['protocol_version'].astext, '1') != '2',
                        WakeRow.handoff['submission_may_have_run'].astext == 'true'))

    async def update_handoff(self, wake_id, changes, *, status=None):
        async with self.database.begin() as session:
            row = await session.get(WakeRow, UUID(str(wake_id)), with_for_update=True)
            if row is None:
                raise NotFound('Wake not found')
            row.handoff = {**(row.handoff or {}), **changes}
            if status is not None:
                row.status = status
            row.updated_at = datetime.now(UTC)
            await session.flush()
            return wake_view(row)

    async def acknowledge_notify(self, wake_id, *, generation):
        async with self.database.begin() as db:
            row = await db.get(WakeRow, UUID(str(wake_id)), with_for_update=True)
            if row is None:
                raise NotFound('Wake not found')
            if row.handoff.get('notify_generation', 0) == generation:
                row.handoff = {**row.handoff, 'notify_pending': False}

    async def chats_to_scan(self):
        async with self.database.session() as session:
            pending = select(ObservationRow.chat_id).where(*self._pending())
            active = select(WakeRow.chat_id).where(or_(self._real_active(), and_(
                WakeRow.boot_id == self.boot_id, WakeRow.status.in_(ACTIVE_STATUSES))))
            return tuple(sorted((await session.scalars(pending.union(active))).all()))

    async def active_wake(self, chat_id):
        async with self.database.session() as session:
            row = await session.scalar(select(WakeRow).where(WakeRow.chat_id == chat_id, or_(
                self._real_active(), and_(WakeRow.boot_id == self.boot_id, WakeRow.status.in_(ACTIVE_STATUSES),
                    func.coalesce(WakeRow.handoff['mode'].astext, 'mock') != 'gateway'))))
            return None if row is None else wake_view(row)

    async def commit_judgement(self, chat_id: str, outcome: JudgementOutcome, *, protocol_version=1,
                               target_session_id=None, target_waiting_wake_id=None):
        selected = [value.item.id for value in outcome.selected]
        ignored = [identifier for group in outcome.ignored for identifier in group.item_ids]
        identifiers = selected + ignored
        if len(set(identifiers)) != len(identifiers):
            raise StaleSelection('Judgement IDs overlap')
        if not identifiers:
            return None
        try:
            async with self.database.begin() as session:
                await self.lock_scope(session, 'qq', chat_id)
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
                if protocol_version == 2 and target_waiting_wake_id is not None:
                    return await self._merge_waiting(session, target_waiting_wake_id, chat_id, outcome, by_id)
                target = str(UUID(str(target_session_id))) if target_session_id is not None else None
                if protocol_version == 2:
                    for identifier in selected:
                        assigned = by_id[identifier].detail.get('argus_session_id')
                        if assigned is not None and assigned != target:
                            raise StaleSelection('Semantic assignment changed')
                now, identifier = datetime.now(UTC), uuid4()
                payload = WakePayload(wake_id=identifier, boot_id=self.boot_id, chat_id=chat_id,
                    **({'protocol_version': 2, 'target_session_id': target} if protocol_version == 2 else {}),
                    reason=outcome.reason, rule_id=outcome.rule_id,
                    items=tuple(WakeItem(item_id=value.item.id, source=value.item.source,
                        chat_id=chat_id, message_id=value.item.message_id, revision=value.item.revision,
                        locator=value.item.locator, excerpt=value.excerpt, truncated=value.truncated)
                        for value in outcome.selected))
                wake = WakeRow(id=identifier, boot_id=self.boot_id, chat_id=chat_id, status='waiting',
                    reason=outcome.reason, rule_id=outcome.rule_id, payload=payload.model_dump(mode='json'),
                    decision_detail=outcome.decision_detail, attempt_count=0, created_at=now, updated_at=now,
                    handoff=({'mode': 'gateway', 'protocol_version': 2, 'target_session_id': target,
                        'request_fingerprint': request_fingerprint(payload), 'routing_reason': outcome.reason}
                        if protocol_version == 2 else {'mode': 'mock'}))
                session.add(wake)
                await session.flush()
                for identifier in selected:
                    by_id[identifier].wake_id = wake.id
                    if protocol_version == 2 and target is not None:
                        by_id[identifier].detail = {**by_id[identifier].detail, 'argus_session_id': target}
                    by_id[identifier].status_reason = outcome.reason
                    by_id[identifier].decision_detail = outcome.decision_detail
                await session.flush()
                return wake_view(wake)
        except IntegrityError as error:
            if getattr(error.orig, 'sqlstate', None) == '23505':
                raise ActiveWakeExists('Chat already has an active request') from None
            raise

    async def reply_session(self, chat_id, message_id):
        async with self.database.session() as db:
            row = await db.scalar(select(ObservationRow).where(ObservationRow.chat_id == chat_id,
                ObservationRow.message_id == str(message_id), ObservationRow.deleted.is_(False), self._latest())
                .order_by(ObservationRow.revision.desc()).limit(1))
            return None if row is None else row.detail.get('argus_session_id')

    async def routing_deferred(self, item_ids, *, cursor=None, reason):
        async with self.database.begin() as db:
            rows = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(item_ids))))
            if not rows:
                return
            await self.lock_scope(db, 'qq', rows[0].chat_id)
            for row in rows:
                if row.boot_id == self.boot_id and row.status == 'new' and row.wake_id is None:
                    row.decision_detail = {**(row.decision_detail or {}), 'routing_reason': reason,
                        'routing_cursor': cursor, 'routing_attempted_at': datetime.now(UTC).isoformat()}

    async def waiting_wakes(self, chat_id, *, limit=20):
        async with self.database.session() as db:
            rows = await db.scalars(select(WakeRow).where(WakeRow.chat_id == chat_id,
                WakeRow.boot_id == self.boot_id, WakeRow.handoff['protocol_version'].astext == '2',
                func.coalesce(WakeRow.handoff['submission_may_have_run'].astext, 'false') != 'true',
                func.coalesce(WakeRow.handoff['local_terminal'].astext, 'false') != 'true',
                func.coalesce(WakeRow.handoff['release_complete'].astext, 'false') != 'true')
                .order_by(WakeRow.created_at, WakeRow.id).limit(limit))
            return tuple(wake_view(row) for row in rows)

    async def fail_stale_submission(self, wake_id):
        """Retire an unsent batch and atomically release only untouched current work."""
        async with self.database.begin() as db:
            existing = await db.get(WakeRow, UUID(str(wake_id)))
            if existing is None:
                raise NotFound('Wake not found')
            await self.lock_scope(db, 'qq', existing.chat_id)
            row = await db.get(WakeRow, existing.id, with_for_update=True, populate_existing=True)
            if row.boot_id != self.boot_id or row.handoff.get('submission_may_have_run'):
                return
            items = list(await db.scalars(select(ObservationRow).where(ObservationRow.wake_id == row.id,
                ObservationRow.boot_id == self.boot_id, ObservationRow.status == 'new',
                ObservationRow.deleted.is_(False), ObservationRow.exposed_wake_id.is_(None), self._latest())))
            row.handoff = {**row.handoff, 'local_terminal': True}
            row.status = 'failed'
            row.updated_at = self.clock()
            for item in items:
                item.wake_id = None

    async def prepare_submission(self, wake_id):
        """Reserve the sole possible-running owner before any external call."""
        async with self.database.begin() as db:
            existing = await db.get(WakeRow, UUID(str(wake_id)))
            if existing is None:
                raise NotFound('Wake not found')
            await self.lock_scope(db, 'qq', existing.chat_id)
            items = list(await db.scalars(select(ObservationRow).where(ObservationRow.wake_id == existing.id)))
            for item in sorted(items, key=self._identity):
                await self._lock_message(db, item)
            row = await db.get(WakeRow, existing.id, with_for_update=True, populate_existing=True)
            owner = await db.scalar(select(WakeRow.id).where(WakeRow.chat_id == row.chat_id,
                WakeRow.id != row.id, self._real_active()).limit(1))
            if owner is not None:
                raise ActiveWakeExists('Scope owner may still be running')
            if row.boot_id != self.boot_id and not row.handoff.get('submission_may_have_run'):
                raise StaleSelection('Old boot cannot submit new work')
            if not row.handoff.get('submission_may_have_run'):
                expected = {value.item_id for value in wake_view(row).payload.items}
                valid = set(await db.scalars(select(ObservationRow.id).where(ObservationRow.id.in_(expected),
                    ObservationRow.boot_id == self.boot_id, ObservationRow.deleted.is_(False), self._latest())))
                if valid != expected:
                    raise StaleSelection('Request changed before submission')
            fingerprint = request_fingerprint(wake_view(row).payload)
            if row.handoff.get('request_fingerprint', fingerprint) != fingerprint:
                raise StaleSelection('Frozen request changed')
            row.handoff = {**row.handoff, 'mode': 'gateway', 'request_fingerprint': fingerprint,
                'submission_may_have_run': True}
            row.status = 'sent'
            await db.flush()
            return wake_view(row)

    async def assign_to_session(self, item_ids, *, session_id, wake_id=None) -> None:
        session_id = str(UUID(str(session_id)))
        identifiers = {UUID(str(value)) for value in item_ids}
        if not identifiers:
            return
        async with self.database.begin() as db:
            rows = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(identifiers))))
            if len(rows) != len(identifiers) or len({row.chat_id for row in rows}) != 1:
                raise StaleSelection('Invalid assignment scope')
            await self.lock_scope(db, 'qq', rows[0].chat_id)
            rows = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(identifiers))
                .with_for_update().execution_options(populate_existing=True)))
            for row in sorted(rows, key=self._identity):
                await self._lock_message(db, row)
            valid = set(await db.scalars(select(ObservationRow.id).where(ObservationRow.id.in_(identifiers),
                *self._pending())))
            if valid != identifiers:
                raise StaleSelection('Assignment changed')
            if wake_id is not None:
                wake = await db.get(WakeRow, UUID(str(wake_id)), with_for_update=True)
                if wake is None or wake.chat_id != rows[0].chat_id or wake.handoff.get('target_session_id') != session_id:
                    raise StaleSelection('Invalid assignment wake')
            for row in rows:
                if row.detail.get('argus_session_id', session_id) != session_id:
                    raise StaleSelection('Already assigned to another session')
                row.detail = {**row.detail, 'argus_session_id': session_id}
                if wake_id is not None:
                    row.wake_id = UUID(str(wake_id))

    async def _merge_waiting(self, db, wake_id, chat_id, outcome, by_id):
        wake = await db.get(WakeRow, UUID(str(wake_id)), with_for_update=True)
        if (wake is None or wake.chat_id != chat_id or wake.boot_id != self.boot_id
                or wake.handoff.get('protocol_version') != 2 or wake.handoff.get('submission_may_have_run')
                or wake.handoff.get('local_terminal') or wake.handoff.get('binding')):
            raise StaleSelection('Waiting candidate changed')
        target = wake.handoff.get('target_session_id')
        for value in outcome.selected:
            row = by_id[value.item.id]
            if row.detail.get('argus_session_id', target) != target:
                raise StaleSelection('Semantic assignment changed')
            row.wake_id = wake.id
            if target is not None:
                row.detail = {**row.detail, 'argus_session_id': target}
        payload = wake_view(wake).payload
        additions = tuple(WakeItem(item_id=value.item.id, source=value.item.source, chat_id=chat_id,
            message_id=value.item.message_id, revision=value.item.revision, locator=value.item.locator,
            excerpt=value.excerpt, truncated=value.truncated) for value in outcome.selected)
        if len(payload.items) + len(additions) > 20 or sum(len(value.excerpt) for value in (*payload.items, *additions)) > 20000:
            raise StaleSelection('Waiting batch full')
        payload = payload.model_copy(update={'items': (*payload.items, *additions)})
        wake.payload = payload.model_dump(mode='json')
        wake.handoff = {**wake.handoff, 'request_fingerprint': request_fingerprint(payload)}
        await db.flush()
        return wake_view(wake)

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
        existing = await session.get(WakeRow, wake_id)
        if existing is not None:
            await self.lock_scope(session, 'qq', existing.chat_id)
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
