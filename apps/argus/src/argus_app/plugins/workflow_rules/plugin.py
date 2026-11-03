"""Channel-owned evaluation and control validation on the public Icarus runtime."""
import asyncio
import logging
from uuid import uuid4
from datetime import datetime, UTC

from icarus.core.plugin import BasePlugin, handles
from argus_app.contracts.events import ObservationRecorded, RulePassed
from argus_app.contracts.capabilities import RuleControl, RuleControlRequest, RULE_FUNCTIONS
from argus_app.contracts.models import Observation, Decision, DiscoveryDecision, DomainError
from argus_app.plugins.workflow_rules.discovery import load_discovery_rule, INITIAL_CHANNEL_RULE, candidate_summaries
from argus_app.plugins.rule_capabilities.responses import ok, failure
from argus_app.rule_sdk.engine import RuleError


class WorkflowRules(BasePlugin):
    provided_capabilities = (RuleControl,)

    def __init__(self, plugin_id, store, engine, rule_files, *, discovery_enabled=False,
                 discovery_rule_path=None, discovery_fallback=None):
        super().__init__(plugin_id)
        self.store, self.engine, self.rule_files = store, engine, rule_files
        self.discovery_fallback = discovery_fallback
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
        pending = None
        async with self._discovery_lock:
            current = await self.store.observation(event.id)
            state = current.detail.get('discovery', {})
            if state.get('status') in {'created', 'existing', 'ignored', 'exhausted'}:
                return
            if state.get('status') == 'fallback_pending':
                await self._resume_discovery_fallback(current, state)
                return
            if state.get('next_retry_at', '') > datetime.now(UTC).isoformat():
                return
            owners = [ch for ch in await self.store.channels()
                      if current.source_key in ch.subscriptions and ch.lifecycle in {'active', 'paused'}]
            if len(owners) > 20:
                await self.store.record_discovery(current.id, {'action': 'defer', 'reason': 'candidate_capacity'})
                return
            candidates = candidate_summaries(owners)
            versions = {ch.id: ch.version for ch in owners}
            context = {'mode': 'discovery', 'source_key': current.source_key, 'candidates': candidates,
                       'subscriptions': {current.source_key: None}, 'progress': ''}
            low_confidence = False
            async def call(name, args):
                nonlocal low_confidence
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
                data = response.unwrap().data
                if name == 'semantic' and safe_args.get('mode') == 'discovery':
                    low_confidence = data.get('defer_reason') == 'low_confidence' and data.get('discovery', {}).get('action') == 'defer'
                return data
            try:
                value = await self.engine.run(self.discovery_source, current.model_dump(mode='json'), context, call)
                decision = DiscoveryDecision.model_validate(value).model_dump(exclude_none=True)
                if decision['action'] == 'defer' and low_confidence and self.discovery_fallback is not None:
                    token = await self.store.claim_discovery_fallback(current.id, versions)
                    if token:
                        pending = (current, candidates, token)
                    else:
                        latest = await self.store.observation(current.id)
                        latest_state = latest.detail.get('discovery', {})
                        if latest_state.get('status') == 'fallback_pending':
                            await self._resume_discovery_fallback(latest, latest_state)
                            return
                        if latest_state.get('status') in {'created', 'existing', 'ignored', 'exhausted'}:
                            return
                if pending is None:
                    await self._record_discovery(current, decision, versions)
            except Exception as exc:
                await self.store.record_discovery(current.id, {'error': getattr(exc, 'code', 'discovery_failed')})
                return
        # The durable claim has committed; no transaction or global discovery lock
        # is held while waiting for a general model. RuleEngine limits stay unchanged.
        if pending is not None:
            await self._run_discovery_fallback(*pending)

    async def _record_discovery(self, current, decision, versions=None, fallback_token=None):
        artifact = {}
        if decision['action'] == 'new':
            identity = self.store.discovered_channel_id(current)
            artifact = self.rule_files.publish(INITIAL_CHANNEL_RULE, identity)
        channel_id = await self.store.record_discovery(current.id, decision,
            candidate_versions=versions, fallback_token=fallback_token, **artifact)
        if channel_id:
            await self.publish(RulePassed(channel_id=channel_id))

    async def _resume_discovery_fallback(self, current, state):
        fallback = state['fallback']
        if fallback['status'] == 'started':
            if fallback['deadline_at'] > datetime.now(UTC).isoformat():
                return
            await self.store.finish_discovery_fallback(current.id, fallback['token'], error_code='outcome_unknown')
            fallback = (await self.store.observation(current.id)).detail['discovery']['fallback']
        if fallback['status'] == 'completed':
            await self._record_discovery(current, fallback['decision'], fallback['candidate_versions'], fallback['token'])
        else:
            await self.store.record_discovery(current.id, {'error': fallback.get('error_code', 'unavailable')})

    async def _run_discovery_fallback(self, current, candidates, token):
        try:
            async with asyncio.timeout(20):
                result = await self.discovery_fallback(current.content.get('text', ''), candidates)
            decision = DiscoveryDecision.model_validate(result).model_dump(exclude_none=True)
            if (not decision['reason'].strip() or len(decision['reason']) > 512
                    or (decision['action'] == 'existing' and decision['channel_id'] not in {x['id'] for x in candidates})):
                raise DomainError('invalid_output')
        except asyncio.CancelledError:
            async def persist_cancelled():
                await self.store.finish_discovery_fallback(current.id, token, error_code='cancelled')
                await self.store.record_discovery(current.id, {'error': 'cancelled'})
            await asyncio.shield(persist_cancelled())
            raise
        except Exception as exc:
            code = 'timeout' if isinstance(exc, TimeoutError) else getattr(exc, 'code', 'unavailable')
            if code not in {'timeout', 'invalid_output', 'unavailable'}:
                code = 'invalid_output' if isinstance(exc, ValueError) else 'unavailable'
            await self.store.finish_discovery_fallback(current.id, token, error_code=code)
            await self.store.record_discovery(current.id, {'error': code})
            return
        await self.store.finish_discovery_fallback(current.id, token, decision=decision)
        latest = await self.store.observation(current.id)
        await self._resume_discovery_fallback(latest, latest.detail['discovery'])

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
