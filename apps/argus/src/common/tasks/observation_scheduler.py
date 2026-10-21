"""Fair current-boot scheduling: one bounded worker per chat, no old-work recovery."""

import asyncio
from datetime import UTC, datetime
import logging

from ..repositories.observation_repository import ActiveWakeExists, StaleSelection

logger = logging.getLogger('argus.scheduler')


class ObservationScheduler:
    def __init__(self, repository, judgement, wakes, *, permission_for=lambda _: 2,
                 quiet_seconds=5.0, max_debounce_seconds=30.0, scan_interval_seconds=1.0,
                 max_concurrent_chats=8, max_items=20, recent_items=10,
                 clock=lambda: datetime.now(UTC)):
        self.repository, self.judgement, self.wakes = repository, judgement, wakes
        self.permission_for, self.clock = permission_for, clock
        self.quiet, self.max_debounce, self.interval = quiet_seconds, max_debounce_seconds, scan_interval_seconds
        self.capacity, self.max_items, self.recent_items = max_concurrent_chats, max_items, recent_items
        self.workers = {}
        self._changed = asyncio.Event()
        self._cursor = None
        self._closed = False

    def notify(self, chat_id, *, immediate=False):
        self._changed.set()

    async def process_chat(self, chat_id):
        active = await self.repository.active_wake(chat_id)
        if active is not None:
            await self.wakes.advance(active.id)
            return
        window = await self.repository.pending_window(chat_id)
        if window is None:
            return
        now = self.clock()
        if (not window.has_strong_signal and (now - window.last_observed_at).total_seconds() < self.quiet
                and (now - window.first_observed_at).total_seconds() < self.max_debounce):
            return
        items = await self.repository.latest_open_items(chat_id, limit=self.max_items)
        if not items:
            return
        recent = await self.repository.recent_context(chat_id,
            before=(items[0].observed_at, items[0].id), limit=self.recent_items)
        choice = await self.judgement.judge(items, permission_level=self.permission_for(chat_id), recent_context=recent)
        try:
            wake = await self.repository.commit_judgement(chat_id, choice)
        except (StaleSelection, ActiveWakeExists):
            return
        if wake is not None:
            await self.wakes.advance(wake.id)

    async def _work(self, chat_id):
        try:
            await self.process_chat(chat_id)
        except Exception as error:
            logger.warning('Chat step failed (%s)', type(error).__name__)

    async def scan_once(self):
        for chat, task in tuple(self.workers.items()):
            if task.done():
                task.result()
                del self.workers[chat]
        chats = list(await self.repository.chats_to_scan())
        if self._cursor is not None:
            chats = [chat for chat in chats if chat > self._cursor] + [chat for chat in chats if chat <= self._cursor]
        for chat in chats:
            if len(self.workers) >= self.capacity or self._closed:
                break
            if chat not in self.workers:
                self.workers[chat] = asyncio.create_task(self._work(chat), name='argus:chat')
                self._cursor = chat

    async def run(self):
        while not self._closed:
            self._changed.clear()
            try:
                await self.scan_once()
            except Exception as error:
                logger.warning('Observation scan failed (%s)', type(error).__name__)
            try:
                await asyncio.wait_for(self._changed.wait(), self.interval)
            except TimeoutError:
                pass

    async def aclose(self):
        self._closed = True
        self._changed.set()
        for task in self.workers.values():
            task.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        self.workers.clear()
