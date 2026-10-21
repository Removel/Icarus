from agent_app.plugins.prompt.base_prompt import DEFAULT_SYSTEM_PROMPT
from icarus.core.tools.result_budget import conservative_token_count


def test_base_prompt_is_compact_and_does_not_own_plugin_policy():
    assert conservative_token_count(DEFAULT_SYSTEM_PROMPT) <= 600
    assert "长期记忆" not in DEFAULT_SYSTEM_PROMPT
    assert "memory_recall" not in DEFAULT_SYSTEM_PROMPT
    assert "Icarus Agent" in DEFAULT_SYSTEM_PROMPT
    assert "授权" in DEFAULT_SYSTEM_PROMPT
    assert "验证" in DEFAULT_SYSTEM_PROMPT
    assert "普通聊天默认简短、自然、直接" in DEFAULT_SYSTEM_PROMPT
