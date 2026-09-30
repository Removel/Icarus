import asyncio
import pytest
from apps.argus.src.decision_graph.graph import DecisionGraph, NodeBinding, Edge, RepeatBlock
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.entity.decision import RouteDecision


async def identity(value):
    return value


def test_middle_result_does_not_stop_graph():
    async def finish(value):
        assert value is True
        return RouteDecision('submit', 'yes')
    graph = DecisionGraph((NodeBinding('a', identity, lambda root, _: root),
        NodeBinding('end', finish, lambda _, r: r['a'], requires=('a',))),
        (Edge('a', 'end'),), 'a', 'end')
    run = asyncio.run(GraphRunner().run(graph, True))
    assert run.status == 'completed' and run.decision.action == 'submit'


def test_conditional_join_skips_unselected_branch():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda root, _: root),
        NodeBinding('left', identity, lambda *_: RouteDecision('silence', 'no'), requires=('a',)),
        NodeBinding('right', identity, lambda *_: RouteDecision('submit', 'yes'), requires=('a',)),
        NodeBinding('end', identity, lambda _, r: r.get('left', r.get('right')), join='any'),
    ), (Edge('a','left',lambda v: not v),Edge('a','right',lambda v: v),Edge('left','end'),Edge('right','end')), 'a','end')
    run = asyncio.run(GraphRunner().run(graph, True))
    assert run.decision.action == 'submit'
    assert run.executions['left'].status == 'skipped'


def test_failure_blocks_dependent_but_runs_independent_branch(caplog):
    seen=[]
    async def fail(_): raise ValueError('secret input must not be logged')
    async def independent(_): seen.append(True); return 1
    graph=DecisionGraph((NodeBinding('start',identity,lambda *_:True),
        NodeBinding('bad',fail,lambda *_:None,requires=('start',)),
        NodeBinding('ok',independent,lambda *_:None,requires=('start',)),
        NodeBinding('end',identity,lambda *_:RouteDecision('submit','unsafe'),requires=('bad','ok'))),
        (Edge('start','bad'),Edge('start','ok'),Edge('bad','end'),Edge('ok','end')), 'start','end')
    run=asyncio.run(GraphRunner().run(graph,None))
    assert seen==[True] and run.status=='failed' and run.decision.action=='silence'
    assert run.executions['end'].status=='blocked'
    assert 'secret input' not in caplog.text


def test_invalid_cycle_rejected():
    with pytest.raises(ValueError):
        DecisionGraph((NodeBinding('a',identity,lambda *_:None),NodeBinding('b',identity,lambda *_:None)),
                      (Edge('a','b'),Edge('b','a')), 'a','b')


def test_repeat_is_bounded_and_uses_fresh_round_input():
    async def increment(value): return value+1
    body=DecisionGraph((NodeBinding('inc',increment,lambda root,_:root),),(), 'inc','inc')
    repeat=RepeatBlock(body,lambda old,out:out,lambda out:out==2,3)
    graph=DecisionGraph((NodeBinding('loop',repeat,lambda root,_:root),
        NodeBinding('end',identity,lambda _,r:RouteDecision('submit',str(r['loop'])),requires=('loop',))),
        (Edge('loop','end'),), 'loop','end')
    run=asyncio.run(GraphRunner().run(graph,0))
    assert run.decision.reason=='2'
    exhausted=asyncio.run(GraphRunner().run(graph,0,max_steps=2))
    assert exhausted.status=='failed'
