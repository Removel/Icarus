"""Managed service loops for batch execution and timed evidence checks."""
import asyncio


class BatchRuntime:
    def __init__(self, scheduler, service, *, recovery_interval_seconds):
        self.scheduler, self.service = scheduler, service
        self.recovery_interval_seconds = recovery_interval_seconds

    async def run_batches(self):
        while True:
            batch_id = await self.scheduler.ready.get()
            try:
                await self.service.process_batch(batch_id)
            finally:
                self.scheduler.done(batch_id)
            await self.scheduler.replenish()
            self.scheduler.wakeup.set()

    async def run_checks(self):
        while True:
            self.scheduler.wakeup.clear()
            await self.scheduler.check_due()
            try:
                async with asyncio.timeout(self.recovery_interval_seconds):
                    await self.scheduler.wakeup.wait()
            except TimeoutError:
                pass
