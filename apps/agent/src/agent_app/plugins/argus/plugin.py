"""One ordinary plugin supplies scope-fixed observation tools and prompt data."""

import asyncio
from hashlib import sha256
from uuid import uuid4

from agent_app.argus_protocol import HandoffError, canonical_json
from agent_app.contracts.prompt import PromptContributionEvent, PromptSection
from icarus.core.agents import AgentToolCompletedEvent
from icarus.core.contracts.input import InputFinishedEvent, UserInputEvent
from .notifications import watch_availability
from icarus.core.contracts.context import BlackboardContextReadyEvent
from .delivery import ObservationDelivery
from .actions import ActionFacts
from .references import ObservationReferences
from icarus.core.plugin import BasePlugin, handles
from icarus.core.tools import ToolExecutionResult
from .prompt import ARGUS_WORK_GUIDANCE, plain, render_observations
from icarus.core.session import SessionContext
from .config import PluginConfig


class ArgusPlugin(BasePlugin):
    config_schema = PluginConfig

    def __init__(self, plugin_id, *, context, client=None, append_record=None, prompt_fits=None,
                 load_binding=None, session_id=None, load_records=None, availability_signal=None, task_channels=None):
        super().__init__(plugin_id)
        self.context, self.client, self.append_record = context, client, append_record
        self.task_context = context
        self.load_binding, self.session_id = load_binding, session_id
        self.load_records, self.availability_signal = load_records, availability_signal
        self.input_source, self.task_control_source, self.context_source = 'user-input', 'task-control', 'blackboard'
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
        self.references = ObservationReferences(self._append if append_record else None)
        self.delivery = ObservationDelivery(self)
        self._references_loaded = False
        self.task_channels = task_channels
        self._watcher = None
        self._notice_sent = False
        self._context_ready = False
        self._task_finished = False
        self.actions = ActionFacts(self)
        self.history_cursor = None

    @property
    def protocol_version(self):
        # The assembled semantic session has no binding until its first task.
        return self.task_context.metadata.get('protocol_version', 2 if self.load_binding else 1)

    async def _append(self, *, task_id, update_type, payload):
        return await self.append_record(task_id=task_id, update_type=update_type, payload={
            **payload, 'wake_id': self.task_context.metadata['wake_id'], 'binding_id': self.binding['binding_id']})

    async def _restore(self):
        if self.load_records is None:
            return
        rows = await self.load_records({'argus_reference', 'argus_report', 'argus_report_synced',
                                        'argus_report_rejected', 'argus_wait_cursor', 'argus_action'})
        self.actions.restore(rows)
        if not self._references_loaded:
            self.references.restore([row for row in rows if row.update_type == 'argus_reference'
                                     and row.payload.get('kind') != 'read_coverage'])
            self._references_loaded = True
        selected = [row for row in rows if row.payload.get('wake_id') == self.task_context.metadata['wake_id']
                    and row.payload.get('binding_id') == self.binding['binding_id']]
        self.delivery.restore(selected)
        fingerprints = {}
        for row in selected:
            value = row.payload
            if row.update_type == 'argus_report':
                fingerprint = value['fingerprint']
                self._reports[fingerprint] = value['report_id']
                self._report_payloads[fingerprint] = value['items']
                fingerprints[value['report_id']] = fingerprint
            elif row.update_type in {'argus_report_synced', 'argus_report_rejected'}:
                fingerprint = fingerprints.get(value['report_id'])
                if fingerprint is not None:
                    self._receipts[fingerprint] = value
            elif row.update_type == 'argus_wait_cursor':
                self.last_cursor = value['last_returned_cursor']

    def static_prompt_sections(self):
        return (PromptSection('argus-work', 'session', ARGUS_WORK_GUIDANCE),) if self.active else ()

    def build_tools(self):
        from .tools import ArgusTool
        return tuple(ArgusTool(self, operation) for operation in ('read', 'history', 'wait', 'report')) if self.active else ()

    @handles(UserInputEvent)
    async def on_input(self, source, event):
        if source != self.input_source:
            return
        if event.task_id in self._seen_inputs:
            return
        self._seen_inputs.add(event.task_id)
        if not self.active:
            await self.publish(PromptContributionEvent(task_id=event.task_id, status='completed'))
            return
        try:
            if self.load_binding is not None:
                # Invalidate the previous task before any fallible load; missing
                # bindings cannot inherit its tools or required contribution.
                self.task_context = SessionContext(kind='argus_background', scope=self.context.scope)
                self.binding = {}
                try:
                    selected = await self.load_binding(event.task_id)
                except (RuntimeError, KeyError, ValueError):
                    raise HandoffError('execution_mismatch') from None
                if (selected.get('task_id') != event.task_id or selected.get('session_id') != self.session_id
                        or selected.get('scope') != plain(self.context.scope)
                        or not selected.get('wake_id') or not selected.get('binding_id')):
                    raise HandoffError('execution_mismatch')
                self.task_context = SessionContext(kind='argus_background', scope=self.context.scope, metadata=selected)
                self.binding = plain(selected.get('binding', selected))
                self.last_cursor = self.binding.get('initial_cursor')
                self.exposed.clear()
                self.roles.clear()
                self._pending_delivery.clear()
                self._reports.clear()
                self._receipts.clear()
                self._report_payloads.clear()
            await self._stop_watcher()
            self.history_cursor = None
            self._notice_sent = self._context_ready = self._task_finished = False
            self.references.task_id = event.task_id
            self.delivery = ObservationDelivery(self)
            await self._restore()
            if self.protocol_version == 2:
                await self._contribute_v2(event.task_id)
                if self.availability_signal is not None:
                    self._watcher = self.start_background_work(
                        lambda: watch_availability(self, event.task_id), name=f'argus-availability:{event.task_id}')
                return
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
            while items and not self.prompt_fits(render_observations(self.task_context, items, self.last_cursor)):
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
            await self.client.call(self.task_context.metadata['wake_id'], 'deliver', {
                'binding_id': self.binding['binding_id'], 'observation_ids': identifiers,
            })
            for item in items:
                self.exposed.add(item['observation_id'])
                self.roles[item['observation_id']] = item['role']
            section = PromptSection('observations', 'contributor', render_observations(self.task_context, items, self.last_cursor))
            await self.publish(PromptContributionEvent(task_id=event.task_id, status='completed', sections=(section,)))
        except HandoffError as error:
            await self.publish(PromptContributionEvent(task_id=event.task_id, status='failed', error=error.code))

    async def _contribute_v2(self, task_id):
        views: list[dict] = []
        remaining = 20000
        for item in self.binding.get('initial_items', []):
            if item.get('content_restricted'):
                continue
            view = await self.delivery.project(item)
            self.delivery.initial.append((item, view))
            if remaining <= 0:
                continue
            # Prefer whole high-density messages; leave omitted messages pending.
            if views and (len(view['text']) > remaining or not self.prompt_fits(
                    render_observations(self.task_context, views + [view], None))):
                continue
            view['text'] = view['text'][:remaining]
            view['truncated'] |= len(view['text']) < len(item.get('excerpt', ''))
            while view['text'] and not self.prompt_fits(render_observations(self.task_context, views + [view], None)):
                view['text'] = view['text'][:len(view['text']) // 2]
                view['truncated'] = True
            if self.prompt_fits(render_observations(self.task_context, views + [view], None)):
                views.append(view)
                remaining -= len(view['text'])
        if not views:
            raise HandoffError('content_restricted')
        await self.publish(PromptContributionEvent(task_id=task_id, status='completed', sections=(
            PromptSection('observations', 'contributor', render_observations(self.task_context, views, None)),)))

    @handles(BlackboardContextReadyEvent)
    async def on_context_ready(self, source, event):
        if (source != self.context_source or self.protocol_version != 2 or self._task_finished
                or event.task_id != self.task_context.metadata.get('task_id')):
            return
        try:
            await self.delivery.context_ready(event.input_prompt)
        except HandoffError:
            # Never pretend a failed acknowledgement granted report permission.
            self.delivery.initial.clear()
        finally:
            self._context_ready = True
            if self.availability_signal is not None:
                self.availability_signal.set()

    @handles(InputFinishedEvent)
    async def on_finished(self, source, event):
        if source != self.input_source or event.task_id != self.task_context.metadata.get('task_id'):
            return
        self._task_finished = True
        await self._stop_watcher()

    async def _stop_watcher(self):
        watcher, self._watcher = self._watcher, None
        if watcher is not None:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)

    async def execute(self, operation, arguments, *, task_id=None, tool_call_id=None):
        if (not self.active or self._task_finished
                or task_id != self.task_context.metadata.get('task_id')):
            return ToolExecutionResult(False, error='execution_mismatch')
        try:
            if operation == 'report':
                result = await self._report(arguments, task_id)
                if self.protocol_version == 2 and result.success:
                    return ToolExecutionResult(True, output={key: result.output[key] for key in
                        ('disposition', 'report_cursor', 'code') if key in result.output})
                return result
            if self.protocol_version == 2 and operation == 'read':
                arguments = dict(arguments)
                identifier = self.references.resolve(arguments.pop('ref', None))['observation_id']
                arguments['observation_id'] = identifier
            payload = {'binding_id': self.binding['binding_id'], **arguments}
            if self.protocol_version == 2 and operation == 'history':
                payload['cursor'] = self.history_cursor
            if operation == 'wait' and payload.get('cursor') is None:
                payload['cursor'] = self.last_cursor
            result = await self.client.call(self.task_context.metadata['wake_id'], operation, payload)
            if self.protocol_version == 2:
                view = await self.delivery.view(operation, result)
                if tool_call_id is not None:
                    self.delivery.remember((task_id, tool_call_id), operation, payload, result, view)
                return ToolExecutionResult(True, output=view)
            if tool_call_id is not None:
                self._pending_delivery[(task_id, tool_call_id)] = result
            return ToolExecutionResult(True, output=result)
        except HandoffError as error:
            return ToolExecutionResult(False, error=error.code, metadata={'retryable': error.retryable})

    @handles(AgentToolCompletedEvent)
    async def on_tool_completed(self, source, event):
        if (source != self.agent_source or event.task_id != self.task_context.metadata.get('task_id')
                or self._task_finished):
            return
        key = (event.task_id, event.tool_call.id)
        if self.protocol_version == 2:
            pending = self.delivery.pending.get(key)
            if pending is not None and event.tool_call.name != 'argus_' + pending[0]:
                return
            await self.actions.completed(event)
            try:
                confirmed = await self.delivery.confirm(key, event.result)
                if confirmed:
                    self._notice_sent = False
                    if self.availability_signal is not None:
                        self.availability_signal.set()
            except HandoffError:
                pass
            return
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
        if self.protocol_version == 2:
            normalized_items = []
            for item in items:
                if not isinstance(item, dict) or set(item) - {'ref', 'status', 'reason'}:
                    return ToolExecutionResult(False, error='invalid_report')
                ref = item.get('ref')
                if not isinstance(ref, str):
                    return ToolExecutionResult(False, error='unknown_reference')
                identifier = self.references.resolve(ref)['observation_id']
                if identifier not in self.exposed:
                    pending = self.delivery.delivery_pending(identifier)
                    return ToolExecutionResult(False, error='delivery_pending' if pending else 'not_exposed',
                                               metadata={'retryable': pending})
                if self.roles.get(identifier) != 'work':
                    return ToolExecutionResult(False, error='not_reportable')
                normalized_items.append({key: value for key, value in item.items() if key != 'ref'}
                                        | {'observation_id': identifier})
            items = normalized_items
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
                await self._append(task_id=task_id, update_type='argus_report', payload={
                    'report_id': report_id, 'items': normalized, 'fingerprint': fingerprint,
                })
                self._reports[fingerprint] = report_id
                self._report_payloads[fingerprint] = normalized
            for previous_fingerprint, previous_id in self._reports.items():
                if previous_fingerprint == fingerprint:
                    break
                if previous_fingerprint not in self._receipts:
                    previous_receipt = await self.client.call(self.task_context.metadata['wake_id'], 'report', {
                        'binding_id': self.binding['binding_id'], 'report_id': previous_id,
                        'items': self._report_payloads[previous_fingerprint]})
                    previous_type = 'argus_report_synced' if previous_receipt['disposition'] == 'accepted' else 'argus_report_rejected'
                    await self._append(task_id=task_id, update_type=previous_type, payload=previous_receipt)
                    self._receipts[previous_fingerprint] = previous_receipt
            receipt = await self.client.call(self.task_context.metadata['wake_id'], 'report', {
                'binding_id': self.binding['binding_id'], 'report_id': report_id, 'items': normalized,
            })
            update_type = 'argus_report_synced' if receipt['disposition'] == 'accepted' else 'argus_report_rejected'
            await self._append(task_id=task_id, update_type=update_type, payload=receipt)
            self._receipts[fingerprint] = receipt
            return ToolExecutionResult(True, output=receipt)

    async def stop(self):
        self._task_finished = True
        await self._stop_watcher()
        self._pending_delivery.clear()
        # The trusted host owns the injected client and closes it after all sessions.
