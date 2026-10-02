import asyncio
import pytest
from apps.argus.src.decision_graph.graph import DecisionGraph, RepeatBlock
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.entity.decision import RouteDecision


async def identity(value):
    return value


def test_middle_result_does_not_stop_graph():
    async def finish(value):
        assert value is True
        return RouteDecision('submit', 'yes')
    graph = DecisionGraph((NodeBinding('a', identity, lambda root, _: {'value': root}),
        NodeBinding('end', finish, lambda _, r: {'value': r['a']}, requires=('a',))),
        (Edge('a', 'end'),), 'a', 'end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), True))
    assert run.status == 'completed' and run.output.action == 'submit'


def test_conditional_join_skips_unselected_branch():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda root, _: {'value': root}),
        NodeBinding('left', identity, lambda *_: {'value': RouteDecision('silence', 'no')}, requires=('a',)),
        NodeBinding('right', identity, lambda *_: {'value': RouteDecision('submit', 'yes')}, requires=('a',)),
        NodeBinding('end', identity, lambda _, r: {'value': r.get('left', r.get('right'))}, join='any'),
    ), (Edge('a','left',lambda v: not v),Edge('a','right',lambda v: v),Edge('left','end'),Edge('right','end')), 'a','end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), True))
    assert run.output.action == 'submit'
    assert run.executions['left'].status == 'skipped'


def test_failure_blocks_dependent_but_runs_independent_branch(caplog):
    seen=[]
    async def fail(value): raise ValueError('secret input must not be logged')
    async def independent(value): seen.append(True); return 1
    graph=DecisionGraph((NodeBinding('start',identity,lambda *_:{'value': True}),
        NodeBinding('bad',fail,lambda *_:{'value': None},requires=('start',)),
        NodeBinding('ok',independent,lambda *_:{'value': None},requires=('start',)),
        NodeBinding('end',identity,lambda *_:{'value': RouteDecision('submit','unsafe')},requires=('bad','ok'))),
        (Edge('start','bad'),Edge('start','ok'),Edge('bad','end'),Edge('ok','end')), 'start','end')
    run=asyncio.run(GraphRunner().run(GraphCompiler().compile(graph),None))
    assert seen==[True] and run.status=='failed' and run.output is None
    assert run.executions['end'].status=='blocked'
    assert 'secret input' not in caplog.text


def test_invalid_cycle_rejected():
    with pytest.raises(ValueError):
        GraphCompiler().compile(DecisionGraph((NodeBinding('a',identity,lambda *_:{'value': None}),NodeBinding('b',identity,lambda *_:{'value': None})),
                      (Edge('a','b'),Edge('b','a')), 'a','b'))


def test_repeat_is_bounded_and_uses_fresh_round_input():
    async def increment(value): return value+1
    body=DecisionGraph((NodeBinding('inc',increment,lambda root,_:{'value': root}),),(), 'inc','inc')
    repeat=RepeatBlock(body,lambda old,out:out,lambda out:out==2,3)
    graph=DecisionGraph((NodeBinding('loop',repeat,lambda root,_:{'value': root}),
        NodeBinding('end',identity,lambda _,r:{'value': RouteDecision('submit',str(r['loop']))},requires=('loop',))),
        (Edge('loop','end'),), 'loop','end')
    run=asyncio.run(GraphRunner().run(GraphCompiler().compile(graph),0))
    assert run.output.reason=='2'
    exhausted=asyncio.run(GraphRunner().run(GraphCompiler().compile(graph),0,max_steps=2))
    assert exhausted.status=='failed'
