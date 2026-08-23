"""执行本地 Bash 命令。"""

import asyncio
import subprocess
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition
from apps.agent.src.model_provider.types import Message


class BashTool(BaseTool):
    TERMINATE_GRACE_SECONDS = 1.0

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
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        command, workdir, timeout = self._validate_arguments(arguments)

        try:
            completed = subprocess.run(
                ["bash", "-lc", command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            return ToolExecutionResult(success=False, error=str(error))

        return self._result(
            completed.returncode,
            completed.stdout,
            completed.stderr,
        )

    async def ainvoke(
        self,
        arguments: dict[str, Any],
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        step: int | None = None,
        task_messages: tuple[Message, ...] = (),
    ) -> ToolExecutionResult:
        del task_id, run_id, step, task_messages
        command, workdir, timeout = self._validate_arguments(arguments)
        process = await asyncio.create_subprocess_exec(
            "bash",
            "-lc",
            command,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            if timeout is None:
                stdout, stderr = await process.communicate()
            else:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
        except asyncio.CancelledError:
            await asyncio.shield(self._terminate(process))
            raise
        except TimeoutError:
            await self._terminate(process)
            return ToolExecutionResult(
                success=False,
                error=f"Command timed out after {timeout:g} seconds",
            )

        return self._result(
            process.returncode or 0,
            stdout.decode(errors="replace"),
            stderr.decode(errors="replace"),
        )

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
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=cls.TERMINATE_GRACE_SECONDS,
            )
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    def _result(
        return_code: int,
        stdout: str,
        stderr: str,
    ) -> ToolExecutionResult:
        output = {
            "exit_code": return_code,
            "stdout": stdout,
            "stderr": stderr,
        }
        return ToolExecutionResult(
            success=return_code == 0,
            output=output,
            error=None if return_code == 0 else stderr,
        )

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return arguments.get("parallel", False) is True
