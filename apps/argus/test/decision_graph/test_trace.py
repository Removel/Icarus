import asyncio
from apps.argus.src.decision_graph.graph import DecisionGraph, NodeBinding
from apps.argus.src.decision_graph.runner import GraphRunner
from apps.argus.src.entity.decision import RouteDecision
from apps.argus.src.strategy.base_policy import BasePolicy


class SilentPolicy(BasePolicy):
    name, version = 'quiet', '7'
    async def evaluate(self, value):
        return RouteDecision('silence', 'no_human')


def test_trace_records_policy_version_and_safe_routing_result():
    graph = DecisionGraph((NodeBinding('end', SilentPolicy().evaluate, lambda root, _: root),), (), 'end', 'end')
    result = asyncio.run(GraphRunner().run(graph, 'sensitive input'))
    trace = result.trace()[0]
    assert trace['version'] == '7'
    assert trace['result'] == {'action': 'silence', 'reason': 'no_human', 'target_task_id': None}
    assert 'sensitive input' not in str(result.trace())
