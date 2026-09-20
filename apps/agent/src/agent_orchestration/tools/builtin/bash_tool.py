"""Execute local Bash commands with bounded time and output capture."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import os
import selectors
import signal
import subprocess
import time
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import Message, ToolDefinition


@dataclass
class _BoundedCapture:
    maximum: int
    stdout: bytearray = field(default_factory=bytearray)
    stderr: bytearray = field(default_factory=bytearray)
    total: int = 0
    exceeded: bool = False

    def add(self, stream: str, chunk: bytes) -> None:
        if self.exceeded:
            return
        remaining = self.maximum - self.total
        target = self.stdout if stream == "stdout" else self.stderr
        target.extend(chunk[:remaining])
        self.total += min(len(chunk), remaining)
        if len(chunk) > remaining:
            self.exceeded = True


class BashTool(BaseTool):
    TERMINATE_GRACE_SECONDS = 1.0
    DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024

    def __init__(self, *, max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> None:
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.max_output_bytes = max_output_bytes

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="bash",
            description="使用 Bash 执行本地命令并返回退出码和输出",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "需要执行的 Bash 命令",
                    },
                    "workdir": {
                        "type": "string",
                        "description": "可选工作目录",
                    },
                    "timeout": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "可选超时秒数",
                    },
                    "parallel": {
                        "type": "boolean",
                        "description": "确认命令与相邻调用无资源冲突时设为 true",
                        "default": False,
                    },
                },
                "required": ["command"],
                "additionalProperties": False,
            },
        )

    def invoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
        timeout_seconds: float | None = None,
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        command, workdir, timeout = self._validate_arguments(arguments)
        timeout = _minimum_timeout(timeout, timeout_seconds)
        try:
            process = subprocess.Popen(
                ["bash", "-lc", command],
                cwd=workdir,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            capture, timed_out = self._collect_sync(process, timeout)
        except OSError as error:
            return ToolExecutionResult(success=False, error=str(error))

        return self._captured_result(process.returncode, capture, timeout, timed_out)

    async def ainvoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
        timeout_seconds: float | None = None,
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        command, workdir, timeout = self._validate_arguments(arguments)
        timeout = _minimum_timeout(timeout, timeout_seconds)
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        capture = _BoundedCapture(self.max_output_bytes)
        limit_reached = asyncio.Event()
        readers = [
            asyncio.create_task(
                self._read_stream(process.stdout, "stdout", capture, limit_reached)
            ),
            asyncio.create_task(
                self._read_stream(process.stderr, "stderr", capture, limit_reached)
            ),
        ]
        wait_task = asyncio.create_task(process.wait())
        limit_task = asyncio.create_task(limit_reached.wait())
        timed_out = False
        try:
            done, _ = await asyncio.wait(
                {wait_task, limit_task},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            timed_out = not done
            if timed_out or limit_task in done:
                await self._terminate(process)
            else:
                await wait_task
                if _process_group_exists(process.pid):
                    await self._terminate(process)
            await asyncio.gather(*readers)
        except asyncio.CancelledError:
            await asyncio.shield(self._terminate(process))
            raise
        except BaseException:
            await asyncio.shield(self._terminate(process))
            raise
        finally:
            for task in (wait_task, limit_task, *readers):
                if not task.done():
                    task.cancel()
            await asyncio.gather(
                wait_task, limit_task, *readers, return_exceptions=True
            )

        return self._captured_result(process.returncode, capture, timeout, timed_out)

    def _collect_sync(
        self, process: subprocess.Popen[bytes], timeout: float | None
    ) -> tuple[_BoundedCapture, bool]:
        if process.stdout is None or process.stderr is None:
            raise RuntimeError("Bash output pipes were not created")
        capture = _BoundedCapture(self.max_output_bytes)
        selector = selectors.DefaultSelector()
        streams = {process.stdout: "stdout", process.stderr: "stderr"}
        for stream, name in streams.items():
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        deadline = time.monotonic() + timeout if timeout is not None else None
        timed_out = False
        terminated = False
        try:
            while selector.get_map():
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                if remaining is not None and remaining <= 0:
                    timed_out = True
                    self._terminate_sync(process)
                    terminated = True
                    break
                events = selector.select(
                    0.1 if remaining is None else max(0, min(0.1, remaining))
                )
                for key, _ in events:
                    chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    capture.add(key.data, chunk)
                    if capture.exceeded:
                        self._terminate_sync(process)
                        terminated = True
                        break
                if terminated:
                    break
                if process.poll() is not None and _process_group_exists(
                    process.pid
                ):
                    self._terminate_sync(process)
                    terminated = True
                    break
            if not terminated:
                remaining = (
                    None if deadline is None else deadline - time.monotonic()
                )
                try:
                    process.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    self._terminate_sync(process)
                    terminated = True
                if _process_group_exists(process.pid):
                    self._terminate_sync(process)
        except BaseException:
            self._terminate_sync(process)
            raise
        finally:
            selector.close()
            process.stdout.close()
            process.stderr.close()
        return capture, timed_out

    @staticmethod
    async def _read_stream(stream, name, capture, limit_reached) -> None:
        if stream is None:
            return
        while True:
            chunk = await stream.read(64 * 1024)
            if not chunk:
                return
            capture.add(name, chunk)
            if capture.exceeded:
                limit_reached.set()

    @staticmethod
    def _validate_arguments(
        arguments: dict[str, Any],
    ) -> tuple[str, str | None, float | None]:
        command = arguments.get("command")
        workdir = arguments.get("workdir")
        timeout = arguments.get("timeout")
        parallel = arguments.get("parallel", False)
        if not isinstance(command, str) or not command:
            raise ValueError("command must be a non-empty string")
        if workdir is not None and not isinstance(workdir, str):
            raise ValueError("workdir must be a string")
        if timeout is not None and (
            not isinstance(timeout, (int, float))
            or isinstance(timeout, bool)
            or timeout <= 0
        ):
            raise ValueError("timeout must be a positive number")
        if not isinstance(parallel, bool):
            raise ValueError("parallel must be a boolean")
        return command, workdir, timeout

    @classmethod
    async def _terminate(cls, process: asyncio.subprocess.Process) -> None:
        pid = getattr(process, "pid", None)
        if process.returncode is not None and not _process_group_exists(pid):
            return
        started_at = time.monotonic()
        _signal_process_group(pid, signal.SIGTERM, process.terminate)
        try:
            await asyncio.wait_for(
                process.wait(), timeout=cls.TERMINATE_GRACE_SECONDS
            )
        except TimeoutError:
            pass
        remaining = cls.TERMINATE_GRACE_SECONDS - (time.monotonic() - started_at)
        await _await_process_group_exit(pid, remaining)
        if process.returncode is None or _process_group_exists(pid):
            _signal_process_group(pid, signal.SIGKILL, process.kill)
        if process.returncode is None:
            await process.wait()

    @classmethod
    def _terminate_sync(cls, process: subprocess.Popen[bytes]) -> None:
        pid = getattr(process, "pid", None)
        if process.poll() is not None and not _process_group_exists(pid):
            return
        started_at = time.monotonic()
        _signal_process_group(pid, signal.SIGTERM, process.terminate)
        try:
            process.wait(timeout=cls.TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
        remaining = cls.TERMINATE_GRACE_SECONDS - (time.monotonic() - started_at)
        _wait_for_process_group_exit(pid, remaining)
        if process.poll() is None or _process_group_exists(pid):
            _signal_process_group(pid, signal.SIGKILL, process.kill)
        if process.poll() is None:
            process.wait()

    def _captured_result(
        self,
        return_code: int | None,
        capture: _BoundedCapture,
        timeout: float | None,
        timed_out: bool,
    ) -> ToolExecutionResult:
        stdout = bytes(capture.stdout).decode(errors="replace")
        stderr = bytes(capture.stderr).decode(errors="replace")
        output = {
            "exit_code": return_code if return_code is not None else -1,
            "stdout": stdout,
            "stderr": stderr,
        }
        if capture.exceeded:
            return ToolExecutionResult(
                False,
                output=output,
                error=(
                    "Command output exceeded the "
                    f"{self.max_output_bytes}-byte capture limit and was terminated"
                ),
                metadata={
                    "disposition": "output_limit_exceeded",
                    "output_capture_incomplete": True,
                    "captured_output_bytes": capture.total,
                },
            )
        if timed_out:
            return ToolExecutionResult(
                False,
                output=output,
                error=f"Command timed out after {timeout:g} seconds",
                metadata={"disposition": "timed_out"},
            )
        return ToolExecutionResult(
            success=return_code == 0,
            output=output,
            error=None if return_code == 0 else stderr,
        )

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return arguments.get("parallel", False) is True


def _signal_process_group(
    pid: int | None, sig: signal.Signals, fallback
) -> None:
    if pid is None:
        fallback()
        return
    try:
        os.killpg(pid, sig)
    except (AttributeError, ProcessLookupError, PermissionError):
        try:
            fallback()
        except ProcessLookupError:
            pass


def _process_group_exists(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.killpg(pid, 0)
    except (AttributeError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


async def _await_process_group_exit(pid: int | None, timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while _process_group_exists(pid) and time.monotonic() < deadline:
        await asyncio.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _wait_for_process_group_exit(pid: int | None, timeout: float) -> None:
    deadline = time.monotonic() + max(0.0, timeout)
    while _process_group_exists(pid) and time.monotonic() < deadline:
        time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))


def _minimum_timeout(
    tool_timeout: float | None, framework_timeout: float | None
) -> float | None:
    values = [
        float(value)
        for value in (tool_timeout, framework_timeout)
        if value is not None
    ]
    return min(values) if values else None
