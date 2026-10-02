"""Read actual task lifecycles and bounded committed history."""
from datetime import UTC, datetime
import hashlib
import json
from ..clients.icarus_client import GatewayBusinessError
from ..entity.decision import TaskSnapshot
from ..entity.evidence import TaskEvidence

COUNTS = ('queued_task_count', 'pending_event_count', 'pending_plugin_event_count', 'background_work_count')
HISTORY_FIELDS = {
    'user.message': ('text',), 'user.correction': ('text', 'applied_before_step'),
    'task.accepted': ('queue_position',), 'task.started': (), 'task.finished': ('status',),
    'assistant.message': ('text', 'step'), 'tool.started': ('tool_name', 'call_id'),
    'tool.completed': ('tool_name', 'call_id', 'success'),
}


def session_signature(status):
    return (status['workspace_key'], status['session_id'], status['lifecycle'],
            tuple(sorted(status.get('active_task_ids', ()))), *(status.get(k, 0) for k in COUNTS))


class IcarusStateReader:
    def __init__(self, store, batches, client, workspace_path, *, clock=lambda: datetime.now(UTC)):
        self.store, self.batches, self.client, self.workspace_path, self.clock = store, batches, client, workspace_path, clock

    def absent(self):
        return TaskSnapshot(self.workspace_path, None, None, (), 'absent', self.clock())

    async def evaluate(self, *, batch):
        link = await self.store.current_link(batch.platform, batch.account_id, batch.subject)
        if link is None or link.workspace_path != self.workspace_path:
            return self.absent()
        try:
            first = await self.client.session_get(link.workspace_path, link.session_id)
        except GatewayBusinessError as error:
            if error.code == 'session_not_found':
                return self.absent()
            raise
        self._identity(first, link)
        ids = tuple(sorted(first.get('active_task_ids', ())))
        tasks = []
        for task_id in ids:
            try:
                status = await self.client.task_get_status(link.workspace_path, link.session_id, task_id)
            except GatewayBusinessError as error:
                if error.code not in {'task_status_unavailable', 'task_not_found'}:
                    raise
                tasks.append(TaskEvidence(task_id, None, False))
            else:
                if (status.get('task_id') != task_id or status.get('session_id') != link.session_id
                        or status.get('workspace_key') != link.workspace_key):
                    raise ValueError('Task identity mismatch')
                lifecycle = status['lifecycle']
                if lifecycle not in {'accepted','running','completed','failed','cancelled','interrupted'}:
                    raise ValueError('Invalid task lifecycle')
                tasks.append(TaskEvidence(task_id, lifecycle, True))
        # Cursor discovery followed by a bounded tail read; gaps in sequence are legitimate.
        history = await self.client.session_get_history(link.workspace_path, link.session_id,
                                                       after_sequence=0, max_pages=1)
        cursor = history.get('history_cursor', 0)
        if history.get('has_more') and cursor:
            history = await self.client.session_get_history(link.workspace_path, link.session_id,
                                                           after_sequence=max(0, cursor - 200), max_pages=1)
        records, remaining, complete = [], 20000, not history.get('has_more', False)
        for raw in history.get('records', ()):
            if raw.get('workspace_key', link.workspace_key) != link.workspace_key or raw.get('session_id', link.session_id) != link.session_id:
                raise ValueError('History identity mismatch')
            fields = HISTORY_FIELDS.get(raw['type'])
            if fields is None:
                continue
            payload = {}
            for key in fields:
                value = raw.get('payload', {}).get(key)
                if isinstance(value, str):
                    shortened = value[:min(4000, remaining)]
                    complete &= len(shortened) == len(value)
                    remaining -= len(shortened)
                    value = shortened
                if value is not None:
                    payload[key] = value
            records.append(dict(type=raw['type'], task_id=raw.get('task_id'), sequence=raw.get('sequence', 0), payload=payload))
        for task in tasks:
            if task.lifecycle != 'running':
                continue
            deliveries = await self.batches.task_delivery_rows((batch.platform, batch.account_id, batch.subject), task_id=task.task_id)
            request = (deliveries[0].params or {}).get('prompt') if deliveries else None
            if not request:
                continue
            # display_text is not the executed request. Prefer our exact ID-bound payload.
            original = next((r for r in records if r['task_id'] == task.task_id and r['type'] == 'user.message'), None)
            if original is not None:
                remaining += len(original['payload'].get('text', ''))
                original['payload'] = {'text': request[:min(4000, remaining)]}
                original['source'] = 'argus_delivery'
                remaining -= len(original['payload']['text'])
            else:
                excerpt = request[:min(4000, remaining)]
                remaining -= len(excerpt)
                records.append(dict(type='user.message', task_id=task.task_id, sequence=0,
                    source='argus_delivery', payload={'text': excerpt}))
            complete = complete and len(request) <= 4000
        second = await self.client.session_get(link.workspace_path, link.session_id)
        self._identity(second, link)
        return TaskSnapshot(link.workspace_path, link.workspace_key, link.session_id, ids,
            first['lifecycle'], self.clock(), tuple(tasks), *(first.get(k, 0) for k in COUNTS),
            tuple(records), history.get('history_cursor', cursor), complete,
            session_signature(first) == session_signature(second))

    @staticmethod
    def _identity(status, link):
        if status['workspace_key'] != link.workspace_key or status['session_id'] != link.session_id:
            raise ValueError('Workspace/session identity mismatch')


def work_signature(snapshot):
    return dict(workspace_key=snapshot.workspace_key,session_id=snapshot.session_id,
        lifecycle=snapshot.lifecycle,tasks=[(t.task_id,t.lifecycle,t.status_known) for t in snapshot.tasks],
        queued=snapshot.queued_task_count,pending=snapshot.pending_event_count,
        plugin_pending=snapshot.pending_plugin_event_count,background=snapshot.background_work_count,
        history_cursor=snapshot.history_cursor,complete=snapshot.history_complete,stable=snapshot.stable)


def fingerprint(snapshot, human_seq):
    data = dict(work=work_signature(snapshot),human_seq=human_seq)
    return hashlib.sha256(json.dumps(data,sort_keys=True).encode()).hexdigest()

