"""Binding-scoped tools and durable callback receipts.

Only full current frozen content present in the actual tool execution model view
can be acknowledged. A source key is never used as a lock or routing identity.
"""
from __future__ import annotations
import asyncio
import json
import os
import httpx
from pydantic import Field
from typing import Annotated, Any, Literal
from agent_app.argus_protocol import WireModel, Id
from icarus.core.models.messages import ToolDefinition
from icarus.core.tools import BaseTool, ToolExecutionResult

TOOL_NAMES = ('argus_channel_read','argus_read','argus_history','argus_rule_check','argus_confirm','argus_channel_update','argus_finish')

class Empty(WireModel):
    pass
class Read(WireModel):
    observation_id: Id
class History(WireModel):
    limit: Annotated[int, Field(ge=1, le=100)] = 20
    before: Id | None = None
    text: Annotated[str, Field(max_length=4096)] | None = None
class Write(WireModel):
    request_id: Id
class Check(Write):
    source: Annotated[str, Field(min_length=1, max_length=32768)]
    event_refs: Annotated[list[Id], Field(max_length=20)] = []
class Receipt(WireModel):
    observation_id: Id
    status: Literal['handled','no_action']
    reason: Annotated[str, Field(max_length=4096)] | None = None
class Confirm(Write):
    items: Annotated[list[Receipt], Field(min_length=1, max_length=20)]
class Update(Write):
    expected_version: Annotated[int, Field(ge=1)]
    progress: Annotated[str, Field(max_length=8192)] | None = None
    source_keys: Annotated[list[Id], Field(max_length=16)] | None = None
    rule_hash: Annotated[str, Field(max_length=128)] | None = None
    lifecycle: Literal['active','paused','completed'] | None = None
class Finish(Write):
    update: Literal['committed','unchanged']
MODELS: dict[str, type[WireModel]] = dict(zip(TOOL_NAMES, (Empty,Read,History,Check,Confirm,Update,Finish)))
KINDS = {'argus_rule_check':'rule.check_requested','argus_confirm':'agent.result_submitted',
         'argus_channel_update':'channel.update_requested','argus_finish':'agent.finish_requested'}

