"""Final model-view proof and contiguous body coverage for v2 whole-item delivery."""

from copy import deepcopy
import json

from agent_app.argus_protocol import HandoffError
from .prompt import model_item, plain


class ObservationDelivery:
    def __init__(self, plugin):
        self.plugin = plugin
        self.initial = []
        self.pending = {}
        self.coverage = {}
        self.total = {}
        self.complete = set()
        self.acknowledging = set()
        self.history_page = None

    async def project(self, item, *, text=None):
        plugin = self.plugin
        ref = await plugin.references.assign(item['observation_id'],
            wake_id=plugin.task_context.metadata['wake_id'], binding_id=plugin.binding['binding_id'])
        return model_item(item, ref, plain(plugin.context.scope), text=text)

    async def view(self, operation, wire):
        if operation == 'read':
            return {'item': await self.project(wire['item'], text=''), 'text': wire['text'],
                    'next_text_offset': wire['next_text_offset'], 'truncated': wire['truncated']}
        return {'items': [await self.project(item) for item in wire.get('items', [])],
                'has_more': wire.get('has_more', False), 'truncated': wire.get('truncated', False)}

    def remember(self, key, operation, arguments, wire, view):
        self.pending[key] = (operation, dict(arguments), deepcopy(wire), deepcopy(view))

    def delivery_pending(self, identifier):
        if identifier in self.acknowledging:
            return True
        if any(raw['observation_id'] == identifier and not view['truncated'] for raw, view in self.initial):
            return True
        return any(any(item.get('observation_id') == identifier for item in
                       wire.get('items', [wire['item']] if 'item' in wire else []))
                   for _, _, wire, _ in self.pending.values())

    async def ack(self, items):
        plugin = self.plugin
        work = [item['observation_id'] for item in items
                if item.get('role') == 'work' and item['observation_id'] not in plugin.exposed]
        if work:
            self.acknowledging.update(work)
            try:
                receipt = await plugin.client.call(plugin.task_context.metadata['wake_id'], 'deliver', {
                    'binding_id': plugin.binding['binding_id'], 'observation_ids': work})
                if set(receipt.get('delivered_ids', [])) != set(work):
                    raise HandoffError('protocol_error')
            finally:
                self.acknowledging.difference_update(work)
        for item in items:
            plugin.exposed.add(item['observation_id'])
            plugin.roles[item['observation_id']] = item.get('role', 'context')
            self.complete.add(item['observation_id'])

    async def context_ready(self, input_prompt):
        """Parse only the known Prompt block, never substring-match untrusted bodies."""
        start = '<plugin_context>\n'
        if not input_prompt.startswith(start):
            return
        try:
            blocks, _ = json.JSONDecoder().raw_decode(input_prompt[len(start):])
        except (ValueError, TypeError):
            return
        visible = []
        header = f'## contributor/{self.plugin.plugin_id}/observations\n'
        if not isinstance(blocks, list):
            return
        for block in blocks:
            if not isinstance(block, dict) or block.get('source_plugin_id') != 'prompt' or block.get('context_type') != 'prompt':
                continue
            content = block.get('content', '')
            if not isinstance(content, str):
                continue
            for section in content.split('\n\n## '):
                section = section if section.startswith('## ') else '## ' + section
                if section.startswith(header):
                    try:
                        data = json.loads(section[len(header):])
                        visible.extend(data.get('untrusted_observations', []))
                    except (ValueError, TypeError, AttributeError):
                        continue
        full = [raw for raw, view in self.initial if not raw.get('truncated', False)
                and not view['truncated'] and view in visible]
        await self.ack(full)
        # The final snapshot is definitive: omitted/partial entries are readable,
        # not indefinitely "delivery_pending" just because they were proposed.
        self.initial.clear()

    async def confirm(self, key, result):
        pending = self.pending.get(key)
        if pending is None or not result.success or result.output != pending[3]:
            return False
        operation, arguments, wire, _ = pending
        plugin = self.plugin
        if operation == 'read':
            item = wire['item']
            identifier = item['observation_id']
            start = arguments.get('text_offset', 0)
            end = start + len(wire['text'])
            next_offset = wire['next_text_offset']
            if (type(start) is not int or start < 0 or (next_offset is not None and next_offset != end)
                    or bool(wire['truncated']) != (next_offset is not None)):
                raise HandoffError('protocol_error')
            ranges = self.coverage.setdefault(identifier, [])
            ranges.append((start, end))
            if next_offset is None:
                self.total[identifier] = end
            if plugin.append_record is not None:
                await plugin._append(task_id=key[0], update_type='argus_reference', payload={
                    'kind': 'read_coverage', 'observation_id': identifier, 'start': start, 'end': end,
                    'total': self.total.get(identifier)})
            covered = 0
            for left, right in sorted(ranges):
                if left > covered:
                    break
                covered = max(covered, right)
            if identifier in self.total and covered == self.total[identifier]:
                await self.ack([item])
        else:
            await self.ack([item for item in wire.get('items', []) if not item.get('truncated', False)])
            if operation == 'history' and arguments.get('cursor') == plugin.history_cursor:
                self.history_page = ({item['observation_id'] for item in wire.get('items', [])}, wire['next_cursor'])
            if operation == 'wait' and not any(item.get('truncated', False) for item in wire.get('items', [])):
                plugin.last_cursor = wire['next_cursor']
                if plugin.append_record is not None:
                    await plugin._append(task_id=key[0], update_type='argus_wait_cursor',
                                         payload={'last_returned_cursor': plugin.last_cursor})
        if self.history_page is not None and self.history_page[0] <= self.complete:
            plugin.history_cursor = self.history_page[1]
            self.history_page = None
        self.pending.pop(key, None)
        return True

    def restore(self, rows):
        for row in rows:
            value = row.payload
            if value.get('kind') != 'read_coverage':
                continue
            if (row.task_id != self.plugin.task_context.metadata.get('task_id')
                    or value.get('wake_id') != self.plugin.task_context.metadata['wake_id']
                    or value.get('binding_id') != self.plugin.binding['binding_id']):
                continue
            start, end, total = value.get('start'), value.get('end'), value.get('total')
            if type(start) is not int or type(end) is not int or not 0 <= start <= end:
                raise HandoffError('invalid_reference_record')
            identifier = value['observation_id']
            self.coverage.setdefault(identifier, []).append((start, end))
            if total is not None:
                if type(total) is not int or total < end:
                    raise HandoffError('invalid_reference_record')
                self.total[identifier] = total
