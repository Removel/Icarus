"""Trusted Argus service, using normal sessions and the existing conversation journal.

A durable claim is written before submission. An interrupted ambiguous claim is
failed closed on restart, never replayed as a second model task.
"""
from __future__ import annotations
import asyncio
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from collections.abc import Callable
from uuid import NAMESPACE_URL, uuid5
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate
from agent_app.argus_protocol import WakeRequest
from agent_app.application.errors import SubmissionNotAdmitted
from .argus_delivery import SubmissionState, submission_state

class BridgeConflict(ValueError):
    pass

class ArgusBridge:
    def __init__(self, runtime, *, workspace_path=None):
        self.runtime = runtime
        self.workspace = Path(workspace_path or os.getenv('ARGUS_WORKSPACE_PATH', str(Path.home() / '.icarus' / 'argus'))).expanduser().resolve()
        self._lock = asyncio.Lock()
        self.callback_locks: dict[str, asyncio.Lock] = {}
        self.loop = None
        self.bindings: dict[str, dict] = {}
        self._live_tasks: set[str] = set()
        self.listeners: dict[str,Callable[[str,str],bool]] = {}
        from .argus_sessions import ArgusSessions
        self.sessions = ArgusSessions(runtime, self.workspace)
        from agent_app.plugins.argus.references import References
        self.references = References(self)

    def identity_for_wake(self, wake):
        return SessionIdentity.create(self.workspace, wake['session_id'])

    async def append(self, identity, kind, payload, task_id=None):
        return await self.runtime.session_store.append_update(identity, RuntimeUpdate(
            workspace_key=identity.workspace_key, session_id=identity.session_id,
            task_id=task_id, type=kind, payload=payload, occurred_at=datetime.now(UTC)))

    async def ensure_session(self, channel_id, request_id, session_key):
        async with self._lock:
            try:
                identity = await self.sessions.select(channel_id, session_key, request_id)
            except ValueError as error:
                raise BridgeConflict(str(error)) from None
            self.runtime.configure_bridge_session(identity.session_id, {'bridge': self})
            return {'session_id': identity.session_id}

    async def _find(self, wake_id):
        store = self.runtime.session_store
        root = SessionIdentity.create(self.workspace)
        for session_id in await store.list_session_ids(root.workspace_key, include_deleted=True):
            if not session_id.startswith('argus-'):
                continue
            identity = SessionIdentity.create(self.workspace, session_id)
            records, _ = await store.read_updates(identity, include_deleted=True)
            for record in records:
                if record.type == 'argus.wake' and record.payload['wake_id'] == wake_id:
                    return identity, record, records
        return None

    async def wake(self, wake: WakeRequest):
        self.loop = asyncio.get_running_loop()
        data = wake.model_dump()
        # New optional fields must not change fingerprints of immutable legacy requests.
        for key in ('window_items','history_items'):
            if not data[key]:
                data.pop(key)
        fingerprint = hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
        async with self._lock:
            existing = await self._find(wake.wake_id)
            if existing:
                if existing[1].payload['fingerprint'] != fingerprint:
                    raise BridgeConflict('wake fingerprint conflict')
                state = self._admission(existing[1], existing[2])
                if (state.phase == 'prepared' or
                        (state.phase == 'not_admitted' and state.retryable and state.attempt < 3)):
                    return await self._submit(existing[0], existing[1], state)
                return await self._status(existing)
            try:
                identity = await self.sessions.require(wake.channel_id, wake.session_key, wake.session_id)
            except ValueError as error:
                raise BridgeConflict(str(error)) from None
            self.runtime.configure_bridge_session(identity.session_id, {'bridge': self})
            records, _ = await self.runtime.session_store.read_updates(identity)
            # Channel business execution is independent of its selected Session.
            # A different Session cannot bypass a previous unresolved execution.
            root = SessionIdentity.create(self.workspace)
            for sid in await self.runtime.session_store.list_session_ids(root.workspace_key, include_deleted=True):
                other_identity = SessionIdentity.create(self.workspace, sid)
                other_records, _ = await self.runtime.session_store.read_updates(other_identity, include_deleted=True)
                for old in other_records:
                    if old.type != 'argus.wake' or old.payload['binding']['wake']['channel_id'] != wake.channel_id:
                        continue
                    status = await self._status((other_identity, old, other_records))
                    if status['commit_state'] != 'applied':
                        raise BridgeConflict('channel has an unresolved execution; reconcile before a new wake')
            task_id = uuid5(NAMESPACE_URL, 'icarus:argus:wake:' + wake.wake_id).hex
            from agent_app.plugins.argus.presentation import render_round, semantic_subject, initial_task_prompt
            current_items = wake.window_items or wake.items
            displayed = current_items + wake.history_items
            messages, sources = await self.references.ensure(identity,
                observation_ids=tuple(item.id for item in displayed),
                source_keys=tuple(dict.fromkeys(wake.channel_snapshot.source_keys + [item.source_key for item in displayed])))
            body = render_round(wake, message_refs=messages, source_refs=sources)
            prompt = initial_task_prompt(wake)
            binding = {'wake':data, 'prompt':prompt, 'observation_prompt':body,
                       'delivered':False, 'confirmed':[], 'finished':False,
                       'admission_version': 1, 'view_version': 3,
                       'message_refs':messages, 'source_refs':sources,
                       'semantic_text':semantic_subject(current_items)}
            claim = await self.append(identity, 'argus.wake', {'wake_id':wake.wake_id,'fingerprint':fingerprint,'binding':binding}, task_id)
            await self.append(identity, 'argus.submission', {'attempt': 0, 'phase': 'prepared'}, task_id)
            return await self._submit(identity, claim, SubmissionState('prepared'))

    def _admission(self, claim, records):
        state = submission_state(tuple(records), task_id=claim.task_id)
        if state.attempt == 0 and not any(r.type == 'argus.submission' and r.task_id == claim.task_id for r in records):
            if claim.payload['binding'].get('admission_version') == 1:
                return SubmissionState('prepared')
        return state

    async def _submit(self, identity, claim, state):
        binding, task_id = dict(claim.payload['binding']), claim.task_id
        wake = binding['wake']
        if binding.get('view_version',1) >= 2:
            await self.references.validate(identity, message_refs=binding['message_refs'], source_refs=binding['source_refs'])
        # Revalidate deletion/selection even on a safe retry after restart.
        await self.sessions.require(wake['channel_id'], wake['session_key'], wake['session_id'])
        self.runtime.configure_bridge_session(identity.session_id, {'bridge': self})
        attempt = state.attempt + 1
        self.bindings[task_id] = binding
        await self.append(identity, 'argus.submission', {'attempt': attempt, 'phase': 'submitting'}, task_id)
        try:
            await self.runtime.submit(self.workspace, identity.session_id, binding['prompt'],
                submission_id=wake['wake_id'], task_id=task_id, bridge=True,
                **({'semantic_text':binding['semantic_text']} if binding.get('semantic_text') is not None else {}))
        except SubmissionNotAdmitted as error:
            await self.append(identity, 'argus.submission', {'attempt': attempt, 'phase': 'not_admitted',
                'retryable': error.retryable, 'error_code': error.code}, task_id)
        except BaseException:
            # The durable submitting record already fences an ambiguous interruption,
            # including cancellation or a failure to write any further evidence.
            self.bindings.pop(task_id, None)
            raise
        else:
            self._live_tasks.add(task_id)
            await self.append(identity, 'argus.submission', {'attempt': attempt, 'phase': 'accepted'}, task_id)
        records, _ = await self.runtime.session_store.read_updates(identity)
        return await self._status((identity, claim, records))

    async def notify(self, wake_id, session_id, binding_id, watermark):
        async with self._lock:
            found=await self._find(wake_id)
            if found is None:
                raise BridgeConflict('Unknown notification wake')
            identity,claim,_=found
            wake=claim.payload['binding']['wake']
            if identity.session_id!=session_id or wake['binding_id']!=binding_id:
                raise BridgeConflict('Notification binding mismatch')
            task_id=claim.task_id
            current=self.bindings.get(task_id)
            listener=self.listeners.get(task_id)
            notified=bool(task_id in self._live_tasks and current and not current.get('finished')
                and listener and listener(task_id,watermark))
            return {'notified':notified}

    async def status(self, wake_id):
        async with self._lock:
            found = await self._find(wake_id)
            if found is None:
                raise KeyError(wake_id)
            return await self._status(found)

    async def _reconcile_commits(self, identity, task_id, records):
        import httpx
        callbacks, receipts, wake = {}, {}, None
        for record in records:
            if record.task_id != task_id:
                continue
            if record.type == 'argus.wake':
                wake = record.payload['binding']['wake']
            elif record.type == 'argus.callback' and record.payload['body']['kind'] == 'agent.commit_requested':
                body = record.payload['body']
                callbacks[body['request_id']] = body
            elif record.type == 'argus.receipt':
                receipts[record.payload['request_id']] = record.payload['result']
        state = 'none'
        for request_id, body in callbacks.items():
            result = receipts.get(request_id)
            if result is not None:
                if result.get('applied') is True and result.get('committed') is True:
                    return 'applied'
                state = 'rejected'
                continue
            token = os.getenv('ARGUS_AGENT_API_TOKEN', '')
            if not token or wake is None:
                return 'unknown'
            try:
                async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False,
                    headers={'Authorization': 'Bearer ' + token}) as client:
                    response = await client.get(wake['argus_url'] + '/agent/commits/' + body['binding_id'] + '/' + request_id)
                    response.raise_for_status()
                    value = response.json()
                remote = value.get('state')
                if remote not in {'applied', 'rejected'}:
                    return 'pending' if remote == 'pending' else 'unknown'
                result = value['result']
                if type(result.get('applied')) is not bool or (remote == 'applied' and result.get('committed') is not True):
                    return 'unknown'
                await self.append(identity, 'argus.receipt', {'request_id': request_id, 'result': result}, task_id)
                if remote == 'applied':
                    return 'applied'
                state = 'rejected'
            except Exception:
                return 'unknown'
        return state

    async def _status(self, found):
        identity, claim, records = found
        task_id = claim.task_id
        status = 'accepted'
        for record in records:
            if record.task_id != task_id:
                continue
            if record.type == 'task.started':
                status = 'running'
            elif record.type == 'task.finished':
                status = 'completed' if record.payload.get('status') == 'completed' else 'failed'
            elif record.type == 'argus.failed':
                status = 'failed'
        if status in {'accepted', 'running'} and task_id not in self._live_tasks:
            status = 'failed'  # Never restart arbitrary actions after an ambiguous interruption.
        commit_state = await self._reconcile_commits(identity, task_id, records) if status in {'completed', 'failed'} else 'none'
        if status in {'completed', 'failed'} and commit_state not in {'pending', 'unknown'}:
            self._live_tasks.discard(task_id)
            self.bindings.pop(task_id, None)
            self.callback_locks.pop(task_id, None)
        admission = self._admission(claim, records)
        if admission.phase in {'prepared', 'not_admitted'}:
            status = 'failed'
        retryable = admission.phase == 'prepared' or (
            admission.phase == 'not_admitted' and admission.retryable and admission.attempt < 3)
        return {'task_id': task_id, 'session_id': identity.session_id, 'status': status,
                'commit_state': commit_state, 'replay_safe': False,
                'admission_state': admission.phase, 'attempt': admission.attempt,
                'retryable': retryable, 'error_code': admission.error_code}
