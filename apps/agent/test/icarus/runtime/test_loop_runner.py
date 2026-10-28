import asyncio
from icarus.runtime import loop_runner


def test_sync_bridge_runs_on_host_loop_and_rejects_same_thread():
    async def run():
        assert hasattr(loop_runner, "RuntimeLoopRunner"), "host loop bridge is missing"
        runner = loop_runner.RuntimeLoopRunner()
        runner.bind()
        host_loop = asyncio.get_running_loop()
        async def operation():
            assert asyncio.get_running_loop() is host_loop
            return "done"
        assert await asyncio.to_thread(runner.run, operation) == "done"
        try:
            runner.run(operation)
        except RuntimeError as error:
            assert "ainvoke" in str(error)
        else:
            raise AssertionError("same-thread sync call would deadlock")
        runner.close()
    asyncio.run(run())
