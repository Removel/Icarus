import asyncio
import importlib


def test_fanout_overflow_closes_only_slow_subscriber_and_wakes_waiters():
    async def run():
        api = importlib.import_module("icarus.runtime.fanout")
        fanout = api.BoundedFanout(capacity=1)
        slow, fast = fanout.subscribe(), fanout.subscribe()
        fanout.publish("first")
        assert await fast.next() == "first"
        fanout.publish("second")
        assert slow.closed
        assert not fast.closed
        assert await fast.next() == "second"
        try:
            await slow.next()
        except api.MailboxOverflowError:
            pass
        else:
            raise AssertionError("slow subscription must report overflow")
        waiter = asyncio.create_task(fast.next())
        fanout.close()
        try:
            await waiter
        except RuntimeError:
            pass
        else:
            raise AssertionError("closing must wake the reader")
    asyncio.run(run())