class ArgusTool(BaseTool):
    def __init__(self, plugin, name):
        self.plugin, self.name = plugin, name

    @property
    def definition(self):
        return ToolDefinition(self.name, 'Operate only on the current Argus binding. Writes require a stable request_id; success means remote applied, not merely HTTP acceptance.', MODELS[self.name].model_json_schema())

    def invoke(self, arguments, **execution):
        bridge = self.plugin.bridge
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if bridge.loop is not None and bridge.loop.is_running():
                return asyncio.run_coroutine_threadsafe(self.ainvoke(arguments, **execution), bridge.loop).result()
            return asyncio.run(self.ainvoke(arguments, **execution))
        return ToolExecutionResult(False, error='Use ainvoke from an async execution context')

    async def ainvoke(self, arguments, *, task_id=None, run_id=None, step=None, task_messages=()):
        try:
            payload = MODELS[self.name].model_validate(arguments).model_dump(exclude_none=True)
            bridge = self.plugin.bridge
            binding = bridge.bindings.get(task_id)
            channel = self.plugin.task_channels.get(task_id) if task_id else None
            if not binding or binding['finished'] or channel is None:
                raise ValueError('Inactive or cancelled Argus task binding')
            channel.raise_if_cancelled()
            wake = binding['wake']
            refs = {str(i+1):item['id'] for i,item in enumerate(wake['items'])}
            current_ids = set(refs.values())
            def resolve(ref):
                value = refs.get(ref, ref)
                if value not in current_ids:
                    raise ValueError('Reference is outside the current frozen round')
                return value
            if self.name == 'argus_read':
                payload['observation_id'] = resolve(payload['observation_id'])
            if self.name == 'argus_rule_check':
                payload['event_refs'] = [resolve(ref) for ref in payload['event_refs']]
            if self.name == 'argus_confirm':
                for item in payload['items']:
                    item['observation_id'] = resolve(item['observation_id'])
                if len({item['observation_id'] for item in payload['items']}) != len(payload['items']):
                    raise ValueError('Duplicate confirmation IDs')
            token = os.getenv('ARGUS_AGENT_API_TOKEN', '')
            if not token:
                raise ValueError('ARGUS_AGENT_API_TOKEN is required')
            async with bridge.callback_locks.setdefault(task_id, asyncio.Lock()):
                # Revalidate after waiting: a preceding finish/cancel may have
                # revoked this binding while another invocation was queued.
                if binding['finished']:
                    raise ValueError('Argus binding is already finished')
                channel.raise_if_cancelled()
                async with httpx.AsyncClient(timeout=20, follow_redirects=False, trust_env=False,
                    headers={'Authorization':'Bearer ' + token}) as client:
                    # The model executor supplies its final messages, after compaction/hooks.
                    text = '\n'.join(m.content if isinstance(m.content,str) else '\n'.join(getattr(p,'text','') for p in (m.content or ())) for m in task_messages if m.role == 'user')
                    if not binding['delivered'] and binding['prompt'] in text:
                        delivered = await self._callback(client, task_id, binding, 'agent.delivered',
                            {'observation_ids':list(refs.values())}, 'delivered-' + wake['binding_id'])
                        binding['delivered'] = delivered.get('applied') is True
                    if self.name in {'argus_confirm','argus_channel_update','argus_finish'} and not binding['delivered']:
                        raise ValueError('Full frozen content is not visible in the final model view')
                    if self.name in {'argus_channel_update','argus_finish'} and set(binding['confirmed']) != current_ids:
                        raise ValueError('Current frozen round is not fully confirmed')
                    if self.name in KINDS:
                        request_id = payload.pop('request_id')
                        result = await self._callback(client, task_id, binding, KINDS[self.name], payload, request_id)
                        if result.get('applied') is not True:
                            return ToolExecutionResult(False, output=result, error=str(result.get('code','Argus rejected operation')))
                        if self.name == 'argus_confirm':
                            binding['confirmed'] = list(set(binding['confirmed']) | {item['observation_id'] for item in payload['items']})
                        if self.name == 'argus_finish':
                            binding['finished'] = True
                            bridge.bindings.pop(task_id, None)
                            bridge.callback_locks.pop(task_id, None)
                    else:
                        params: dict[str, Any]
                        if self.name == 'argus_channel_read':
                            path, params = '/channels/' + wake['channel_id'], {}
                        elif self.name == 'argus_read':
                            path, params = '/observations/' + payload['observation_id'], {}
                        else:
                            path, params = '/channels/' + wake['channel_id'] + '/history', payload
                        response = await client.get(wake['argus_url'] + path, params=params | {'binding_id':wake['binding_id']})
                        response.raise_for_status()
                        result = response.json()
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
        identity = bridge.identity(wake['channel_id'])
        body = {'binding_id':wake['binding_id'], 'request_id':request_id,'kind':kind,'payload':payload}
        records, _ = await bridge.runtime.session_store.read_updates(identity)
        for record in records:
            # Request IDs belong to one task/binding producer, not its reused
            # conversation. A later round may legitimately reuse the same ID.
            if record.task_id != task_id:
                continue
            if record.type == 'argus.callback' and record.payload['body']['request_id'] == request_id:
                if record.payload['body'] != body:
                    raise ValueError('Callback request ID fingerprint conflict')
            if record.type == 'argus.receipt' and record.payload['request_id'] == request_id:
                return record.payload['result']
        await bridge.append(identity, 'argus.callback', {'body':body}, task_id)
        response = await client.post(wake['argus_url'] + '/agent/events', json=body)
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or type(result.get('applied')) is not bool:
            raise ValueError('Invalid Argus operation receipt')
        await bridge.append(identity, 'argus.receipt', {'request_id':request_id,'result':result}, task_id)
        return result
