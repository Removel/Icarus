"""Fair current-boot scheduling: one bounded worker per chat, no old-work recovery."""

import asyncio
from datetime import UTC, datetime
import logging

from ..clients.gateway_client import GatewayBusinessError, GatewayTransportError
from ..repositories.observation_repository import ActiveWakeExists, StaleSelection
from ..repositories.handoff_repository import HandoffConflict
from ..schemas.judgement import JudgementOutcome
from ..schemas.observation import SelectedItem, item_text
from ..services.session_routing_service import RoutingUnavailable

logger = logging.getLogger('argus.scheduler')


def merge_recent(recent, history, *, limit):
    """Prefer persisted observations, then fill gaps from source history."""
    if limit <= 0:
        return ()
    by_id = {item.message_id: item for item in recent}
    for item in history:
        by_id.setdefault(item.message_id, item)
    ordered = sorted(by_id.values(), key=lambda item: (item.sent_at, item.message_id))
    return tuple(ordered[-limit:])


class ObservationScheduler:
    def __init__(self, repository, judgement, wakes, *, routing=None, history=None,
                 permission_for=lambda _: 2,
                 quiet_seconds=5.0, max_debounce_seconds=30.0, scan_interval_seconds=1.0,
                 max_concurrent_chats=8, max_items=20, recent_items=10,
                 clock=lambda: datetime.now(UTC)):
        self.repository, self.judgement, self.wakes = repository, judgement, wakes
        self.routing = routing
        self.history = history
        self.permission_for, self.clock = permission_for, clock
        self.quiet, self.max_debounce, self.interval = quiet_seconds, max_debounce_seconds, scan_interval_seconds
        self.capacity, self.max_items, self.recent_items = max_concurrent_chats, max_items, recent_items
        self.workers = {}
        self._changed = asyncio.Event()
        self._cursor = None
        self._closed = False

    def notify(self, chat_id, *, immediate=False):
        self._changed.set()

    async def _recent_context(self, chat_id, *, before, before_sent, limit):
        recent = await self.repository.recent_context(chat_id, before=before, limit=limit)
        if self.history is None:
            return recent
        try:
            fetched = await self.history.recent(chat_id, before=before_sent, limit=limit)
        except Exception as error:
            # A source-history hiccup never blocks judging persisted observations.
            logger.warning('History unavailable (%s)', type(error).__name__)
            return recent
        return merge_recent(recent, fetched, limit=limit)

    async def process_chat(self, chat_id):
        if self.routing is not None:
            return await self._semantic_step(chat_id)
        active = await self.repository.active_wake(chat_id)
        if active is not None:
            await self.wakes.advance(active.id)
            return
        window = await self.repository.pending_window(chat_id)
        if window is None:
            return
        now = self.clock()
        if (not window.has_strong_signal and (now - window.last_observed_at).total_seconds() < self.quiet
                and (now - window.first_observed_at).total_seconds() < self.max_debounce):
            return
        items = await self.repository.latest_open_items(chat_id, limit=self.max_items)
        if not items:
            return
        recent = await self._recent_context(chat_id,
            before=(items[0].observed_at, items[0].id), before_sent=items[0].sent_at,
            limit=self.recent_items)
        choice = await self.judgement.judge(items, permission_level=self.permission_for(chat_id), recent_context=recent)
        try:
            wake = await self.repository.commit_judgement(chat_id, choice)
        except (StaleSelection, ActiveWakeExists):
            return
        if wake is not None:
            await self.wakes.advance(wake.id)

    async def _semantic_step(self, chat_id):
        active = await self.repository.active_wake(chat_id)
        if active is not None:
            await self.wakes.advance(active.id)
        try:
            await self._route_pending(chat_id)
        except (RoutingUnavailable, StaleSelection, ActiveWakeExists):
            # Failed classification never consumes observations; admission still progresses.
            pass
        if await self.repository.active_wake(chat_id) is None:
            waiting = await self.repository.waiting_wakes(chat_id, limit=1)
            if waiting:
                await self.wakes.advance(waiting[0].id)

    async def _route_pending(self, chat_id):
        window = await self.repository.pending_window(chat_id)
        if window is None:
            return
        now = self.clock()
        if (not window.has_strong_signal and (now - window.last_observed_at).total_seconds() < self.quiet
                and (now - window.first_observed_at).total_seconds() < self.max_debounce):
            return
        items = await self.repository.latest_open_items(chat_id, limit=self.max_items, routing_fair=True)
        if not items:
            return
        recent = await self._recent_context(chat_id,
            before=(items[0].observed_at, items[0].id), before_sent=items[0].sent_at,
            limit=self.recent_items)
        # Already-assigned appended work must not be reclassified or silently ignored.
        assigned, unassigned = {}, []
        remaining = self.judgement.max_chars
        for item in items:
            target = item.detail.get('argus_session_id')
            if target is None:
                unassigned.append(item)
            elif self.permission_for(chat_id) >= 1 and self.wakes.policy.allows(item) and not item.is_self:
                text = item_text(item)
                # Keep whole overflow items pending in their existing semantic context.
                if remaining == 0 or (assigned and len(text) > remaining):
                    continue
                excerpt = text[:remaining]
                assigned.setdefault(target, []).append(SelectedItem(item=item, excerpt=excerpt,
                    truncated=len(excerpt) < len(text)))
                remaining -= len(excerpt)
        for target, values in assigned.items():
            await self._commit_group(chat_id, JudgementOutcome(selected=tuple(values), decision='wake',
                reason='assigned_continuation'), {'target_session_id': target, 'target_waiting_wake_id': None})
        if not unassigned:
            return
        outcome = await self.judgement.judge(unassigned, permission_level=self.permission_for(chat_id), recent_context=recent)
        if outcome.decision != 'wake':
            if outcome.reason in ('jev_disabled', 'jev_failed'):
                return
            await self.repository.commit_judgement(chat_id, outcome, protocol_version=2, target_session_id=None)
            return
        if outcome.ignored:
            await self.repository.commit_judgement(chat_id, JudgementOutcome(ignored=outcome.ignored,
                decision='ignore', reason='filtered'), protocol_version=2, target_session_id=None)
        cursor = next(((value.item.decision_detail or {}).get('routing_cursor') for value in outcome.selected
            if (value.item.decision_detail or {}).get('routing_cursor')), None)
        try:
            page = await self.wakes.agent.sessions(self.wakes._scope(chat_id), cursor=cursor, limit=10)
            candidates = list(page['sessions'])
            next_cursor = page['next_cursor']
            if next_cursor is not None:
                second = await self.wakes.agent.sessions(self.wakes._scope(chat_id), cursor=next_cursor, limit=10)
                known = {value['session_id'] for value in candidates}
                candidates.extend(value for value in second['sessions'] if value['session_id'] not in known)
                next_cursor = second['next_cursor']
        except (GatewayBusinessError, GatewayTransportError) as error:
            raise RoutingUnavailable('candidate_query_failed') from error
        explicit = {}
        for value in outcome.selected:
            reply = value.item.detail.get('reply_to')
            if reply is not None:
                target = await self.repository.reply_session(chat_id, reply)
                if target is not None:
                    explicit[str(value.item.id)] = target
        trusted = {value['session_id'] for value in candidates}
        missing = [value.item.id for value in outcome.selected if explicit.get(str(value.item.id)) not in trusted
            and str(value.item.id) in explicit]
        if missing:
            await self.repository.routing_deferred(missing, cursor=next_cursor, reason='reply_target_not_in_bounded_candidates')
            missing = set(map(str, missing))
            outcome = outcome.model_copy(update={'selected': tuple(value for value in outcome.selected
                if str(value.item.id) not in missing)})
            explicit = {identifier: target for identifier, target in explicit.items() if identifier not in missing}
        # A returned candidate is authorized; explicit reply evidence can prioritize it without UUID generation.
        for target in dict.fromkeys(explicit.values()):
            values = tuple(value for value in outcome.selected if explicit.get(str(value.item.id)) == target)
            await self._commit_group(chat_id, outcome.model_copy(update={'selected': values, 'ignored': ()}),
                {'target_session_id': target, 'target_waiting_wake_id': None})
        outcome = outcome.model_copy(update={'selected': tuple(value for value in outcome.selected
            if str(value.item.id) not in explicit)})
        if not outcome.selected:
            return
        for wake in await self.repository.waiting_wakes(chat_id):
            # Waiting IDs are local only and never leave Argus.
            candidates.append({'wake_id': str(wake.id), 'chat_id': chat_id,
                'summary': '\n'.join(value.excerpt for value in wake.payload.items),
                'last_related_at': wake.created_at.isoformat(), 'has_work': True})
        groups = await self.routing.route([value.item for value in outcome.selected], candidates=candidates, recent_context=recent)
        selected = {str(value.item.id): value for value in outcome.selected}
        for group in groups:
            values = tuple(selected[identifier] for identifier in group['observation_ids'])
            await self._commit_group(chat_id, outcome.model_copy(update={'selected': values, 'ignored': (),
                'decision_detail': {**outcome.decision_detail, 'routing_reason': group['reason']}}), group)

    async def _commit_group(self, chat_id, outcome, group):
        target, waiting_id = group['target_session_id'], group['target_waiting_wake_id']
        if target is not None:
            await self.repository.assign_to_session([value.item.id for value in outcome.selected], session_id=target)
            active = await self.repository.active_wake(chat_id)
            binding = active.handoff.get('binding') if active else None
            if binding is not None and binding.get('protocol_version') == 2 and binding['session_id'] == target:
                try:
                    await self.wakes.attach(active, session_id=target, observation_ids=[value.item.id for value in outcome.selected])
                    return
                except HandoffConflict as error:
                    if error.code != 'execution_closed':
                        raise
            # A finalize race retains target and becomes a later batch, never arbitrary latest.
        await self.repository.commit_judgement(chat_id, outcome, protocol_version=2,
            target_session_id=target, target_waiting_wake_id=waiting_id)

    async def _work(self, chat_id):
        try:
            await self.process_chat(chat_id)
        except Exception as error:
            logger.warning('Chat step failed (%s)', type(error).__name__)

    async def scan_once(self):
        for chat, task in tuple(self.workers.items()):
            if task.done():
                task.result()
                del self.workers[chat]
        chats = list(await self.repository.chats_to_scan())
        if self._cursor is not None:
            chats = [chat for chat in chats if chat > self._cursor] + [chat for chat in chats if chat <= self._cursor]
        for chat in chats:
            if len(self.workers) >= self.capacity or self._closed:
                break
            if chat not in self.workers:
                self.workers[chat] = asyncio.create_task(self._work(chat), name='argus:chat')
                self._cursor = chat

    async def run(self):
        while not self._closed:
            self._changed.clear()
            try:
                await self.scan_once()
            except Exception as error:
                logger.warning('Observation scan failed (%s)', type(error).__name__)
            try:
                await asyncio.wait_for(self._changed.wait(), self.interval)
            except TimeoutError:
                pass

    async def aclose(self):
        self._closed = True
        self._changed.set()
        for task in self.workers.values():
            task.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.workers.clear()
