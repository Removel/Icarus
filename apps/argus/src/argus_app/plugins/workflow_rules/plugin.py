"""Channel-owned evaluation and control validation on the public Icarus runtime."""
import asyncio
import logging
from uuid import uuid4
from datetime import datetime, UTC

from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.events import ObservationRecorded, RulePassed
from argus_app.contracts.capabilities import RuleControl, RuleControlRequest, RULE_FUNCTIONS
from argus_app.contracts.models import Observation, Decision, DomainError
from argus_app.plugins.workflow_rules.discovery import load_discovery_rule, INITIAL_CHANNEL_RULE
from argus_app.plugins.rule_capabilities.responses import ok, failure
from argus_app.rule_sdk.engine import RuleError


class WorkflowRules(BasePlugin):
    provided_capabilities = (RuleControl,)

    def __init__(self, plugin_id, store, engine, rule_files, *, discovery_enabled=False, discovery_rule_path=None):
        super().__init__(plugin_id)
        self.store, self.engine, self.rule_files = store, engine, rule_files
        self.discovery_enabled = discovery_enabled
        self.discovery_source = load_discovery_rule(discovery_rule_path)
        self._discovery_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()

    @handles(ObservationRecorded)
    async def observation_recorded(self, source, event):
        subscribed = False
        for channel in await self.store.channels():
            if event.source_key in channel.subscriptions and channel.lifecycle in {'active', 'paused'}:
                subscribed = True
                await self.process_channel(channel.id)
        if not subscribed and self.discovery_enabled:
            await self.discover(await self.store.observation(event.observation_id))

    async def discover(self, event):
        async with self._discovery_lock:
            current = await self.store.observation(event.id)
            state = current.detail.get('discovery', {})
            if state.get('status') in {'created', 'ignored', 'exhausted'}:
                return
            if state.get('next_retry_at', '') > datetime.now(UTC).isoformat():
                return
            owners = await self.store.channels()
            owned = any(current.source_key in ch.subscriptions and ch.lifecycle in {'active', 'paused'} for ch in owners)
            if owned:
                await self.store.record_discovery(current.id, {'passed': False, 'reason': 'already subscribed'})
                return
            context = {'mode': 'discovery', 'source_key': current.source_key,
                       'subscriptions': {current.source_key: None}, 'progress': ''}
            async def call(name, args):
                request_type = RULE_FUNCTIONS.get(name)
                if request_type is None:
                    raise RuleError('capability_forbidden')
                safe_args = dict(args)
                if name == 'history':
                    sources = safe_args.get('source_keys', [current.source_key])
                    if not isinstance(sources, list) or set(sources) != {current.source_key}:
                        raise RuleError('source_forbidden')
                    safe_args.update(source_keys=[current.source_key], discovery_observation_id=current.id,
                                     source_bounds={current.source_key: current.id})
                response = await self.request(request_type(channel_id='', args=safe_args, timeout_seconds=5))
                return response.unwrap().data
            try:
                value = await self.engine.run(self.discovery_source, current.model_dump(mode='json'), context, call)
                decision = Decision.model_validate(value).model_dump()
                artifact = {}
                if decision['passed']:
                    identity = self.store.discovered_channel_id(current)
                    artifact = self.rule_files.publish(INITIAL_CHANNEL_RULE, identity)
                channel_id = await self.store.record_discovery(current.id, decision, **artifact)
            except Exception as exc:
                await self.store.record_discovery(current.id, {'error': f"{getattr(exc, 'code', 'discovery_failed')}: {exc}"[:512]})
                return
            if channel_id:
                await self.publish(RulePassed(channel_id=channel_id))

    async def scan_once(self):
        # No private event bus: DB replay is the recovery path for missed notices.
        if self.discovery_enabled:
            for event in await self.store.discovery_candidates():
                try:
                    await self.discover(event)
                except Exception:
                    logging.getLogger(__name__).exception('Discovery recovery failed for %s', event.id)
        for control in await self.store.pending_controls():
            try:
                await self._control(control)
            except Exception:
                logging.getLogger(__name__).exception('Control replay failed for %s', control.id)
        for channel in await self.store.channels():
            try:
                await self.process_channel(channel.id)
                current = await self.store.channel(channel.id)
                if any(entry.get('status') == 'passed' and entry.get('channel_version', current.version) == current.version
                       for entry in current.runtime.get('pending', {}).values()):
                    await self.publish(RulePassed(channel_id=current.id))
            except Exception:
                logging.getLogger(__name__).exception('Channel scan failed for %s', channel.id)

    async def _bounds(self, channel):
        bounds = {}
        for key in channel.subscriptions:
            rows = await self.store.observations(source_keys=[key], limit=1)
            bounds[key] = rows[0].id if rows else None
        return bounds

    def _broker(self, channel, bounds):
        async def call(name, args):
            request_type = RULE_FUNCTIONS.get(name)
            if request_type is None:
                raise RuleError('capability_forbidden', f'unknown rule capability: {name}')
            safe_args = dict(args)
            if name == 'history':
                safe_args['source_bounds'] = bounds
            response = await self.request(request_type(channel_id=channel.id, args=safe_args, timeout_seconds=5))
            try:
                return response.unwrap().data
            except Exception as exc:
                raise RuleError(getattr(exc, 'error_code', None) or 'capability_failed', str(exc)) from None
        return call

    async def process_channel(self, channel_id):
        # One bounded scan, not a retry loop. Store owns retry counters/backoff.
        async with self._scan_lock:
            channel = await self.store.channel(channel_id)
            if channel.lifecycle != 'active':
                return
            events = await self.store.unread(channel_id, limit=50)
            if not events:
                return
            bounds = await self._bounds(channel)
            for event in events:
                channel = await self.store.channel(channel_id)
                if channel.lifecycle != 'active':
                    return
                try:
                    source = self.rule_files.read(channel.rule_path, channel.rule_hash)
                    decision = await self.engine.evaluate(source, event, channel, self._broker(channel, bounds))
                    value = decision.model_dump()
                except Exception as exc:
                    value = {'error': f"{getattr(exc, 'code', 'rule_failed')}: {exc}"[:512]}
                try:
                    committed = await self.store.decide(channel_id, event.id, channel.version, value)
                except DomainError as exc:
                    if exc.code != 'pending_capacity':
                        raise
                    # Leave cursor unchanged and let delivery drain this channel.
                    logging.getLogger(__name__).warning('Channel %s is backpressured', channel_id)
                    return
                if not committed:
                    # CAS/config changed: re-read on next scan rather than evaluate a stale batch.
                    return
                if value.get('passed'):
                    await self.publish(RulePassed(channel_id=channel_id))

    @handles(RuleControlRequest)
    async def control_requested(self, source, request):
        try:
            control = await self.store.observation(request.args['observation_id'])
            channel = await self.store.bound_channel(control.detail['binding_id'])
            if channel.id != request.channel_id:
                return failure(request, 'binding_mismatch')
            return ok(request, await self._control(control))
        except Exception as exc:
            return failure(request, getattr(exc, 'code', 'control_failed'), str(exc))

    async def _control(self, control):
        async with self._control_lock:
            # Scheduler replay and directed requests share one execution gate.
            # Re-read after acquiring it: the caller's DTO may predate a receipt.
            current = await self.store.observation(control.id)
            return await self._control_once(current)

    async def _control_once(self, control):
        if 'result' in control.detail:
            return control.detail['result']
        try:
            channel = await self.store.bound_channel(control.detail['binding_id'])
            payload = control.detail.get('payload', control.content)
            if control.kind == 'rule.check_requested':
                source = payload['source']
                self.rule_files.check(source)
                refs = payload.get('event_refs', [])
                if not isinstance(refs, list) or len(refs) > 20:
                    raise RuleError('rule_check_invalid', 'event_refs must contain at most 20 references')
                events = []
                for ref in refs:
                    event = await self.store.observation(ref)
                    if event.kind != 'message.observed' or event.source_key not in channel.subscriptions:
                        raise RuleError('source_forbidden')
                    events.append(event)
                if not events:
                    events = await self.store.observations(source_keys=list(channel.subscriptions), limit=1)
                if not events:
                    events = [Observation(id=str(uuid4()), source_key=next(iter(channel.subscriptions), 'check:synthetic'), message_id='rule-check', kind='message.observed', revision=1, observed_at=datetime.now(UTC).isoformat(), content={'text': ''})]
                bounds = await self._bounds(channel)
                decisions = [await self.engine.evaluate(source, event, channel, self._broker(channel, bounds)) for event in events]
                ref = self.rule_files.publish(source, channel.id)
                return await self.store.complete_control(control.id, {'applied': True, **ref, 'decisions': [decision.model_dump() for decision in decisions]})
            if control.kind == 'channel.update_requested':
                if 'rule_hash' in payload or 'rule_path' in payload:
                    hash = payload.get('rule_hash')
                    path = f'{channel.id}/{hash}.py'
                    if not hash or payload.get('rule_path', path) != path:
                        raise RuleError('rule_path', 'activation requires canonical checked rule path/hash')
                    self.rule_files.read(path, hash)
            return await self.store.apply_control(control.id)
        except Exception as exc:
            # Persist rejected activation/check receipts, so scheduler replay is idempotent.
            return await self.store.complete_control(control.id, {'applied': False, 'code': getattr(exc, 'code', 'control_invalid'), 'message': str(exc)[:512]})
