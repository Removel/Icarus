import asyncio
import json
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.strategy.decision_trace import safe_trace


def test_unknown_reason_and_material_text_do_not_leak():
    async def expose():
        return RouteDecision('silence','DO_NOT_EXPOSE_SECRET')
    graph=DecisionGraph((NodeBinding('end',expose,lambda *_:{}),),(),'end','end')
    trace=safe_trace(asyncio.run(GraphRunner().run(GraphCompiler().compile(graph),None)))
    assert 'DO_NOT_EXPOSE_SECRET' not in json.dumps(trace)
    assert trace['nodes'][0]['result']['reason']=='other'


def test_edge_trace_never_exposes_collect_output():
    from apps.argus.src.decision_graph.edge import Edge, EdgeAction
    async def identity(value):
        return value
    declaration = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': True}),
        NodeBinding('b', identity, lambda *_: {'value': True}),
    ), (Edge('a', 'b', collect=EdgeAction(identity, lambda *_: {'value': 'sensitive-chat'})),), 'a', 'b')
    trace = safe_trace(asyncio.run(GraphRunner().run(GraphCompiler().compile(declaration), None)))
    assert [item['id'] for item in trace['nodes']] == ['a', 'b']
    assert trace['edges'][0]['id'] == 'a->b'
    assert 'result' not in trace['edges'][0]
    assert 'sensitive-chat' not in json.dumps(trace)
