"""内置 Agent 工具。"""

from apps.agent.src.agent_orchestration.tools.builtin.bash_tool import BashTool
from apps.agent.src.agent_orchestration.tools.builtin.insert_tool import InsertTool
from apps.agent.src.agent_orchestration.tools.builtin.read_tool import ReadTool
from apps.agent.src.agent_orchestration.tools.builtin.write_tool import WriteTool


def create_builtin_tools() -> list[ReadTool | WriteTool | InsertTool | BashTool]:
    return [ReadTool(), WriteTool(), InsertTool(), BashTool()]


__all__ = [
    "BashTool",
    "InsertTool",
    "ReadTool",
    "WriteTool",
    "create_builtin_tools",
]
