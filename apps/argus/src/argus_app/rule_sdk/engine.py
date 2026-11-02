"""Restricted Python rules run only in an isolated, resource-limited worker.

This is a constrained single-operator execution environment, not a hardened
multi-tenant sandbox. Never execute rule source in this process.
"""
from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import sys


class RuleError(ValueError):
    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__((message or code)[:512])


SAFE_FUNCTIONS = {'len', 'str', 'int', 'float', 'bool', 'any', 'all', 'min', 'max', 'sum', 'sorted', 'range', 'abs', 'capability'}
SAFE_METHODS = {'get', 'lower', 'upper', 'strip', 'startswith', 'endswith', 'count', 'split', 'items', 'keys', 'values'}
SAFE_NODES = (ast.Module, ast.FunctionDef, ast.arguments, ast.arg, ast.Return,
    ast.Assign, ast.AugAssign, ast.Name, ast.Load, ast.Store, ast.Constant,
    ast.Dict, ast.List, ast.Tuple, ast.Subscript, ast.Slice, ast.Attribute,
    ast.Call, ast.keyword, ast.If, ast.IfExp, ast.Compare, ast.BoolOp,
    ast.UnaryOp, ast.BinOp, ast.And, ast.Or, ast.Not, ast.Add, ast.Sub,
    ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn,
    ast.Is, ast.IsNot, ast.For, ast.While, ast.Break, ast.Continue, ast.Pass,
    ast.Expr, ast.ListComp, ast.DictComp, ast.GeneratorExp, ast.comprehension)


def lint(source: str) -> None:
    if not isinstance(source, str) or len(source.encode()) > 32768:
        raise RuleError('rule_invalid', 'rule source exceeds 32 KiB')
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise RuleError('rule_invalid', str(exc)) from None
    if len(tree.body) != 1 or not isinstance(tree.body[0], ast.FunctionDef):
        raise RuleError('rule_invalid', 'define exactly one decide(event, channel) function')
    fn = tree.body[0]
    if fn.name != 'decide' or fn.decorator_list or fn.returns or fn.type_params:
        raise RuleError('rule_invalid', 'invalid decide declaration')
    if ([a.arg for a in fn.args.args] != ['event', 'channel'] or fn.args.defaults
            or fn.args.vararg or fn.args.kwarg or fn.args.kwonlyargs or fn.args.posonlyargs):
        raise RuleError('rule_invalid', 'decide accepts event and channel only')
    nodes = list(ast.walk(tree))
    if len(nodes) > 4000:
        raise RuleError('rule_invalid', 'rule AST exceeds limit')
    for node in nodes:
        if not isinstance(node, SAFE_NODES):
            raise RuleError('rule_invalid', f'forbidden syntax: {type(node).__name__}')
        if isinstance(node, ast.FunctionDef) and node is not fn:
            raise RuleError('rule_invalid', 'nested functions forbidden')
        if isinstance(node, (ast.Name, ast.arg)):
            name = node.id if isinstance(node, ast.Name) else node.arg
            if name.startswith('_') or (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and name in SAFE_FUNCTIONS):
                raise RuleError('rule_invalid', 'private/reserved name forbidden')
        if isinstance(node, ast.arg) and node.annotation is not None:
            raise RuleError('rule_invalid', 'annotations forbidden')
        if isinstance(node, ast.Attribute) and (node.attr.startswith('_') or node.attr not in SAFE_METHODS or not isinstance(node.ctx, ast.Load)):
            raise RuleError('rule_invalid', 'attribute access forbidden')
        if isinstance(node, ast.Call):
            if not ((isinstance(node.func, ast.Name) and node.func.id in SAFE_FUNCTIONS)
                    or (isinstance(node.func, ast.Attribute) and node.func.attr in SAFE_METHODS)):
                raise RuleError('rule_invalid', 'function call forbidden')
    try:
        compile(tree, '<argus-rule>', 'exec')  # validation only; never execute here
    except (SyntaxError, ValueError, RecursionError) as exc:
        raise RuleError('rule_invalid', str(exc)) from None


class RuleEngine:
    def __init__(self, timeout: float = 2.0):
        if not 0 < timeout <= 30:
            raise ValueError('timeout must be in (0, 30]')
        self.timeout = timeout

    async def run(self, source: str, event: dict, channel: dict, call) -> dict:
        lint(source)
        public_channel = {key: value for key, value in channel.items() if key != 'runtime'}
        public_event = {key: value for key, value in event.items() if key != 'detail'}
        initial = (json.dumps({'source': source, 'event': public_event, 'channel': public_channel}, ensure_ascii=False) + '\n').encode()
        if len(initial) > 2 * 1024 * 1024:
            raise RuleError('rule_limit', 'rule input exceeds 2 MiB')
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-I', str(Path(__file__).with_name('worker.py')),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=131072,
        )
        try:
            async with asyncio.timeout(self.timeout):
                proc.stdin.write(initial)
                await proc.stdin.drain()
                call_count, semantic_count = 0, 0
                for _ in range(4):
                    line = await proc.stdout.readline()
                    if not line or len(line) > 131072:
                        raise RuleError('rule_failed', 'worker exited or exceeded output limit')
                    msg = json.loads(line)
                    if msg.get('type') == 'result':
                        if not isinstance(msg.get('value'), dict):
                            raise RuleError('rule_invalid', 'rule must return decision object')
                        return msg['value']
                    if msg.get('type') == 'error':
                        raise RuleError('rule_failed', str(msg.get('message', 'worker failed')))
                    if msg.get('type') != 'call' or not isinstance(msg.get('args'), dict):
                        raise RuleError('rule_failed', 'invalid worker protocol')
                    call_count += 1
                    semantic_count += int(msg.get('name') == 'semantic')
                    if call_count > 3 or semantic_count > 1:
                        raise RuleError('rule_limit', 'at most 3 capability calls and 1 semantic call per evaluation')
                    result = await call(msg['name'], msg['args'])
                    reply = json.dumps({'value': result})
                    if len(reply.encode()) > 131000:
                        raise RuleError('rule_limit', 'capability response too large')
                    proc.stdin.write((reply + '\n').encode())
                    await proc.stdin.drain()
                raise RuleError('rule_limit', 'too many capability calls')
        except TimeoutError:
            raise RuleError('rule_timeout') from None
        except (ValueError, BrokenPipeError, ConnectionError) as exc:
            if isinstance(exc, RuleError):
                raise
            raise RuleError('rule_failed', str(exc)) from None
        finally:
            if proc.returncode is None:
                proc.kill()
            await proc.wait()

    async def evaluate(self, source, event, channel, call):
        from argus_app.contracts.models import Decision
        result = await self.run(source, event.model_dump(mode='json'), channel.model_dump(mode='json'), call)
        try:
            return Decision.model_validate(result)
        except ValueError as exc:
            raise RuleError('rule_invalid', str(exc)) from None
