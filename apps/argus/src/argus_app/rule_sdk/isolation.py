"""Linux namespaces and an empty read-only root; never fall back to host execution."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile


@dataclass(frozen=True)
class IsolationStatus:
    available: bool
    reason: str = ''


class IsolatedWorker:
    def __init__(self, process, directory):
        self.process, self.directory = process, directory

    async def receive(self):
        from .engine import RuleError
        line = await self.process.stdout.readline()
        if not line or len(line) > 131072:
            raise RuleError('rule_failed', 'Isolated worker exited or exceeded output limit')
        return json.loads(line)

    async def send(self, value):
        from .engine import RuleError
        data = (json.dumps(value, allow_nan=False) + '\n').encode()
        if len(data) > 131000:
            raise RuleError('rule_limit', 'Capability response too large')
        self.process.stdin.write(data)
        await self.process.stdin.drain()

    async def terminate_wait(self):
        try:
            if self.process.returncode is None:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            await self.process.wait()
        finally:
            self.directory.cleanup()


class IsolationLauncher:
    def __init__(self, *, executable=None, python=None, worker_path=None):
        self.executable = executable or shutil.which('unshare') or '/nonexistent/unshare'
        self.python = python or '/usr/bin/python3'
        self.worker_path = worker_path or Path(__file__).with_name('worker.py')
        self._status = None

    def _command(self, root, *args):
        return [self.executable, '--user', '--map-root-user', '--mount', '--pid', '--fork',
                '--kill-child=KILL', '--net', '--ipc', '--uts', '--', self.python, '-I',
                str(self.worker_path), str(root), *args]

    def probe(self):
        if self._status is None:
            try:
                with tempfile.TemporaryDirectory(prefix='argus-probe-') as root:
                    result = subprocess.run(self._command(root, '--probe'), env={'LANG': 'C.UTF-8'},
                                            capture_output=True, timeout=5, start_new_session=True)
                    self._status = IsolationStatus(result.returncode == 0 and result.stdout.strip() == b'ready',
                                                   '' if result.returncode == 0 else 'namespace/seccomp probe failed')
            except (OSError, subprocess.TimeoutExpired):
                self._status = IsolationStatus(False, 'Linux namespaces/seccomp unavailable')
        return self._status

    async def start(self, source: str, event: dict, channel: dict):
        from .engine import RuleError
        if not (await asyncio.to_thread(self.probe)).available:
            raise RuleError('isolation_unavailable')
        initial = (json.dumps({'source': source, 'event': event, 'channel': channel},
                              ensure_ascii=False, allow_nan=False) + '\n').encode()
        if len(initial) > 2 * 1024 * 1024:
            raise RuleError('rule_limit', 'Rule input exceeds 2 MiB')
        directory = tempfile.TemporaryDirectory(prefix='argus-rule-')
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(directory.name), env={'LANG': 'C.UTF-8'},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL, limit=131072, start_new_session=True)
        except BaseException:
            directory.cleanup()
            raise
        worker = IsolatedWorker(process, directory)
        try:
            process.stdin.write(initial)
            await process.stdin.drain()
            return worker
        except BaseException:
            await worker.terminate_wait()
            raise
