"""Application-owned, durable Channel-to-many-Session selection."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
from typing import cast

from agent_app.application.errors import SessionAlreadyExistsError
from icarus.core.identity import SessionIdentity
from icarus.core.runtime_update import RuntimeUpdate, RuntimeUpdateType


class ArgusSessions:
    def __init__(self, runtime, workspace: Path):
        self.runtime = runtime
        self.workspace = Path(workspace).expanduser().resolve()
        self._lock = asyncio.Lock()

    async def select(self, channel_id: str, session_key: str, request_id: str) -> SessionIdentity:
        if any(not isinstance(x, str) or not x.strip() or len(x) > 128 for x in (channel_id, session_key, request_id)):
            raise ValueError('invalid_session_selection')
        async with self._lock:
            store = self.runtime.session_store
            reserved = SessionIdentity.create(self.workspace, 'argus-' + uuid5(NAMESPACE_URL,
                'icarus:argus:selection:' + channel_id + ':' + session_key).hex)
            link = await store.get_argus_session_link(reserved.workspace_key, channel_id, session_key)
            if link is None:
                legacy = None
                if not await store.has_argus_session_links(reserved.workspace_key, channel_id):
                    candidates = []
                    for sid in await store.list_session_ids(reserved.workspace_key):
                        if not sid.startswith('argus-'):
                            continue
                        identity = SessionIdentity.create(self.workspace, sid)
                        records, _ = await store.read_updates(identity)
                        if any(r.type == 'argus.session' and r.payload.get('channel_id') == channel_id
                               and 'session_key' not in r.payload for r in records):
                            candidates.append(identity)
                            # An old uncommitted execution must be reconciled before adoption.
                            for wake in [r for r in records if r.type == 'argus.wake']:
                                if not any(r.type == 'argus.receipt' and r.task_id == wake.task_id
                                           and r.payload.get('result', {}).get('committed') is True for r in records):
                                    raise ValueError('legacy_session_requires_reconciliation')
                    if len(candidates) > 1:
                        raise ValueError('legacy_session_ambiguous')
                    legacy = candidates[0] if candidates else None
                identity = await store.reserve_argus_session_link(reserved, channel_id, session_key,
                                                                   request_id, legacy_identity=legacy)
                link = await store.get_argus_session_link(reserved.workspace_key, channel_id, session_key)
                assert link is not None
            identity, status = link
            record = await store.get_session(identity, include_deleted=True)
            if (record is not None and record.deleted_at is not None) or (record is None and status == 'ready'):
                raise ValueError('session_selection_deleted')
            if status != 'ready':
                self.workspace.mkdir(parents=True, exist_ok=True)
                if record is None:
                    try:
                        await self.runtime.create_session(self.workspace, identity.session_id, load_runtime=False, bridge=True)
                    except SessionAlreadyExistsError:
                        pass
                records, _ = await store.read_updates(identity)
                if not any(r.type == 'argus.session' and r.payload.get('channel_id') == channel_id
                           and r.payload.get('session_key') == session_key for r in records):
                    await store.append_update(identity, RuntimeUpdate(workspace_key=identity.workspace_key,
                        session_id=identity.session_id, task_id=None, type=cast(RuntimeUpdateType, 'argus.session'),
                        payload={'channel_id': channel_id, 'session_key': session_key, 'request_id': request_id},
                        occurred_at=datetime.now(UTC)))
                await store.complete_argus_session_link(identity, channel_id, session_key)
            return identity

    async def require(self, channel_id: str, session_key: str, session_id: str) -> SessionIdentity:
        root = SessionIdentity.create(self.workspace)
        link = await self.runtime.session_store.get_argus_session_link(root.workspace_key, channel_id, session_key)
        if link is None or link[1] != 'ready' or link[0].session_id != session_id:
            raise ValueError('session/channel target mismatch')
        if not await self.runtime.session_store.session_exists(link[0]):
            raise ValueError('session_selection_deleted')
        return link[0]
