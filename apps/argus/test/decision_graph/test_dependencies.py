import asyncio
import pytest
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.decision_graph.compiler import GraphCompiler
from collections import namedtuple
RouteDecision = namedtuple("RouteDecision", "action reason")


@pytest.mark.parametrize('join',['selected','any'])
def test_mandatory_material_dependency_cannot_be_filtered_out(join):
    async def identity(value):return value
    async def broken(value):raise ValueError('material failed')
    graph=DecisionGraph((
        NodeBinding('root',identity,lambda *_:{'value': True}),
        NodeBinding('trigger',identity,lambda *_:{'value': True},requires=('root',)),
        NodeBinding('material',broken,lambda *_:{'value': None},requires=('root',)),
        NodeBinding('end',identity,lambda *_:{'value': RouteDecision('submit','bad')},requires=('material',),join=join),
    ),(Edge('root','trigger'),Edge('root','material'),Edge('trigger','end')),'root','end')
    result=asyncio.run(GraphRunner().run(GraphCompiler().compile(graph),None))
    assert result.status=='failed'
    assert result.executions['end'].status=='blocked'
