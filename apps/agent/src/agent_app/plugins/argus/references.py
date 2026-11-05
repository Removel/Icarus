"""Durable session aliases. Once exposed, an alias never changes its target."""
import asyncio
import re


class References:
    def __init__(self, bridge):
        self.bridge = bridge
        self._lock = asyncio.Lock()

    async def validate(self, identity, *, message_refs, source_refs):
        messages, sources = await self.ensure(identity, observation_ids=(), source_keys=())
        for exposed, durable in ((message_refs, messages), (source_refs, sources)):
            if any(durable.get(target) != alias for target, alias in exposed.items()):
                raise ValueError('reference_state_corrupt')

    async def ensure(self, identity, *, observation_ids: tuple[str,...], source_keys: tuple[str,...]):
        async with self._lock:
            records, _ = await self.bridge.runtime.session_store.read_updates(identity)
            messages: dict[str, str] = {}
            sources: dict[str, str] = {}
            for record in records:
                if record.type != 'argus.references':
                    continue
                for key, mapping, prefix in [('messages',messages,'m'),('sources',sources,'s')]:
                    for target, alias in record.payload.get(key,{}).items():
                        if not isinstance(alias,str) or not re.fullmatch(prefix+r'[1-9][0-9]*',alias):
                            raise ValueError('reference_state_corrupt')
                        if (target in mapping and mapping[target] != alias or
                                alias in mapping.values() and mapping.get(target) != alias):
                            raise ValueError('reference_state_corrupt')
                        mapping[target] = alias
            # Validate every previously exposed v2 binding against authoritative alias logs.
            for record in records:
                if record.type != 'argus.wake':
                    continue
                binding = record.payload['binding']
                if binding.get('view_version') != 2:
                    continue
                for key, mapping in [('message_refs',messages),('source_refs',sources)]:
                    if any(mapping.get(target) != alias for target,alias in binding.get(key,{}).items()):
                        raise ValueError('reference_state_corrupt')
            added: dict[str, dict[str, str]] = {'messages':{}, 'sources':{}}
            for key, mapping, values, prefix in [('messages',messages,observation_ids,'m'),('sources',sources,source_keys,'s')]:
                next_number = max((int(a[1:]) for a in mapping.values()),default=0) + 1
                for target in values:
                    if target not in mapping:
                        mapping[target] = prefix + str(next_number)
                        next_number += 1
                        added[key][target] = mapping[target]
            if any(added.values()):
                await self.bridge.append(identity,'argus.references',added)
            return messages, sources
