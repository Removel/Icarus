"""Session-wide short references; identities and grants remain private to the plugin."""

import asyncio

from agent_app.argus_protocol import HandoffError


class ObservationReferences:
    def __init__(self, append_record=None):
        self.append_record = append_record
        self.task_id = None
        self._references = {}
        self._observations = {}
        self._lock = asyncio.Lock()

    def restore(self, records):
        for row in records:
            value = dict(row.payload)
            ref, identifier = value.get('ref'), value.get('observation_id')
            if (not isinstance(ref, str) or not ref.startswith('#') or not ref[1:].isdigit()
                    or int(ref[1:]) < 1 or ref != f'#{int(ref[1:])}' or not isinstance(identifier, str)
                    or not value.get('original_binding_id') or not value.get('original_wake_id')):
                raise HandoffError('invalid_reference_record')
            if ref in self._references and self._references[ref] != value:
                raise HandoffError('invalid_reference_record')
            if identifier in self._observations and self._observations[identifier] != ref:
                raise HandoffError('invalid_reference_record')
            self._references[ref] = value
            self._observations[identifier] = ref

    def resolve(self, ref: str) -> dict:
        try:
            return dict(self._references[ref])
        except (KeyError, TypeError):
            raise HandoffError('unknown_reference') from None

    async def assign(self, observation_id: str, *, wake_id: str, binding_id: str) -> str:
        async with self._lock:
            if observation_id in self._observations:
                return self._observations[observation_id]
            number = max((int(ref[1:]) for ref in self._references), default=0) + 1
            ref = f'#{number}'
            value = {'ref': ref, 'observation_id': observation_id,
                     'original_wake_id': wake_id, 'original_binding_id': binding_id}
            if self.append_record is not None:
                await self.append_record(task_id=self.task_id, update_type='argus_reference', payload=value)
            self._references[ref] = value
            self._observations[observation_id] = ref
            return ref
