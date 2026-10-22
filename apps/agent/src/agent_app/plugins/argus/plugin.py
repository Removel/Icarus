"""One ordinary plugin supplies scope-fixed observation tools and prompt data."""

import asyncio
from hashlib import sha256
from uuid import uuid4

from agent_app.argus_protocol import HandoffError, canonical_json
from agent_app.contracts.prompt import PromptContributionEvent, PromptSection
from icarus.core.agents import AgentToolCompletedEvent
from icarus.core.contracts.input import UserInputEvent
from icarus.core.plugin import BasePlugin, handles
from icarus.core.tools import ToolExecutionResult
from .prompt import ARGUS_WORK_GUIDANCE, plain, render_observations
from .config import PluginConfig


class ArgusPlugin(BasePlugin):
    config_schema = PluginConfig

    def __init__(self, plugin_id, *, context, client=None, append_record=None, prompt_fits=None):
        super().__init__(plugin_id)
        self.context, self.client, self.append_record = context, client, append_record
        self.prompt_fits = prompt_fits or (lambda content: len(content) <= 4096)
        self.active = context.kind == 'argus_background'
        self.binding = plain(context.metadata.get('binding', {}))
        self.exposed = set()
        self.roles = {}
        self._seen_inputs = set()
        self._pending_delivery = {}
        self._reports = {}
        self._receipts = {}
        self._report_payloads = {}
        self._report_lock = asyncio.Lock()
        self.last_cursor = self.binding.get('initial_cursor')
        self.agent_source = 'agent'

    def static_prompt_sections(self):
        return (PromptSection('argus-work', 'session', ARGUS_WORK_GUIDANCE),) if self.active else ()

    def build_tools(self):
        from .tools import ArgusTool
        return tuple(ArgusTool(self, operation) for operation in ('read', 'history', 'wait', 'report')) if self.active else ()

    @handles(UserInputEvent)
    async def on_input(self, source, event):
        if event.task_id in self._seen_inputs:
            return
        self._seen_inputs.add(event.task_id)
        if not self.active:
            await self.publish(PromptContributionEvent(task_id=event.task_id, status='completed'))
            return
        try:
            items, remaining = [], 20000
            for item in self.binding.get('initial_items', []):
                if item.get('content_restricted') or remaining <= 0:
                    continue
                value = dict(item)
                text = value.get('excerpt', '')
                value['excerpt'] = text[:remaining]
                value['truncated'] = value.get('truncated', False) or len(text) > remaining
                remaining -= len(value['excerpt'])
                items.append(value)
            if not items:
                raise HandoffError('content_restricted')
            while items and not self.prompt_fits(render_observations(self.context, items, self.last_cursor)):
                longest = max(items, key=lambda value: len(value.get('excerpt', '')))
                text = longest.get('excerpt', '')
                if text:
                    longest['excerpt'] = text[:len(text)//2]
                    longest['truncated'] = True
                else:
                    items.pop()
            if not items:
                raise HandoffError('prompt_too_large')
            identifiers = [item['observation_id'] for item in items]
            await self.client.call(self.context.metadata['wake_id'], 'deliver', {
                'binding_id': self.binding['binding_id'], 'observation_ids': identifiers,
            })
            for item in items:
                self.exposed.add(item['observation_id'])
                self.roles[item['observation_id']] = item['role']
            section = PromptSection('observations', 'contributor', render_observations(self.context, items, self.last_cursor))
            await self.publish(PromptContributionEvent(task_id=event.task_id, status='completed', sections=(section,)))
        except HandoffError as error:
            await self.publish(PromptContributionEvent(task_id=event.task_id, status='failed', error=error.code))

    async def execute(self, operation, arguments, *, task_id=None, tool_call_id=None):
        if not self.active or task_id != self.context.metadata.get('task_id'):
            return ToolExecutionResult(False, error='execution_mismatch')
        try:
            if operation == 'report':
                return await self._report(arguments, task_id)
            payload = {'binding_id': self.binding['binding_id'], **arguments}
            if operation == 'wait' and payload.get('cursor') is None:
                payload['cursor'] = self.last_cursor
            result = await self.client.call(self.context.metadata['wake_id'], operation, payload)
            if tool_call_id is not None:
                self._pending_delivery[(task_id, tool_call_id)] = result
            return ToolExecutionResult(True, output=result)
        except HandoffError as error:
            return ToolExecutionResult(False, error=error.code, metadata={'retryable': error.retryable})

    @handles(AgentToolCompletedEvent)
    async def on_tool_completed(self, source, event):
        if source != self.agent_source:
            return
        key = (event.task_id, event.tool_call.id)
        pending = self._pending_delivery.get(key)
        if pending is None or not event.result.success or event.result.output != pending:
            return
        items = pending.get('items', [pending['item']] if 'item' in pending else [])
        for item in items:
            self.exposed.add(item['observation_id'])
            self.roles[item['observation_id']] = item['role']
        if event.tool_call.name == 'argus_wait':
            self.last_cursor = pending['next_cursor']
            if self.append_record:
                await self.append_record(task_id=event.task_id, update_type='argus_wait_cursor',
                                         payload={'last_returned_cursor': self.last_cursor})
        self._pending_delivery.pop(key, None)

    async def _report(self, arguments, task_id):
        items = arguments.get('items')
        if not isinstance(items, list) or not 1 <= len(items) <= 50:
            return ToolExecutionResult(False, error='invalid_report')
        for item in items:
            if not isinstance(item, dict) or set(item) - {'observation_id', 'status', 'reason'}:
                return ToolExecutionResult(False, error='invalid_report')
            identifier = item.get('observation_id')
            if identifier not in self.exposed:
                return ToolExecutionResult(False, error='delivery_pending')
            if self.roles.get(identifier) != 'work' or item.get('status') not in {'handled', 'no_action'}:
                return ToolExecutionResult(False, error='item_not_reportable')
            if item.get('reason') is not None and (not isinstance(item['reason'], str) or len(item['reason']) > 1000):
                return ToolExecutionResult(False, error='invalid_report')
        if len({item['observation_id'] for item in items}) != len(items):
            return ToolExecutionResult(False, error='invalid_report')
        normalized = sorted(items, key=lambda item: item['observation_id'])
        fingerprint = sha256(canonical_json(normalized).encode()).hexdigest()
        async with self._report_lock:
            if fingerprint in self._receipts:
                return ToolExecutionResult(True, output=self._receipts[fingerprint])
            report_id = self._reports.get(fingerprint)
            if report_id is None:
                report_id = str(uuid4())
                await self.append_record(task_id=task_id, update_type='argus_report', payload={
                    'report_id': report_id, 'items': normalized, 'fingerprint': fingerprint,
                })
                self._reports[fingerprint] = report_id
                self._report_payloads[fingerprint] = normalized
            for previous_fingerprint, previous_id in self._reports.items():
                if previous_fingerprint == fingerprint:
                    break
                if previous_fingerprint not in self._receipts:
                    previous_receipt = await self.client.call(self.context.metadata['wake_id'], 'report', {
                        'binding_id': self.binding['binding_id'], 'report_id': previous_id,
                        'items': self._report_payloads[previous_fingerprint]})
                    previous_type = 'argus_report_synced' if previous_receipt['disposition'] == 'accepted' else 'argus_report_rejected'
                    await self.append_record(task_id=task_id, update_type=previous_type, payload=previous_receipt)
                    self._receipts[previous_fingerprint] = previous_receipt
            receipt = await self.client.call(self.context.metadata['wake_id'], 'report', {
                'binding_id': self.binding['binding_id'], 'report_id': report_id, 'items': normalized,
            })
            update_type = 'argus_report_synced' if receipt['disposition'] == 'accepted' else 'argus_report_rejected'
            await self.append_record(task_id=task_id, update_type=update_type, payload=receipt)
            self._receipts[fingerprint] = receipt
            return ToolExecutionResult(True, output=receipt)

    async def stop(self):
        self._pending_delivery.clear()
        # The trusted host owns the injected client and closes it after all sessions.
