import asyncio
from typesafe_sdk import Choice, ChoiceAnswer, SystemOneResponse, Usage
from apps.argus.src.common.clients.jev_client import JevClient
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.decision_graph.compiler import GraphCompiler
from collections import namedtuple
RouteDecision = namedtuple("RouteDecision", "action reason")


def test_dependent_model_step_receives_previous_answer():
    calls = []
    async def answer(state, questions):
        calls.append(state.copy())
        key = next(iter(questions))
        label = 'read_history' if key == 'next' else 'submit'
        return SystemOneResponse(model='fake', usage=Usage(), answers={
            key: ChoiceAnswer(choice=label, confidence=1., probabilities={label: 1.})})
    client = JevClient(answer=answer)
    async def first(value):
        return await client.ask(state=value, questions={'next': Choice(criteria={'read_history': 'read'})})
    async def second(value):
        result = await client.ask(state={'previous': value.answers['next'].choice},
                                 questions={'action': Choice(criteria={'submit': 'start task'})})
        return RouteDecision(result.answers['action'].choice, 'model')
    graph = DecisionGraph((
        NodeBinding('first', first, lambda root, _: {'value': root}),
        NodeBinding('second', second, lambda _, results: {'value': results['first']}, requires=('first',)),
    ), (Edge('first', 'second'),), 'first', 'second')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), {'content': 'test'}))
    assert run.output.action == 'submit'
    assert calls == [{'content': 'test'}, {'previous': 'read_history'}]
