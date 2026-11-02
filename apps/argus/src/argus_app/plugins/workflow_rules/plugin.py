"""Channel-owned evaluation and control validation on the public Icarus runtime."""
import asyncio
import logging
from uuid import uuid4
from datetime import datetime, UTC

from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.events import ObservationRecorded, RulePassed
from argus_app.contracts.capabilities import RuleControl, RuleControlRequest, RULE_FUNCTIONS
from argus_app.contracts.models import Observation, Decision, DiscoveryDecision, DomainError
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
        for channel in await self.store.channels():
            if event.source_key in channel.subscriptions and channel.lifecycle in {'active', 'paused'}:
                await self.process_channel(channel.id)
        if self.discovery_enabled:
            await self.discover(await self.store.observation(event.observation_id))

    async def discover(self, event):
        async with self._discovery_lock:
            current = await self.store.observation(event.id)
            state = current.detail.get('discovery', {})
            if state.get('status') in {'created', 'ignored', 'exhausted'}:
                return
            if state.get('next_retry_at', '') > datetime.now(UTC).isoformat():
                return
            owners = [ch for ch in await self.store.channels()
                      if current.source_key in ch.subscriptions and ch.lifecycle in {'active', 'paused'}]
            if len(owners) > 20:
                await self.store.record_discovery(current.id, {'action': 'defer', 'reason': 'candidate_capacity'})
                return
            candidates = [{'id': ch.id, 'title': ch.title[:120], 'goal': ch.goal[:1000],
                           'progress': ch.progress[:1000], 'lifecycle': ch.lifecycle} for ch in owners]
            versions = {ch.id: ch.version for ch in owners}
            context = {'mode': 'discovery', 'source_key': current.source_key, 'candidates': candidates,
                       'subscriptions': {current.source_key: None}, 'progress': ''}
            async def call(name, args):
                request_type = RULE_FUNCTIONS.get(name)
                if request_type is None:
                    raise RuleError('capability_forbidden')
                safe_args = dict(args)
                if name == 'semantic' and safe_args.get('mode') == 'discovery':
                    safe_args['candidates'] = candidates
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
                decision = DiscoveryDecision.model_validate(value).model_dump(exclude_none=True)
                artifact = {}
                if decision['action'] == 'new':
                    identity = self.store.discovered_channel_id(current)
                    artifact = self.rule_files.publish(INITIAL_CHANNEL_RULE, identity)
                channel_id = await self.store.record_discovery(current.id, decision, candidate_versions=versions, **artifact)
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
                from argus_app.rule_sdk.checks import check_source
                if set(payload) != {'source'}:
                    raise RuleError('rule_check_invalid', 'Static check accepts source only')
                report = check_source(payload['source'])
                return await self.store.complete_control(control.id, {
                    'applied': report.passed, **report.model_dump()})
            if control.kind == 'agent.commit_requested':
                if 'rule_hash' in payload or 'rule_path' in payload:
                    raise RuleError('rule_path', 'Supply rule_source, not internal rule identities')
                prepared = None
                if 'rule_source' in payload:
                    self.rule_files.check(payload['rule_source'])
                    if getattr(self, 'safety_review', None) is not None:
                        await self.safety_review.check(payload['rule_source'], channel.goal)
                    prepared = self.rule_files.publish(payload['rule_source'], channel.id)
                return await self.store.apply_control(control.id, prepared_rule=prepared)
            if control.kind in {'agent.result_submitted', 'channel.update_requested', 'agent.finish_requested'}:
                raise RuleError('legacy_control_disabled', 'Use a single business commit')
            return await self.store.apply_control(control.id)
        except (RuleError, DomainError, ValueError, KeyError, TypeError) as exc:
            # Deterministic validation failures get fixed rejections. Infrastructure
            # and transaction errors must escape, leaving the persisted intent pending.
            return await self.store.complete_control(control.id, {'applied': False, 'code': getattr(exc, 'code', 'control_invalid'), 'message': str(exc)[:512]})
