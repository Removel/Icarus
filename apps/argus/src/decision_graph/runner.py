"""Sequential graph execution with explicit branch and failure states."""
import asyncio
from dataclasses import dataclass, asdict
import logging
from time import monotonic
from types import MappingProxyType
from uuid import uuid4

from .graph import DecisionGraph, RepeatBlock
from ..entity.decision import RouteDecision, InterventionResult, TaskRelationResult

logger=logging.getLogger('argus.graph')


@dataclass(frozen=True)
class NodeExecution:
    id: str
    status: str
    output: object = None
    error_type: str | None = None
    duration: float = 0
    iteration: int = 0
    version: str = '1'


@dataclass(frozen=True)
class GraphRun:
    status: str
    decision: RouteDecision
    executions: object
    run_id: str

    def trace(self):
        return [{'run_id': self.run_id, 'node': e.id, 'version': e.version,
                 'status': e.status, 'error_type': e.error_type,
                 'duration': e.duration, 'iteration': e.iteration,
                 'result': (asdict(e.output) if isinstance(e.output, (RouteDecision, InterventionResult, TaskRelationResult))
                            else e.output if isinstance(e.output, bool) else None)}
                for e in self.executions.values()]


class GraphRunner:
    async def run(self, graph, value, *, max_steps=32, timeout_seconds=30):
        if max_steps<1 or timeout_seconds<=0: raise ValueError('Invalid graph budget')
        run_id=uuid4().hex
        records={}
        try:
            async with asyncio.timeout(timeout_seconds):
                result=await self._execute(graph,value,[max_steps],records,run_id)
            if not isinstance(result,RouteDecision): raise ValueError('Missing final route')
            return GraphRun('completed',result,MappingProxyType(records),run_id)
        except Exception as error:
            logger.warning('graph failed run=%s error=%s',run_id,type(error).__name__)
            return GraphRun('failed',RouteDecision('silence','graph_failed'),MappingProxyType(records),run_id)

    async def _execute(self,graph,value,budget,records,run_id,prefix='',iteration=0):
        states={}; outputs={}; selected=set()
        for node in graph.order():
            key=prefix+node.id
            version = getattr(getattr(node.evaluate, '__self__', None), 'version', '1')
            parents={e.source for e in graph.edges if e.target==node.id}
            mandatory=set(node.requires)
            control=({p for p in parents if (p,node.id) in selected}
                     if node.join=='selected' else parents)
            active=node.id==graph.entry or any((p,node.id) in selected for p in parents)
            failed=any(states.get(p) in {'failed','blocked'} for p in mandatory|control)
            successful=[p for p in control if states.get(p)=='succeeded']
            status=None
            if failed: status='blocked'
            elif not active: status='skipped'
            elif any(states.get(p)!='succeeded' for p in mandatory): status='blocked'
            elif node.join in {'all','selected'} and len(successful)!=len(control): status='skipped'
            elif node.join=='any' and len(successful)!=1: status='failed'
            if status:
                states[node.id]=status
                records[key]=NodeExecution(key,status,iteration=iteration,version=version)
                continue
            started=monotonic()
            try:
                if budget[0]<=0: raise RuntimeError('Graph step budget exhausted')
                budget[0]-=1
                data=node.bind_input(value,MappingProxyType(outputs))
                async with asyncio.timeout(node.timeout_seconds):
                    if isinstance(node.evaluate,RepeatBlock):
                        block=node.evaluate
                        for turn in range(block.max_iterations):
                            out=await self._execute(block.body_graph,data,budget,records,run_id,
                                                    f'{key}/{turn}/',turn)
                            if block.until(out): break
                            data=block.next_input(data,out)
                        else: raise RuntimeError('Repeat budget exhausted')
                    else:
                        out=await node.evaluate(data)
                edges=[e for e in graph.edges if e.source==node.id and e.condition(out)]
                selected.update((e.source,e.target) for e in edges)
                outputs[node.id]=out
                states[node.id]='succeeded'
                records[key]=NodeExecution(key,'succeeded',out,duration=monotonic()-started,iteration=iteration,version=version)
            except Exception as error:
                states[node.id]='failed'
                records[key]=NodeExecution(key,'failed',error_type=type(error).__name__,
                                           duration=monotonic()-started,iteration=iteration,version=version)
                logger.warning('node failed run=%s node=%s error=%s',run_id,key,type(error).__name__)
        if states[graph.output]!='succeeded': raise RuntimeError('Final route blocked')
        return outputs[graph.output]
