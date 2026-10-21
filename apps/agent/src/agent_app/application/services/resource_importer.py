"""Resource importer application behavior."""

from __future__ import annotations


import asyncio

from icarus.core.resource_ref import ResourceRef
from agent_app.application.session_runtime import SessionRuntime


class ResourceImporter:
    def __init__(self, *, path_resolver_getter):
        self._path_resolver_getter = path_resolver_getter

    @property
    def _path_resolver(self):
        return self._path_resolver_getter()

    async def _import_resources(
        self, runtime: SessionRuntime, resources: tuple[ResourceRef, ...]
    ):
        if not resources:
            return None
        resolver = self._path_resolver
        if resolver is None:
            raise RuntimeError("AgentRuntime is not started")
        paths = [
            (resource.resolve(resolver.incoming_dir), resource.media_type)
            for resource in resources
        ]
        return await asyncio.to_thread(runtime.import_resources, paths)

