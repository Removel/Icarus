import asyncio
import pytest
from apps.argus.src.decision_graph.graph import DecisionGraph, NodeBinding, Edge
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.entity.decision import RouteDecision


@pytest.mark.parametrize('join',['selected','any'])
def test_mandatory_material_dependency_cannot_be_filtered_out(join):
    async def identity(value):return value
    async def broken(_):raise ValueError('material failed')
    graph=DecisionGraph((
        NodeBinding('root',identity,lambda *_:True),
        NodeBinding('trigger',identity,lambda *_:True,requires=('root',)),
        NodeBinding('material',broken,lambda *_:None,requires=('root',)),
        NodeBinding('end',identity,lambda *_:RouteDecision('submit','bad'),requires=('material',),join=join),
    ),(Edge('root','trigger'),Edge('root','material'),Edge('trigger','end')),'root','end')
    result=asyncio.run(GraphRunner().run(graph,None))
    assert result.status=='failed'
    assert result.executions['end'].status=='blocked'
