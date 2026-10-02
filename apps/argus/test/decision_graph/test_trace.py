import asyncio
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.strategy.decision_trace import safe_trace


def test_safe_routing_summary_does_not_include_root_input():
    async def silence(value):
        return RouteDecision('silence', 'no_human')
    graph = DecisionGraph((NodeBinding('end', silence, lambda root, _: {'value': root}),), (), 'end', 'end')
    result = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), 'sensitive input'))
    trace = safe_trace(result)
    assert trace['nodes'][0]['result']['action'] == 'silence'
    assert 'sensitive input' not in str(trace)
