"""Static policy plus fail-closed isolated execution; not a host-code evaluator."""
from __future__ import annotations

import asyncio
import json

from .checks import check_source


class RuleError(ValueError):
    def __init__(self, code: str, message: str = ''):
        self.code = code
        super().__init__((message or code)[:512])


def lint(source: str) -> None:
    report = check_source(source)
    if not report.passed:
        raise RuleError('rule_invalid', '; '.join(
            f'{x.line}:{x.column} {x.code}: {x.message}' for x in report.diagnostics))


class RuleEngine:
    def __init__(self, timeout: float = 2.0, *, launcher=None):
        if not 0 < timeout <= 30:
            raise ValueError('timeout must be in (0, 30]')
        from .isolation import IsolationLauncher
        self.timeout = timeout
        self.launcher = launcher or IsolationLauncher()

    async def run(self, source: str, event: dict, channel: dict, call) -> dict:
        lint(source)
        public_channel = {key: value for key, value in channel.items() if key != 'runtime'}
        public_event = {key: value for key, value in event.items() if key != 'detail'}
        worker = None
        try:
            async with asyncio.timeout(self.timeout):
                worker = await self.launcher.start(source, public_event, public_channel)
                call_count, semantic_count = 0, 0
                for _ in range(4):
                    msg = await worker.receive()
                    if not isinstance(msg, dict):
                        raise RuleError('rule_failed', 'Invalid worker protocol')
                    if msg.get('type') == 'result':
                        if not isinstance(msg.get('value'), dict):
                            raise RuleError('rule_invalid', 'Rule must return a decision object')
                        return msg['value']
                    if msg.get('type') == 'error':
                        raise RuleError('rule_failed', str(msg.get('message', 'worker failed')))
                    name = msg.get('name')
                    if msg.get('type') != 'call' or not isinstance(msg.get('args'), dict):
                        raise RuleError('rule_failed', 'Invalid worker protocol')
                    if name not in {'history', 'mechanical', 'semantic', 'memory', 'relationships', 'knowledge'}:
                        raise RuleError('rule_invalid', 'Only read-only decision capabilities are allowed')
                    call_count += 1
                    semantic_count += int(name == 'semantic')
                    if call_count > 3 or semantic_count > 1:
                        raise RuleError('rule_limit', 'At most 3 capability calls and 1 semantic call per evaluation')
                    async with asyncio.timeout(min(5, self.timeout)):
                        result = await call(name, msg['args'])
                    await worker.send({'value': result})
                raise RuleError('rule_limit', 'Too many capability calls')
        except TimeoutError:
            raise RuleError('rule_timeout') from None
        except (ValueError, OSError, ConnectionError) as exc:
            if isinstance(exc, RuleError):
                raise
            raise RuleError('rule_failed', str(exc)) from None
        finally:
            if worker is not None:
                await asyncio.shield(worker.terminate_wait())

    async def evaluate(self, source, event, channel, call):
        from argus_app.contracts.models import Decision
        result = await self.run(source, event.model_dump(mode='json'), channel.model_dump(mode='json'), call)
        try:
            return Decision.model_validate(result)
        except ValueError as exc:
            raise RuleError('rule_invalid', str(exc)) from None
