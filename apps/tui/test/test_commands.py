import pytest

from apps.tui.src.commands import (
    CommandDefinition,
    CommandRegistry,
    create_default_command_registry,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/resume", "resume"),
        ("  /RESUME \n", "resume"),
        ("/clear", "clear"),
        ("/Clear", "clear"),
        ("/resume old", "resume"),
        ("please /resume", None),
        ("/compact", None),
        ("", None),
    ],
)
def test_parse_local_command(text, expected):
    invocation = create_default_command_registry().parse(text)
    definition = (
        create_default_command_registry().resolve(invocation.name)
        if invocation is not None
        else None
    )
    assert (definition.name.removeprefix("/") if definition else None) == expected


def test_default_command_registry只注册当前三个命令():
    definitions = create_default_command_registry().definitions

    assert [definition.name for definition in definitions] == [
        "/clear",
        "/resume",
        "/exit",
    ]
    assert [definition.handler_name for definition in definitions] == [
        "clear_session",
        "resume_session",
        "exit_app",
    ]


def test_command_registry保留参数并大小写不敏感():
    registry = create_default_command_registry()
    invocation = registry.parse("  /RESUME old session  ")

    assert invocation is not None
    assert invocation.name == "/resume"
    assert invocation.arguments == "old session"
    assert registry.resolve(invocation.name).name == "/resume"


@pytest.mark.parametrize(
    "definition",
    [
        CommandDefinition("clear", "bad", "handler"),
        CommandDefinition("/", "bad", "handler"),
        CommandDefinition("/bad name", "bad", "handler"),
        CommandDefinition("/bad", "bad", ""),
    ],
)
def test_command_registry拒绝无效定义(definition):
    with pytest.raises(ValueError):
        CommandRegistry().register(definition)


def test_command_registry拒绝重复命令():
    registry = CommandRegistry()
    registry.register(CommandDefinition("/clear", "one", "first"))
    with pytest.raises(ValueError, match="already registered"):
        registry.register(CommandDefinition("/CLEAR", "two", "second"))
