"""Business-only Argus tools, with runtime-owned identities and durable receipts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Annotated, Any, Literal

import httpx
from pydantic import AliasChoices, Field, model_validator

from agent_app.argus_protocol import WireModel, Id
from icarus.core.models.messages import ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult

TOOL_NAMES = ('argus_channel_read', 'argus_read', 'argus_history', 'argus_rule_check', 'argus_commit', 'argus_guide', 'argus_receive')


class Empty(WireModel):
    pass


class ChannelRead(WireModel):
    include_rule: bool = False


class Guide(WireModel):
    topic: Literal['overview', 'rules', 'sources', 'commit'] = 'overview'


class Receive(WireModel):
    timeout_seconds: Annotated[float,Field(ge=0,le=30)] = 0
    max_chars: Annotated[int,Field(ge=256,le=16384)] = 8192
    limit: Annotated[int,Field(ge=1,le=20)] = 20


class Read(WireModel):
    ref: Id = Field(validation_alias=AliasChoices('ref', 'observation_id'))


class History(WireModel):
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    before_ref: Id | None = Field(default=None, validation_alias=AliasChoices('before_ref', 'before'))
    text: Annotated[str, Field(max_length=4096)] | None = None


class Check(WireModel):
    source: Annotated[str, Field(min_length=1, max_length=32768)]


class Receipt(WireModel):
    ref: Id
    status: Literal['handled', 'no_action']
    reason: Annotated[str, Field(max_length=4096)] | None = None


class Commit(WireModel):
    results: Annotated[list[Receipt], Field(min_length=1, max_length=100)]
    progress: Annotated[str, Field(max_length=8192)] | None = None
    source_refs: Annotated[list[Annotated[str, Field(min_length=1, max_length=512)]], Field(max_length=16)] | None = Field(default=None, validation_alias=AliasChoices('source_refs', 'source_keys'))
    rule_source: Annotated[str, Field(max_length=32768)] | None = None
    lifecycle: Literal['active', 'paused', 'completed'] | None = None
    next_session: Literal['reuse', 'new'] | None = None

    @model_validator(mode='after')
    def explicit_values(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError('Omit unchanged fields instead of null')
        if self.rule_source is not None and len(self.rule_source.encode()) > 32768:
            raise ValueError('Rule source exceeds 32768 UTF-8 bytes')
        return self


MODELS: dict[str, type[WireModel]] = dict(zip(TOOL_NAMES, (ChannelRead, Read, History, Check, Commit, Guide, Receive)))
KINDS = {'argus_rule_check': 'rule.check_requested', 'argus_commit': 'agent.commit_requested'}
DESCRIPTIONS = {
    'argus_receive': 'Read later related messages into this running task, optionally waiting up to30 seconds. Claims one immutable batch; large bodies are read as contiguous pages, with offsets managed by code. Call again to continue; reduce max_chars if a page is truncated. Returns full window body and all current work_refs. Reading is not completion: report each claimed work ref in the final argus_commit. Unread messages remain for a later round. No new Agent is started.',
    'argus_channel_read': 'Read or refresh the current matter goal, progress and source regions. Request include_rule only when inspecting or modifying the current rule.',
    'argus_guide': 'Read Argus help by topic: overview, rules, sources, commit. Read-only documentation, not permission or a required step for known tools.',
    'argus_read': 'Read an observation from the current frozen round using its short reference. Does not acknowledge it.',
    'argus_history': 'Query authorized Channel history as evidence. History is not part of the current round and is never acknowledged by this read.',
    'argus_rule_check': 'Statically compile and security-check a Python file with main(event, channel). Never executes source or calls models; does not activate a rule.',
    'argus_commit': 'Report every current work result and optional Channel changes, then close this round. Perform intended actions before committing: if replying, send through the original platform using reply_target and check business success first. Final assistant text is internal, not a platform reply; drafting it is not handled work. Use no_action with a reason when no action is needed; non-reply work needs no send. Omitted fields stay unchanged; code supplies technical identities/version. Only a committed receipt means this report succeeded.',
}


class ArgusTool(BaseTool):
    def __init__(self, plugin, name):
        self.plugin, self.name = plugin, name

    @property
    def definition(self):
        return ToolDefinition(self.name, DESCRIPTIONS[self.name], MODELS[self.name].model_json_schema())

    def invoke(self, arguments, **execution):
        bridge = self.plugin.bridge
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if getattr(bridge, 'loop', None) is not None and bridge.loop.is_running():
                return asyncio.run_coroutine_threadsafe(self.ainvoke(arguments, **execution), bridge.loop).result()
            return asyncio.run(self.ainvoke(arguments, **execution))
        return ToolExecutionResult(False, error='Use ainvoke from an async execution context')

    async def ainvoke(self, arguments, *, task_id=None, run_id=None, step=None, task_messages=()):
        try:
            payload = MODELS[self.name].model_validate(arguments).model_dump(exclude_none=True, exclude_unset=True)
            bridge = self.plugin.bridge
            binding = bridge.bindings.get(task_id)
            channel = self.plugin.task_channels.get(task_id) if task_id else None
            if not binding or channel is None:
                raise ValueError('Inactive or cancelled Argus task binding')
            channel.raise_if_cancelled()
            wake = binding['wake']
            v2 = binding.get('view_version',1) >= 2
            if self.name == 'argus_guide':
                if binding['finished']:
                    raise ValueError('Argus binding is already finished')
                from .guide import guide
                return ToolExecutionResult(True, output=guide(payload.get('topic', 'overview'), binding.get('sdk', wake.get('rule_sdk', {}))))
            if v2 and self.name in {'argus_read','argus_history','argus_commit'}:
                await bridge.references.validate(bridge.identity_for_wake(wake),
                    message_refs=binding['message_refs'], source_refs=binding['source_refs'])
            from .inbox import work_items
            frozen = {item['id'] for item in work_items(binding)}
            refs = ({alias: id for id, alias in binding['message_refs'].items() if id in frozen}
                    if v2 else {str(i + 1): item['id'] for i, item in enumerate(wake['items'])})
            def resolve(ref):
                value = refs.get(ref) if v2 else refs.get(ref, ref)
                if value not in frozen:
                    raise ValueError('Reference is outside the current frozen round')
                return value
            if self.name == 'argus_read':
                payload = {'observation_id': resolve(payload['ref'])}
            if self.name == 'argus_history' and 'before_ref' in payload:
                ref = payload.pop('before_ref')
                if v2:
                    matches = [id for id, alias in binding['message_refs'].items() if alias == ref]
                    if not matches:
                        raise ValueError('Unknown history reference')
                    payload['before'] = matches[0]
                else:
                    payload['before'] = ref
            if self.name == 'argus_commit':
                if 'source_refs' in payload:
                    values = payload.pop('source_refs')
                    if v2:
                        authorized = set(binding.get('authorized_sources', wake.get('channel_snapshot', {}).get('source_keys', [])))
                        reverse = {ref:key for key,ref in binding['source_refs'].items() if key in authorized}
                        if any(ref not in reverse for ref in values):
                            raise ValueError('Unknown source reference')
                        payload['source_keys'] = [reverse[ref] for ref in values]
                    else:
                        payload['source_keys'] = values
                results = []
                for item in payload['results']:
                    item = dict(item)
                    item['observation_id'] = resolve(item.pop('ref'))
                    results.append(item)
                if len(results) != len(refs) or {x['observation_id'] for x in results} != set(refs.values()):
                    raise ValueError('Commit must include every frozen observation exactly once')
                payload['results'] = results
                payload['expected_version'] = binding.get('snapshot_version', wake.get('channel_version'))
            token = os.getenv('ARGUS_AGENT_API_TOKEN', '')
            if not token:
                raise ValueError('ARGUS_AGENT_API_TOKEN is required')
            async with bridge.callback_locks.setdefault(task_id, asyncio.Lock()):
                channel.raise_if_cancelled()
                if binding['finished'] and self.name != 'argus_commit':
                    raise ValueError('Argus binding is already finished')
                async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                    headers={'Authorization': 'Bearer ' + token}) as client:
                    if self.name in {'argus_receive','argus_commit'}:
                        from .inbox import synchronize
                        await synchronize(self,client,task_id,binding,task_messages)
                    if self.name == 'argus_receive':
                        from .inbox import receive
                        result = await receive(self,client,task_id,binding,payload,task_messages)
                        signal = binding.get('availability_signal')
                        if signal is not None:
                            signal.set()
                        return result
                    text = '\n'.join(m.content if isinstance(m.content, str) else '\n'.join(
                        getattr(p, 'text', '') for p in (m.content or ())) for m in task_messages if m.role == 'user')
                    body = binding.get('observation_prompt') if binding.get('view_version',1) >= 3 else binding['prompt']
                    from .presentation import observations_visible
                    if not binding['delivered'] and body and observations_visible(body,text):
                        delivered = await self._callback(client, task_id, binding, 'agent.delivered',
                            {'observation_ids': [item['id'] for item in wake['items']]}, 'delivered-' + wake['binding_id'])
                        binding['delivered'] = delivered.get('applied') is True
                    if self.name == 'argus_commit' and any(not b.get('delivered') for b in binding.get('inbox_batches',[])):
                        raise ValueError('Supplemental batch is not fully visible in the final model view')
                    if self.name == 'argus_commit' and not binding['delivered']:
                        raise ValueError('Full frozen content is not visible in the final model view')
                    if self.name in KINDS:
                        kind = KINDS[self.name]
                        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                        request_id = hashlib.sha256((wake['binding_id'] + kind + canonical).encode()).hexdigest()
                        result = await self._callback(client, task_id, binding, kind, payload, request_id)
                        if result.get('applied') is not True:
                            output = self._receipt_view(result) if v2 else result
                            return ToolExecutionResult(False, output=output, error=str(result.get('code', 'Argus rejected operation')))
                        if self.name == 'argus_commit':
                            if result.get('committed') is not True:
                                raise ValueError('Missing committed business receipt')
                            binding['finished'] = True
                            signal=binding.get('availability_signal')
                            if signal is not None:
                                signal.set()
                    else:
                        params: dict[str, Any]
                        if self.name == 'argus_channel_read':
                            path, params = '/channels/' + wake['channel_id'], {}
                        elif self.name == 'argus_read':
                            path, params = '/observations/' + payload['observation_id'], {}
                        else:
                            path, params = '/channels/' + wake['channel_id'] + '/history', payload
                        response = await client.get(wake['argus_url'] + path, params=params | {'binding_id': wake['binding_id']})
                        response.raise_for_status()
                        result = response.json()
                        if self.name == 'argus_channel_read':
                            binding['snapshot_version'] = result['version']
                            binding['authorized_sources'] = list(result.get('subscriptions', {}))
                            binding['sdk'] = result.get('rule_sdk', wake.get('rule_sdk', {}))
                    if v2:
                        result = await self._present(result, binding, payload)
                    return ToolExecutionResult(True, output=result)
        except Exception as error:
            return ToolExecutionResult(False, error=f'Argus operation failed: {error}')

    @staticmethod
    def _receipt_view(result):
        return {key:result[key] for key in ('applied','committed','passed','code','diagnostics','policy_version') if key in result}

    async def _present(self, result, binding, payload):
        from .presentation import project_observation, source_view
        if self.name in KINDS:
            return self._receipt_view(result)
        bridge, wake = self.plugin.bridge, binding['wake']
        if self.name == 'argus_channel_read':
            value = {key:result[key] for key in ('title','goal','progress','lifecycle') if key in result}
            value['sources'] = [binding['source_refs'][key] for key in result.get('subscriptions', {}) if key in binding['source_refs']]
            if payload.get('include_rule'):
                value['rule_source'] = result.get('rule_source','')
            return value
        rows = result if self.name == 'argus_history' else [result]
        identity = bridge.identity_for_wake(wake)
        messages, sources = await bridge.references.ensure(identity,
            observation_ids=tuple(row['id'] for row in rows), source_keys=tuple(row['source_key'] for row in rows))
        binding['message_refs'], binding['source_refs'] = messages, sources
        view = [project_observation(row, ref=messages[row['id']], role='context' if self.name == 'argus_history' else 'work') |
                {'source':sources[row['source_key']], 'region':source_view(row)} for row in rows]
        return {'messages':view} if self.name == 'argus_history' else view[0]

    async def _callback(self, client, task_id, binding, kind, payload, request_id):
        channel = self.plugin.task_channels.get(task_id)
        if channel is None:
            raise ValueError('Inactive Argus task')
        channel.raise_if_cancelled()
        bridge = self.plugin.bridge
        wake = binding['wake']
        identity = bridge.identity_for_wake(wake)
        body = {'binding_id': wake['binding_id'], 'request_id': request_id, 'kind': kind, 'payload': payload}
        records, _ = await bridge.runtime.session_store.read_updates(identity)
        callbacks, receipts = {}, {}
        for record in records:
            if record.task_id != task_id:
                continue
            if record.type == 'argus.callback':
                old = record.payload['body']
                callbacks[old['request_id']] = old
            if record.type == 'argus.receipt':
                receipts[record.payload['request_id']] = record.payload['result']
        if request_id in callbacks and callbacks[request_id] != body:
            raise ValueError('Callback request ID fingerprint conflict')
        if request_id in receipts:
            return receipts[request_id]
        unresolved = [old for rid, old in callbacks.items() if rid not in receipts and old['kind'] == 'agent.commit_requested']
        if unresolved:
            old = unresolved[0]
            response = await client.get(wake['argus_url'] + '/agent/commits/' + wake['binding_id'] + '/' + old['request_id'])
            response.raise_for_status()
            status = response.json()
            if status['state'] in {'applied', 'rejected'}:
                await bridge.append(identity, 'argus.receipt', {'request_id': old['request_id'], 'result': status['result']}, task_id)
                if old['request_id'] == request_id:
                    return status['result']
                if status['state'] == 'applied':
                    raise ValueError('A different business commit already succeeded')
            elif old['request_id'] != request_id or status['state'] == 'pending':
                raise ValueError('Previous business commit outcome is unknown; reconcile before changing intent')
        if binding.get('finished'):
            raise ValueError('Argus binding is already finished')
        if request_id not in callbacks:
            await bridge.append(identity, 'argus.callback', {'body': body}, task_id)
        # Reconciliation and durable append above may both suspend across cancellation.
        channel.raise_if_cancelled()
        response = await client.post(wake['argus_url'] + '/agent/events', json=body)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or type(result.get('applied')) is not bool:
            raise ValueError('Invalid Argus operation receipt')
        await bridge.append(identity, 'argus.receipt', {'request_id': request_id, 'result': result}, task_id)
        return result
