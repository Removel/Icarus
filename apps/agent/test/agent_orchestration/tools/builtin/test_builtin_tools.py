from apps.agent.src.agent_orchestration.tools.builtin import (
    BashTool,
    InsertTool,
    ReadTool,
    WriteTool,
    create_builtin_tools,
)


def test_builtin_tools_默认工具定义完整():
    tools = create_builtin_tools()

    assert [tool.definition.name for tool in tools] == [
        "read",
        "write",
        "insert",
        "bash",
    ]
    assert ReadTool().can_run_parallel({"path": "a.txt"}) is True
    assert WriteTool().can_run_parallel({"path": "a.txt", "content": ""}) is False
    assert InsertTool().can_run_parallel(
        {"path": "a.txt", "line": 1, "content": ""}
    ) is False
    assert BashTool().can_run_parallel({"command": "git status"}) is False
    assert BashTool().can_run_parallel(
        {"command": "git status", "parallel": True}
    ) is True


def test_read_write_insert_完成文件读写与插入(tmp_path):
    path = tmp_path / "nested" / "demo.txt"

    written = WriteTool().invoke({"path": str(path), "content": "line-1\nline-3\n"})
    inserted = InsertTool().invoke(
        {
            "path": str(path),
            "line": 2,
            "content": "line-2\n",
        }
    )
    read = ReadTool().invoke({"path": str(path)})

    assert written.success is True
    assert inserted.success is True
    assert read.output["content"] == "line-1\nline-2\nline-3\n"


def test_insert_行号越界返回统一失败结果(tmp_path):
    path = tmp_path / "demo.txt"
    path.write_text("only\n", encoding="utf-8")

    result = InsertTool().invoke(
        {"path": str(path), "line": 3, "content": "missing\n"}
    )

    assert result.success is False
    assert result.error == "line out of range: 3"


def test_read_文件不存在返回统一失败结果(tmp_path):
    result = ReadTool().invoke({"path": str(tmp_path / "missing.txt")})

    assert result.success is False
    assert result.error


def test_bash_返回退出码标准输出和标准错误(tmp_path):
    success = BashTool().invoke(
        {
            "command": "printf hello",
            "workdir": str(tmp_path),
        }
    )
    failed = BashTool().invoke(
        {
            "command": "printf failure >&2; exit 7",
            "workdir": str(tmp_path),
        }
    )

    assert success.success is True
    assert success.output == {
        "exit_code": 0,
        "stdout": "hello",
        "stderr": "",
    }
    assert failed.success is False
    assert failed.output["exit_code"] == 7
    assert failed.error == "failure"
