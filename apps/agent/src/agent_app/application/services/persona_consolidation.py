"""Application-owned bounded background consolidation of persisted dialogue."""

import asyncio
from collections import OrderedDict
from dataclasses import asdict
from uuid import uuid4

from agent_app.application.errors import SessionNotFoundError
from agent_app.application.services.history_projector import persona_snapshot
from agent_app.contracts.persona import PersonaConsolidationRequest
from agent_app.model_provider.factory import LLMFactory
from agent_app.plugins.persona.config import PersonaConfig
from agent_app.plugins.persona.consolidator import PersonaConsolidator
from agent_app.plugins.persona.judge import create_judge
from agent_app.plugins.persona.store import PersonaStore
from icarus.core.identity import SessionIdentity


class PersonaConsolidationService:
    MAX_TASKS = 2

    def __init__(self, *, config_loader, store, entry, flush_updates, data_dir, logger):
        self.config_loader = config_loader
        self.store = store
        self.entry = entry
        self.flush_updates = flush_updates
        self.data_dir = data_dir
        self.logger = logger
        self.tasks: dict[tuple[str, str, str], tuple[str, asyncio.Task]] = {}
        self.results: OrderedDict[str, dict] = OrderedDict()
        self.stopping = False

    @staticmethod
    def _busy(entry) -> bool:
        return bool(entry.load_task or entry.unload_task or entry.discarding
                    or any(task.lifecycle in {"accepted", "running"} for task in entry.tasks.values())
                    or (entry.runtime is not None and entry.runtime.snapshot().has_work))

    async def request(self, identity: SessionIdentity) -> PersonaConsolidationRequest:
        if not await self.store().session_exists(identity):
            raise SessionNotFoundError(identity.session_id)
        config = self.config_loader()
        settings = PersonaConfig.model_validate(config.runtime.plugin_config.get("persona", {}))
        key = (identity.workspace_key, identity.session_id, settings.persona_id)
        current = self.tasks.get(key)
        if current and not current[1].done():
            return PersonaConsolidationRequest(identity.session_id, "already_running", current[0])
        reason = None
        if self.stopping:
            reason = "stopping"
        elif not settings.evolution_enabled:
            reason = "evolution_disabled"
        elif len(self.tasks) >= self.MAX_TASKS:
            reason = "capacity"
        entry = await self.entry(identity)
        async with entry.mutation_lock:
            if self._busy(entry):
                reason = reason or "busy"
            if entry.persistence_failed:
                reason = "persistence_failed"
            if reason:
                return PersonaConsolidationRequest(identity.session_id, "skipped", reason=reason)
            # Recheck after awaiting the lock: simultaneous requests must share a task.
            current = self.tasks.get(key)
            if current and not current[1].done():
                return PersonaConsolidationRequest(identity.session_id, "already_running", current[0])
            if len(self.tasks) >= self.MAX_TASKS:
                return PersonaConsolidationRequest(identity.session_id, "skipped", reason="capacity")
            if self.stopping:
                return PersonaConsolidationRequest(identity.session_id, "skipped", reason="stopping")
            request_id = uuid4().hex
            task = asyncio.create_task(self._run(identity, settings, config, request_id),
                                       name=f"persona-consolidation-{request_id}")
            self.tasks[key] = request_id, task
            task.add_done_callback(lambda finished: self._finished(key, finished))
            return PersonaConsolidationRequest(identity.session_id, "accepted", request_id)

    def _finished(self, key, task):
        current = self.tasks.get(key)
        if current and current[1] is task:
            self.tasks.pop(key)
        if not task.cancelled():
            task.exception()  # Consume any unexpected callback-visible exception.

    async def _run(self, identity, settings, config, request_id):
        outcome = {"status": "failed", "reason": "consolidation_failed"}
        try:
            async with asyncio.timeout(5):
                await self.flush_updates()
            entry = await self.entry(identity)
            async with entry.mutation_lock:
                if self._busy(entry):
                    outcome = {"status": "skipped", "reason": "busy"}
                    return
                if entry.persistence_failed:
                    raise ValueError("persistence_failed")
                records, cursor = await self.store().read_updates(identity)
                store = PersonaStore(self.data_dir(), settings.persona_id, settings)
                start = store.processed_through(identity)
                if cursor <= start:
                    outcome = {"status": "skipped", "reason": "no_new_messages"}
                    return
                snapshot = persona_snapshot(identity, settings.persona_id, records, start, cursor)
            model_settings = config.get_model(settings.evolution_model_role)
            worker = PersonaConsolidator(store,
                lambda: LLMFactory(config).create_llm(settings.evolution_model_role),
                lambda: create_judge(settings),
                max_input_tokens=model_settings.context_window - max(model_settings.max_tokens, 0))
            outcome = asdict(await worker.consolidate(snapshot))
        except asyncio.CancelledError:
            outcome = {"status": "failed", "reason": "cancelled"}
            raise
        except Exception as error:
            safe = {"history_busy", "input_too_large", "invalid_candidates", "extraction_failed",
                    "judge_unavailable", "persona_changed", "persistence_failed", "overlay_invalid"}
            reason = str(error) if isinstance(error, ValueError) and str(error) in safe else "consolidation_failed"
            outcome = {"status": "skipped" if reason == "history_busy" else "failed", "reason": reason}
        finally:
            self.results[request_id] = outcome
            while len(self.results) > 64:
                self.results.popitem(last=False)
            self.logger.info("Persona consolidation finished: request_id=%s status=%s reason=%s",
                request_id, outcome["status"], outcome.get("reason", ""))

    async def aclose(self):
        self.stopping = True
        tasks = [task for _, task in self.tasks.values()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.tasks.clear()
