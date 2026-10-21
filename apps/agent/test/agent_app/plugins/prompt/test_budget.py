import importlib

import pytest

from agent_app.contracts.prompt import PromptError, PromptSection


def module(name):
    try:
        return importlib.import_module(f"agent_app.plugins.prompt.{name}")
    except ModuleNotFoundError:
        pytest.fail(f"Prompt {name} capability is not implemented")


@pytest.mark.parametrize("content,accepted", [
    (None, True), ("", True), ("abcd", True), ("abcde", False),
    ("中" * 4, True), ("中" * 5, False), ("😀" * 4, False),
])
def test_instructions_budget_uses_characters_and_utf8_bytes(content, accepted):
    validate = module("composer").validate_instructions
    if accepted:
        validate(content, max_tokens=4)
    else:
        with pytest.raises(PromptError) as caught:
            validate(content, max_tokens=4)
        assert caught.value.code == "prompt_too_large"
        assert content not in str(caught.value)


@pytest.mark.parametrize("content", [123, [], "\ud800"])
def test_invalid_instructions_return_safe_error(content):
    with pytest.raises(PromptError) as caught:
        module("composer").validate_instructions(content, max_tokens=4)
    assert caught.value.code == "prompt_invalid"


def test_composition_orders_layers_and_preserves_original_text():
    compose = module("composer").compose_prompt
    result = compose({
        "external": (PromptSection("caller", "external", "\n caller \n"),),
        "persona": (PromptSection("z", "persona", "PERSONA Z"), PromptSection("a", "persona", "PERSONA A")),
        "base": (PromptSection("base", "base", "BASE"),),
    }, layers=("base", "workspace", "persona", "external"),
       required_sources=frozenset({"base", "external"}), max_tokens=1000, max_section_tokens=100)
    assert result.index("BASE") < result.index("PERSONA A") < result.index("PERSONA Z") < result.index("\n caller \n")
    assert "\n caller \n" in result


def test_optional_section_is_dropped_whole_and_external_cannot_be_shed():
    compose = module("composer").compose_prompt
    kwargs = dict(layers=("base", "workspace", "persona", "external"), required_sources=frozenset({"base", "external"}), max_tokens=100, max_section_tokens=40)
    result = compose({"base": (PromptSection("b", "base", "BASE"),), "workspace": (PromptSection("w", "workspace", "X" * 41),)}, **kwargs)
    assert "BASE" in result
    assert "X" not in result
    with pytest.raises(PromptError) as caught:
        compose({"external": (PromptSection("caller", "external", "PRIVATE" * 10),)}, **kwargs)
    assert caught.value.code == "prompt_too_large"


def test_rendering_overhead_counts_toward_total_budget():
    compose = module("composer").compose_prompt
    with pytest.raises(PromptError):
        compose({"base": (PromptSection("base", "base", "1234"),)}, layers=("base",), required_sources=frozenset({"base"}), max_tokens=4, max_section_tokens=4)


def test_optional_oversized_section_does_not_discard_valid_sibling():
    result = module("composer").compose_prompt({"source": (
        PromptSection("a", "persona", "VALID"),
        PromptSection("b", "persona", "X" * 41),
    )}, layers=("persona",), required_sources=frozenset(), max_tokens=100, max_section_tokens=40)
    assert "VALID" in result
    assert "X" not in result


def test_required_duplicate_section_is_invalid():
    compose = module("composer").compose_prompt
    with pytest.raises(PromptError) as caught:
        compose({"persona": (PromptSection("x", "persona", "a"), PromptSection("x", "persona", "b"))}, layers=("persona",), required_sources=frozenset({"persona"}), max_tokens=100, max_section_tokens=20)
    assert caught.value.code == "prompt_invalid"


def test_prompt_configuration_rejects_inconsistent_or_unbounded_budgets():
    config = module("config").PluginConfig
    assert config().max_prompt_tokens == 12288
    assert config().max_section_tokens == 8192
    for values in ({"max_prompt_tokens": 1}, {"contributor_timeout_seconds": float("inf")}, {"required_contributors": ["p", "p"]}):
        with pytest.raises(ValueError):
            config(**values)
