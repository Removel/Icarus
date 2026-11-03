"""Business-only Argus tools, with runtime-owned identities and durable receipts."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field, model_validator

from agent_app.argus_protocol import WireModel, Id
from icarus.core.models.messages import ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult

TOOL_NAMES = ('argus_channel_read', 'argus_read', 'argus_history', 'argus_rule_check', 'argus_commit')


class Empty(WireModel):
    pass


class Read(WireModel):
    observation_id: Id


class History(WireModel):
    limit: Annotated[int, Field(ge=1, le=50)] = 20
    before: Id | None = None
    text: Annotated[str, Field(max_length=4096)] | None = None


class Check(WireModel):
    source: Annotated[str, Field(min_length=1, max_length=32768)]


class Receipt(WireModel):
    ref: Id
    status: Literal['handled', 'no_action']
    reason: Annotated[str, Field(max_length=4096)] | None = None


class Commit(WireModel):
    results: Annotated[list[Receipt], Field(min_length=1, max_length=20)]
    progress: Annotated[str, Field(max_length=8192)] | None = None
    source_keys: Annotated[list[Annotated[str, Field(min_length=1, max_length=512)]], Field(max_length=16)] | None = None
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


MODELS: dict[str, type[WireModel]] = dict(zip(TOOL_NAMES, (Empty, Read, History, Check, Commit)))
KINDS = {'argus_rule_check': 'rule.check_requested', 'argus_commit': 'agent.commit_requested'}
DESCRIPTIONS = {
    'argus_channel_read': 'Read or refresh current Channel goal, progress, version and rule source. Optional when the current snapshot is sufficient.',
    'argus_read': 'Read an observation from the current frozen round using its short reference. Does not acknowledge it.',
    'argus_history': 'Query authorized Channel history as evidence. History is not part of the current round and is never acknowledged by this read.',
    'argus_rule_check': 'Statically compile and security-check a Python file with main(event, channel). Never executes source or calls models; does not activate a rule.',
    'argus_commit': 'Atomically submit every current message result and optional Channel changes/rule source, then close the round. Omitted fields stay unchanged. Only a committed server receipt means success; technical identities and version are supplied automatically.',
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
            refs = {str(i + 1): item['id'] for i, item in enumerate(wake['items'])}
            def resolve(ref):
                value = refs.get(ref, ref)
                if value not in refs.values():
                    raise ValueError('Reference is outside the current frozen round')
                return value
            if self.name == 'argus_read':
                payload['observation_id'] = resolve(payload['observation_id'])
            if self.name == 'argus_commit':
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
                    text = '\n'.join(m.content if isinstance(m.content, str) else '\n'.join(
                        getattr(p, 'text', '') for p in (m.content or ())) for m in task_messages if m.role == 'user')
                    if not binding['delivered'] and binding['prompt'] in text:
                        delivered = await self._callback(client, task_id, binding, 'agent.delivered',
                            {'observation_ids': list(refs.values())}, 'delivered-' + wake['binding_id'])
                        binding['delivered'] = delivered.get('applied') is True
                    if self.name == 'argus_commit' and not binding['delivered']:
                        raise ValueError('Full frozen content is not visible in the final model view')
                    if self.name in KINDS:
                        kind = KINDS[self.name]
                        canonical = json.dumps(payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                        request_id = hashlib.sha256((wake['binding_id'] + kind + canonical).encode()).hexdigest()
                        result = await self._callback(client, task_id, binding, kind, payload, request_id)
                        if result.get('applied') is not True:
                            return ToolExecutionResult(False, output=result, error=str(result.get('code', 'Argus rejected operation')))
                        if self.name == 'argus_commit':
                            if result.get('committed') is not True:
                                raise ValueError('Missing committed business receipt')
                            binding['finished'] = True
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
                    return ToolExecutionResult(True, output=result)
        except Exception as error:
            return ToolExecutionResult(False, error=f'Argus operation failed: {error}')

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
