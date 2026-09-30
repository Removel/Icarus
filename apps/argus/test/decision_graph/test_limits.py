import asyncio
from apps.argus.src.decision_graph.graph import DecisionGraph, NodeBinding, Edge, RepeatBlock
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.entity.decision import RouteDecision


async def identity(value):
    return value


def test_conflicting_any_join_fails_instead_of_last_writer_winning():
    graph = DecisionGraph((
        NodeBinding('root', identity, lambda *_: True),
        NodeBinding('a', identity, lambda *_: RouteDecision('submit', 'a')),
        NodeBinding('b', identity, lambda *_: RouteDecision('silence', 'b')),
        NodeBinding('end', identity, lambda _, results: results['a'], join='any'),
    ), (Edge('root', 'a'), Edge('root', 'b'), Edge('a', 'end'), Edge('b', 'end')), 'root', 'end')
    run = asyncio.run(GraphRunner().run(graph, None))
    assert run.status == 'failed' and run.executions['end'].status == 'failed'


def test_shutdown_cancellation_is_not_silence():
    async def exercise():
        entered = asyncio.Event()
        async def wait(_):
            entered.set()
            await asyncio.Event().wait()
        graph = DecisionGraph((NodeBinding('wait', wait, lambda *_: None),), (), 'wait', 'wait')
        task = asyncio.create_task(GraphRunner().run(graph, None))
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError('Cancellation was swallowed')
    asyncio.run(exercise())


def test_repeat_budget_and_timeout_are_failures():
    body = DecisionGraph((NodeBinding('value', identity, lambda root, _: root + 1),), (), 'value', 'value')
    block = RepeatBlock(body, lambda _, result: result, lambda _: False, 2)
    graph = DecisionGraph((NodeBinding('loop', block, lambda *_: 0),), (), 'loop', 'loop')
    run = asyncio.run(GraphRunner().run(graph, None))
    assert run.status == 'failed'
    assert [run.executions[f'loop/{n}/value'].output for n in (0, 1)] == [1, 2]
    async def wait(_):
        await asyncio.Event().wait()
    graph = DecisionGraph((NodeBinding('wait', wait, lambda *_: None, timeout_seconds=0.01),), (), 'wait', 'wait')
    run = asyncio.run(GraphRunner().run(graph, None))
    assert run.status == 'failed' and run.executions['wait'].error_type == 'TimeoutError'
