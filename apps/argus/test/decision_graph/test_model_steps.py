import asyncio
from typesafe_sdk import Choice, ChoiceAnswer, SystemOneResponse, Usage
from apps.argus.src.clients.jev_client import JevClient
from apps.argus.src.decision_graph.graph import DecisionGraph, NodeBinding, Edge
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.entity.decision import RouteDecision


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
        NodeBinding('first', first, lambda root, _: root),
        NodeBinding('second', second, lambda _, results: results['first'], requires=('first',)),
    ), (Edge('first', 'second'),), 'first', 'second')
    run = asyncio.run(GraphRunner().run(graph, {'content': 'test'}))
    assert run.decision.action == 'submit'
    assert calls == [{'content': 'test'}, {'previous': 'read_history'}]
