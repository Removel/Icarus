"""执行本地 Bash 命令。"""

import subprocess
from typing import Any

from apps.agent.src.agent_orchestration.tools.base_tool import BaseTool
from apps.agent.src.agent_orchestration.tools.types import ToolExecutionResult
from apps.agent.src.model_provider.types import ToolDefinition


class BashTool(BaseTool):
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

    def invoke(self, arguments: dict[str, Any]) -> ToolExecutionResult:
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

        output = {
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        return ToolExecutionResult(
            success=completed.returncode == 0,
            output=output,
            error=None if completed.returncode == 0 else completed.stderr,
        )

    def can_run_parallel(self, arguments: dict[str, Any]) -> bool:
        return arguments.get("parallel", False) is True
