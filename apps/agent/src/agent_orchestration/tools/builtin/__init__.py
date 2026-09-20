"""内置 Agent 工具。"""

from apps.agent.src.agent_orchestration.tools.builtin.bash_tool import BashTool
from apps.agent.src.agent_orchestration.tools.builtin.insert_tool import InsertTool
from apps.agent.src.agent_orchestration.tools.builtin.read_tool import ReadTool
from apps.agent.src.agent_orchestration.tools.builtin.write_tool import WriteTool


def create_builtin_tools(
    *,
    max_output_bytes: int = BashTool.DEFAULT_MAX_OUTPUT_BYTES,
    default_read_lines: int = 200,
    max_read_lines: int = 2000,
) -> list[ReadTool | WriteTool | InsertTool | BashTool]:
    return [
        ReadTool(
            default_limit=default_read_lines,
            maximum_limit=max_read_lines,
        ),
        WriteTool(),
        InsertTool(),
        BashTool(max_output_bytes=max_output_bytes),
    ]


__all__ = [
    "BashTool",
    "InsertTool",
    "ReadTool",
    "WriteTool",
    "create_builtin_tools",
]
