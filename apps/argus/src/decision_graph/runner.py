"""Sequential units, isolated edge failures, and bounded nested repeats."""
import asyncio
import logging
from math import isfinite
from time import monotonic
from types import MappingProxyType
from uuid import uuid4
from .compiler import CompiledGraph
from .graph import RepeatBlock
from .state import ExecutionBudget, ExecutionState, GraphRun, UnitExecution

logger = logging.getLogger('argus.graph')


class GraphRunner:
    async def run(self, graph: CompiledGraph, value, *, max_steps=32, timeout_seconds=30):
        if not isinstance(graph, CompiledGraph):
            raise TypeError('Graph must be compiled')
        if type(max_steps) is not int or max_steps < 1 or not isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError('Invalid graph budget')
        run_id = uuid4().hex
        records, selected = {}, set()
        try:
            async with asyncio.timeout(timeout_seconds):
                result = await self._execute(graph, value, ExecutionBudget(max_steps), records, selected, run_id)
            return GraphRun('completed', result, MappingProxyType(records), tuple(sorted(selected)), run_id)
        except Exception as error:
            logger.warning('graph failed run=%s error=%s', run_id, type(error).__name__)
            return GraphRun('failed', None, MappingProxyType(records), tuple(sorted(selected)), run_id, type(error).__name__)

    @staticmethod
    def _charge(budget):
        if budget.remaining <= 0:
            raise RuntimeError('Graph step budget exhausted')
        budget.remaining -= 1

    async def _execute(self, graph, value, budget, records, selected, run_id, prefix='', iteration=0):
        state = ExecutionState()
        for node in graph.ordered_nodes:
            key = prefix + node.id
            incoming = graph.incoming[node.id]
            parents = {edge.source for edge in incoming}
            mandatory = set(node.requires)
            control = ({p for p in parents if (p, node.id) in state.selected}
                       if node.join == 'selected' else parents)
            active = node.id == graph.entry or any((p, node.id) in state.selected for p in parents)
            failed = (any(state.states.get(p) in {'failed', 'blocked'} for p in mandatory | control)
                      or any(state.states.get(edge.id) == 'failed' for edge in incoming))
            successful = [p for p in control if state.states.get(p) == 'succeeded']
            status = None
            if failed:
                status = 'blocked'
            elif not active:
                status = 'skipped'
            elif any(state.states.get(p) != 'succeeded' for p in mandatory):
                status = 'blocked'
            elif node.join in {'all', 'selected'} and len(successful) != len(control):
                status = 'skipped'
            elif node.join == 'any' and len(successful) != 1:
                status = 'failed'
            if status:
                state.states[node.id] = status
                records[key] = UnitExecution(key, 'node', status, iteration=iteration)
            else:
                started = monotonic()
                try:
                    self._charge(budget)
                    arguments = node.bind_input(value, MappingProxyType(state.outputs))
                    async with asyncio.timeout(node.timeout_seconds):
                        if isinstance(node.evaluate, RepeatBlock):
                            if set(arguments) != {'value'}:
                                raise ValueError('Repeat input must have only value')
                            block, data = node.evaluate, arguments['value']
                            for turn in range(block.max_iterations):
                                out = await self._execute(graph.repeat_bodies[node.id], data, budget, records, selected,
                                                          run_id, f'{key}/{turn}/', turn)
                                if block.until(out):
                                    break
                                data = block.next_input(data, out)
                            else:
                                raise RuntimeError('Repeat budget exhausted')
                        else:
                            out = await node.evaluate(**arguments)
                    state.outputs[node.id] = out
                    state.states[node.id] = 'succeeded'
                    records[key] = UnitExecution(key, 'node', 'succeeded', out,
                                                 duration=monotonic()-started, iteration=iteration)
                except Exception as error:
                    state.states[node.id] = 'failed'
                    records[key] = UnitExecution(key, 'node', 'failed', error_type=type(error).__name__,
                                                 duration=monotonic()-started, iteration=iteration)
                    logger.warning('node failed run=%s node=%s error=%s', run_id, key, type(error).__name__)
            for edge in graph.outgoing[node.id]:
                await self._edge(edge, value, state, budget, records, selected, run_id, prefix, iteration)
        if state.states[graph.output] != 'succeeded':
            raise RuntimeError('Final output blocked')
        return state.outputs[graph.output]

    async def _edge(self, edge, value, state, budget, records, selected, run_id, prefix, iteration):
        key = prefix + edge.id
        source_status = state.states[edge.source]
        status, output, error_type = 'skipped', None, None
        started = monotonic()
        if source_status in {'failed', 'blocked'}:
            status = 'blocked'
        elif source_status == 'succeeded':
            try:
                if edge.condition(state.outputs[edge.source]):
                    state.selected.add((edge.source, edge.target))
                    selected.add((prefix + edge.source, prefix + edge.target))
                    if edge.collect is not None:
                        self._charge(budget)
                        arguments = edge.collect.bind_input(value, MappingProxyType(state.outputs))
                        async with asyncio.timeout(edge.timeout_seconds):
                            output = await edge.collect.action(**arguments)
                    status = 'succeeded'
                    state.outputs[edge.id] = output
            except Exception as error:
                status, error_type = 'failed', type(error).__name__
                logger.warning('edge failed run=%s edge=%s error=%s', run_id, key, error_type)
        state.states[edge.id] = status
        records[key] = UnitExecution(key, 'edge', status, output, error_type,
                                     monotonic()-started, iteration)
