import pytest
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge


async def identity(value):
    return value


def test_compile_is_explicit_and_keeps_declaration_unchanged():
    from apps.argus.src.decision_graph.compiler import GraphCompiler
    nodes = (NodeBinding('a', identity, lambda *_: {'value': 1}),
             NodeBinding('b', identity, lambda _, r: {'value': r['a']}))
    declaration = DecisionGraph(nodes, (Edge('a', 'b'),), 'a', 'b')
    compiled = GraphCompiler().compile(declaration)
    assert tuple(n.id for n in compiled.ordered_nodes) == ('a', 'b')
    assert compiled.edges['a->b'].id == 'a->b'
    assert declaration.edges[0].id is None
    with pytest.raises(TypeError):
        compiled.nodes['x'] = nodes[0]


def test_cycle_rejected_by_compiler():
    from apps.argus.src.decision_graph.compiler import GraphCompiler
    node = NodeBinding('a', identity, lambda *_: {'value': 1})
    declaration = DecisionGraph((node,), (Edge('a', 'a'),), 'a', 'a')
    with pytest.raises(ValueError):
        GraphCompiler().compile(declaration)


@pytest.mark.parametrize('kind', ['duplicate_node','missing_output','dangling_edge','duplicate_edge',
    'unreachable','edge_node_collision','empty_edge_id','unknown_requires','invalid_timeout'])
def test_invalid_structure_is_rejected_before_execution(kind):
    from apps.argus.src.decision_graph.compiler import GraphCompiler
    nodes = (NodeBinding('a',identity,lambda *_: {'value':1}),
             NodeBinding('b',identity,lambda *_: {'value':2}))
    edges, output = (Edge('a','b'),), 'b'
    if kind == 'duplicate_node':
        nodes += (nodes[0],)
    elif kind == 'missing_output':
        output = 'missing'
    elif kind == 'dangling_edge':
        edges = (Edge('a','missing'),)
    elif kind == 'duplicate_edge':
        edges += edges
    elif kind == 'unreachable':
        edges = ()
    elif kind == 'edge_node_collision':
        edges = (Edge('a','b',id='a'),)
    elif kind == 'empty_edge_id':
        edges = (Edge('a','b',id=''),)
    elif kind == 'unknown_requires':
        nodes = (nodes[0],NodeBinding('b',identity,lambda *_: {'value':2},requires=('missing',)))
    else:
        edges = (Edge('a','b',timeout_seconds=float('nan')),)
    with pytest.raises(ValueError):
        GraphCompiler().compile(DecisionGraph(nodes,edges,'a',output))
