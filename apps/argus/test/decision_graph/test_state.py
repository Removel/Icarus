import asyncio
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.decision_graph.compiler import GraphCompiler


def test_plain_outputs_keyword_inputs_and_independent_runs():
    async def exercise():
        async def increment(*, number):
            return number + 1
        graph = DecisionGraph((NodeBinding('end', increment, lambda root, _: {'number': root}),), (), 'end', 'end')
        runner = GraphRunner()
        first = await runner.run(GraphCompiler().compile(graph), 1)
        second = await runner.run(GraphCompiler().compile(graph), 9)
        assert first.status == second.status == 'completed'
        assert first.output == 2 and second.output == 10
        assert first.run_id != second.run_id
        assert first.executions is not second.executions
    asyncio.run(exercise())


def test_none_is_a_successful_output():
    async def nothing():
        return None
    graph = DecisionGraph((NodeBinding('end', nothing, lambda *_: {}),), (), 'end', 'end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.status == 'completed'
    assert run.output is None
