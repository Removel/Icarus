"""Authenticated per-wake handoff transactions on the existing business tables."""

from datetime import UTC, datetime
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import String, and_, cast, or_, select, tuple_

from ..models.observation import ObservationRow
from ..models.wake import WakeRow
from .observation_repository import NotFound, ObservationRepository, observed


class HandoffConflict(ValueError):
    def __init__(self, code='conflict'):
        super().__init__(code)
        self.code = code


class HandoffRepository:
    def __init__(self, database, *, boot_id=None, policy=None, clock=lambda: datetime.now(UTC)):
        self.database, self.clock, self.boot_id = database, clock, boot_id
        self.policy = policy

    async def bind(self, wake_id, binding):
        binding = dict(binding)
        if binding.get('protocol_version') == 1:
            binding.pop('protocol_version')
        async with self.database.begin() as db:
            existing_row = await db.get(WakeRow, UUID(str(wake_id)))
            if existing_row is None:
                raise NotFound('Wake not found')
            await ObservationRepository.lock_scope(db, 'qq', existing_row.chat_id)
            items = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(self._initial_ids(existing_row)))))
            locker = ObservationRepository(self.database, boot_id=existing_row.boot_id)
            for item in sorted(items, key=locker._identity):
                await locker._lock_message(db, item)
            row = await db.get(WakeRow, UUID(str(wake_id)), with_for_update=True, populate_existing=True)
            if row is None:
                raise NotFound('Wake not found')
            scope = binding['scope']
            chat_id = f"{scope['source']}:{scope['account_id']}:{scope['chat_kind']}:{scope['external_id']}"
            if chat_id != row.chat_id or str(row.boot_id) != binding['origin_boot_id']:
                raise HandoffConflict('scope_mismatch')
            if binding.get('request_fingerprint') is not None:
                from ..schemas.wake import WakePayload, request_fingerprint
                if binding['request_fingerprint'] != request_fingerprint(WakePayload.model_validate(row.payload)):
                    raise HandoffConflict('submission_conflict')
            version = row.payload.get('protocol_version', 1)
            if binding.get('protocol_version', 1) != version:
                raise HandoffConflict('protocol_mismatch')
            target = row.handoff.get('target_session_id')
            if version == 2 and target is not None and target != binding['session_id']:
                raise HandoffConflict('session_mismatch')
            existing = (row.handoff or {}).get('binding')
            if existing is not None:
                if any(existing.get(key) != value for key, value in binding.items()):
                    raise HandoffConflict()
                return existing
            ids = self._initial_ids(row)
            if not ids:
                raise HandoffConflict('empty_initial')
            items = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(ids))))
            initial = max(items, key=lambda item: (item.observed_at, item.id))
            result = {**binding, 'binding_id': str(uuid4()),
                      'initial_cursor': {'observed_at': initial.observed_at.isoformat(), 'id': str(initial.id)}}
            if version == 2:
                owner = await db.scalar(select(WakeRow.id).where(WakeRow.chat_id == row.chat_id,
                    WakeRow.id != row.id, ObservationRepository._real_active()).limit(1))
                if owner is not None:
                    raise HandoffConflict('scope_busy')
                for item in items:
                    if item.detail.get('argus_session_id', binding['session_id']) != binding['session_id']:
                        raise HandoffConflict('session_mismatch')
                    item.detail = {**item.detail, 'argus_session_id': binding['session_id']}
            row.handoff = {**(row.handoff or {}), 'mode': 'gateway', 'binding': result,
                           **({'protocol_version': 2, 'target_session_id': binding['session_id'],
                               'submission_may_have_run': True} if version == 2 else {}),
                           'report_cursor': 0, 'report_log': {}, 'phase': 'bound'}
            await db.flush()
            return result

    async def attach(self, wake_id, *, session_id, observation_ids) -> dict:
        session_id = str(UUID(str(session_id)))
        identifiers = {UUID(str(value)) for value in observation_ids}
        if not identifiers or len(identifiers) > 20:
            raise HandoffConflict('invalid_attachment')
        async with self.database.begin() as db:
            wake = await db.get(WakeRow, UUID(str(wake_id)))
            if wake is None:
                raise NotFound('Wake not found')
            await ObservationRepository.lock_scope(db, 'qq', wake.chat_id)
            rows = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(identifiers))))
            locker = ObservationRepository(self.database, boot_id=wake.boot_id)
            for item in sorted(rows, key=locker._identity):
                await locker._lock_message(db, item)
            wake = await db.get(WakeRow, wake.id, with_for_update=True, populate_existing=True)
            binding = wake.handoff.get('binding')
            if 'finalize' in wake.handoff or wake.handoff.get('local_terminal') or wake.handoff.get('release_complete'):
                raise HandoffConflict('execution_closed')
            if (not binding or binding.get('protocol_version', 1) != 2 or binding['session_id'] != session_id
                    or (self.boot_id is not None and str(self.boot_id) != binding['origin_boot_id'])):
                raise HandoffConflict('session_mismatch')
            if len(rows) != len(identifiers):
                raise HandoffConflict('invalid_attachment')
            latest = set(await db.scalars(select(ObservationRow.id).where(ObservationRow.id.in_(identifiers), locker._latest())))
            for item in rows:
                if (item.id not in latest or item.deleted or item.is_self or item.chat_id != wake.chat_id
                        or str(item.boot_id) != binding['origin_boot_id'] or item.status not in ('new', 'delivered')
                        or item.wake_id not in (None, wake.id)
                        or item.detail.get('argus_session_id', session_id) != session_id
                        or (self.policy is not None and not self.policy.allows(observed(item)))):
                    raise HandoffConflict('invalid_attachment')
            added = any(item.wake_id != wake.id for item in rows)
            for item in rows:
                item.detail = {**item.detail, 'argus_session_id': session_id}
                item.wake_id = wake.id
            if added:
                wake.handoff = {**wake.handoff, 'notify_pending': True,
                    'notify_generation': wake.handoff.get('notify_generation', 0) + 1}
            return {'attached_ids': sorted(map(str, identifiers))}

    @staticmethod
    def _pending_query(wake, binding):
        return select(ObservationRow).where(ObservationRow.wake_id == wake.id,
            ObservationRow.chat_id == wake.chat_id, ObservationRow.boot_id == UUID(binding['origin_boot_id']),
            ObservationRow.detail['argus_session_id'].astext == binding['session_id'],
            ObservationRow.status.in_(('new', 'delivered')), ObservationRow.deleted.is_(False),
            ObservationRow.is_self.is_(False), ObservationRepository._latest(),
            ObservationRow.exposed_wake_id.is_distinct_from(wake.id))

    async def pending_items(self, binding_id, *, limit=500, after=None):
        async with self.database.session() as db:
            wake, binding = await self._binding(db, binding_id, lock=False)
            if 'finalize' in wake.handoff:
                raise HandoffConflict('execution_closed')
            query = self._pending_query(wake, binding)
            if after is not None:
                query = query.where(tuple_(ObservationRow.observed_at, ObservationRow.id) > after)
            rows = await db.scalars(query.order_by(ObservationRow.observed_at, ObservationRow.id).limit(limit))
            initial = self._initial_ids(wake)
            return [(observed(item), 'work', 'initial' if item.id in initial else 'incidental', True) for item in rows]

    async def pending_summary(self, binding_id, *, policy):
        """Stream exact eligible count without materializing an unbounded body list."""
        async with self.database.session() as db:
            wake, binding = await self._binding(db, binding_id, lock=False)
            if 'finalize' in wake.handoff:
                raise HandoffConflict('execution_closed')
            rows = await db.stream_scalars(self._pending_query(wake, binding)
                .order_by(ObservationRow.observed_at, ObservationRow.id).execution_options(yield_per=500))
            count, latest = 0, None
            async for row in rows:
                item = observed(row)
                if policy.allows(item):
                    count += 1
                    latest = item.observed_at, item.id
            return count, latest

    def _session_pending_query(self, session_id, chat):
        legitimate_old = and_(
            WakeRow.handoff['binding']['protocol_version'].astext == '2',
            WakeRow.handoff['binding']['session_id'].astext == session_id,
            WakeRow.handoff['binding']['origin_boot_id'].astext == cast(ObservationRow.boot_id, String),
            ~WakeRow.handoff.has_key('finalize'),
            WakeRow.handoff['release_complete'].astext.is_distinct_from('true'))
        current = ObservationRow.boot_id == self.boot_id if self.boot_id is not None else False
        return select(ObservationRow).outerjoin(WakeRow, ObservationRow.wake_id == WakeRow.id).where(
            ObservationRow.chat_id == chat, ObservationRow.detail['argus_session_id'].astext == session_id,
            ObservationRow.deleted.is_(False), ObservationRow.is_self.is_(False),
            ObservationRow.status.in_(('new', 'delivered')), ObservationRepository._latest(),
            or_(current, legitimate_old))

    async def session_pending_summary(self, session_id, scope, *, policy):
        session_id = str(UUID(str(session_id)))
        chat = f"{scope['source']}:{scope['account_id']}:{scope['chat_kind']}:{scope['external_id']}"
        async with self.database.session() as db:
            rows = await db.stream_scalars(self._session_pending_query(session_id, chat)
                .execution_options(yield_per=500))
            count, latest = 0, None
            async for row in rows:
                item = observed(row)
                if policy.allows(item):
                    count += 1
                    latest = item.observed_at if latest is None else max(latest, item.observed_at)
            return count, latest

    async def candidates(self, binding_id, *, after=None, before=None, limit=500):
        from sqlalchemy import tuple_
        async with self.database.session() as db:
            wake, binding = await self._binding(db, binding_id, lock=False)
            if 'finalize' in wake.handoff:
                raise HandoffConflict('execution_closed')
            query = select(ObservationRow).where(ObservationRow.chat_id == wake.chat_id)
            if after is not None:
                query = query.where(tuple_(ObservationRow.observed_at, ObservationRow.id) > after)
            if before is not None:
                query = query.where(tuple_(ObservationRow.observed_at, ObservationRow.id) < before)
            order = (ObservationRow.observed_at.desc(), ObservationRow.id.desc()) if before else (
                ObservationRow.observed_at, ObservationRow.id)
            rows = await db.scalars(query.order_by(*order).limit(limit))
            values = []
            for row in rows:
                role, origin, latest = await self.role(db, row, wake, binding)
                values.append((observed(row), role, origin, latest))
            return values

    async def read(self, binding_id, item_id):
        async with self.database.session() as db:
            wake, binding = await self._binding(db, binding_id, lock=False)
            if 'finalize' in wake.handoff:
                raise HandoffConflict('execution_closed')
            item = await db.get(ObservationRow, UUID(str(item_id)))
            if item is None:
                raise NotFound('Observation not found')
            if item.chat_id != wake.chat_id:
                raise HandoffConflict('scope_mismatch')
            role, origin, latest = await self.role(db, item, wake, binding)
            return observed(item), role, origin, latest

    @staticmethod
    def _initial_ids(wake):
        return {UUID(item.get('observation_id', item.get('item_id'))) for item in wake.payload['items']}

    async def _binding(self, db, binding_id, *, lock=True):
        query = select(WakeRow).where(WakeRow.handoff['binding']['binding_id'].astext == str(binding_id))
        row = await db.scalar(query)
        if row is None:
            raise NotFound('Binding not found')
        if lock:
            # Serialize scope writers before taking the wake row lock.
            await ObservationRepository.lock_scope(db, 'qq', row.chat_id)
            row = await db.scalar(select(WakeRow).where(WakeRow.id == row.id).with_for_update().execution_options(populate_existing=True))
        return row, row.handoff['binding']

    async def binding(self, binding_id):
        async with self.database.session() as db:
            wake, binding = await self._binding(db, binding_id, lock=False)
            return {**binding, 'wake_id': str(wake.id), 'closed': 'finalize' in wake.handoff}

    async def role(self, db, item, wake, binding):
        newer = await db.scalar(select(ObservationRow.id).where(
            ObservationRow.source == item.source, ObservationRow.chat_id == item.chat_id,
            ObservationRow.message_id == item.message_id, ObservationRow.revision > item.revision).limit(1))
        initial = item.id in self._initial_ids(wake)
        current_boot = str(item.boot_id) == binding['origin_boot_id']
        if binding.get('protocol_version', 1) == 2:
            available = (item.wake_id == wake.id and item.status in ('new', 'delivered') and
                item.detail.get('argus_session_id') == binding['session_id'] and not item.is_self)
        else:
            available = (item.wake_id == wake.id and item.status in ('new', 'delivered')) if initial else (
                item.wake_id is None and item.status == 'new')
        return ('work' if current_boot and available and not item.deleted and newer is None else 'context',
                'initial' if initial else 'incidental', newer is None)

    async def mark_exposed(self, binding_id, item_ids):
        async with self.database.begin() as db:
            wake, binding = await self._binding(db, binding_id)
            if 'finalize' in wake.handoff:
                raise HandoffConflict('execution_closed')
            rows = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(
                [UUID(str(item)) for item in item_ids])).with_for_update()))
            if len(rows) != len(set(map(str, item_ids))) or any(item.chat_id != wake.chat_id for item in rows):
                raise HandoffConflict('scope_mismatch')
            for item in rows:
                if binding.get('protocol_version', 1) == 2:
                    role, _, _ = await self.role(db, item, wake, binding)
                    if role != 'work':
                        raise HandoffConflict('item_not_reportable')
                item.exposed_wake_id = wake.id
                if item.status == 'new' and (binding.get('protocol_version', 1) == 2 or item.id in self._initial_ids(wake)):
                    item.status = 'delivered'

    async def report(self, binding_id, report, *, policy=None):
        report = {**report, 'items': sorted(report['items'], key=lambda item: item['observation_id'])}
        fingerprint = sha256(json.dumps(report['items'], sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        async with self.database.begin() as db:
            wake, binding = await self._binding(db, binding_id)
            handoff = dict(wake.handoff)
            log = dict(handoff.get('report_log', {}))
            report_id = report['report_id']
            previous = log.get(report_id)
            if previous is not None:
                if previous['fingerprint'] != fingerprint:
                    raise HandoffConflict('report_id_conflict')
                return previous['receipt']
            if 'finalize' in handoff:
                raise HandoffConflict('execution_closed')
            cursor, error = handoff.get('report_cursor', 0), None
            identifiers = [UUID(item['observation_id']) for item in report['items']]
            rows = list(await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(identifiers)).with_for_update()))
            by_id = {str(item.id): item for item in rows}
            accepted_items = {item['observation_id']: item for entry in log.values()
                              if entry['receipt']['disposition'] == 'accepted' for item in entry['items']}
            if not identifiers or len(identifiers) != len(set(identifiers)) or len(identifiers) > 50:
                raise HandoffConflict('invalid_report')
            for value in report['items']:
                item = by_id.get(value['observation_id'])
                previous_item = accepted_items.get(value['observation_id'])
                if previous_item is not None:
                    if previous_item['status'] != value['status']:
                        error = 'result_conflict'
                    continue
                if item is None or item.chat_id != wake.chat_id or item.exposed_wake_id != wake.id:
                    error = 'item_not_reportable'
                    break
                if policy is not None and not policy.allows(observed(item)):
                    error = 'content_restricted'
                    break
                role, _, _ = await self.role(db, item, wake, binding)
                if role != 'work' or value['status'] not in {'handled', 'no_action'}:
                    error = 'item_not_reportable'
                    break
            if error is None:
                for value in report['items']:
                    item = by_id[value['observation_id']]
                    item.wake_id, item.status, item.status_reason = wake.id, 'processed', value['status']
                cursor += 1
            receipt = {'protocol_version': binding.get('protocol_version', 1), 'report_id': report_id,
                       'disposition': 'rejected' if error else 'accepted', 'report_cursor': cursor}
            if error:
                receipt['code'] = error
            log[report_id] = {'fingerprint': fingerprint, 'items': report['items'], 'receipt': receipt}
            wake.handoff = {**handoff, 'report_cursor': cursor, 'report_log': log}
            return receipt

    async def get_report(self, binding_id, report_id):
        async with self.database.session() as db:
            wake, _ = await self._binding(db, binding_id, lock=False)
            entry = wake.handoff.get('report_log', {}).get(report_id)
            return entry['receipt'] if entry else None

    async def finalize(self, binding_id, evidence):
        async with self.database.begin() as db:
            wake, binding = await self._binding(db, binding_id)
            handoff = dict(wake.handoff)
            if handoff.get('finalize') is not None:
                if handoff['exit_evidence'] != evidence:
                    raise HandoffConflict()
                return handoff['finalize']
            if evidence.get('execution_stopped') is not True or evidence['report_cursor'] != handoff.get('report_cursor', 0):
                raise HandoffConflict('incomplete_reports')
            accepted = {item['observation_id']: item['status'] for entry in handoff.get('report_log', {}).values()
                        if entry['receipt']['disposition'] == 'accepted' for item in entry['items']}
            rows = await db.scalars(select(ObservationRow).where(ObservationRow.id.in_(self._initial_ids(wake))).with_for_update())
            results = []
            for item in rows:
                status = accepted.get(str(item.id), 'unresolved')
                results.append({'observation_id': str(item.id), 'result': status})
                if status == 'unresolved':
                    item.status, item.status_reason = 'failed', 'unresolved'
            if binding.get('protocol_version', 1) == 2:
                appended = await db.scalars(select(ObservationRow).where(ObservationRow.wake_id == wake.id,
                    ObservationRow.id.not_in(self._initial_ids(wake)), ObservationRow.status.in_(('new', 'delivered')))
                    .with_for_update())
                for item in appended:
                    item.wake_id, item.exposed_wake_id, item.status = None, None, 'new'
            result = {'protocol_version': binding.get('protocol_version', 1), 'finalized': True, 'report_cursor': evidence['report_cursor'],
                      'initial_results': sorted(results, key=lambda item: item['observation_id'])}
            wake.status = 'resolved'
            wake.handoff = {**handoff, 'phase': 'stopped', 'stopped': True, 'finalize': result, 'exit_evidence': evidence}
            wake.updated_at = self.clock()
            return result
