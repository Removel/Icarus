import pytest

from apps.tui.src.commands import parse_local_command


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/resume", "resume"),
        ("  /RESUME \n", "resume"),
        ("/clear", "clear"),
        ("/Clear", "clear"),
        ("/resume old", None),
        ("please /resume", None),
        ("/compact", None),
        ("", None),
    ],
)
def test_parse_local_command(text, expected):
    assert parse_local_command(text) == expected
